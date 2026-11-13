"""Hybrid retrieval: vector + keyword search under one permission filter, RRF merge, rerank."""

from functools import cached_property
from typing import Any
from uuid import UUID

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from liverag_core.embedder import Embedder
from liverag_core.reranker import Reranker

CANDIDATES = 30  # how many results each search contributes before merging
RRF_K = 60  # standard constant from the original Reciprocal Rank Fusion paper
MODES = ("rerank", "hybrid", "vector", "keyword")

# Both searches use the SAME permission filter, inside the SQL (rule 1).
PERMISSION_FILTER = "(is_public OR allowed_group_ids && %(groups)s::int[])"

# The inner query may come back slightly out of order (relaxed_order), so the outer one re-sorts.
VECTOR_SQL = f"""
    SELECT * FROM (
        SELECT id, source_table, source_id, text, metadata, updated_at,
               embedding <=> %(vector)s AS score
        FROM chunks_v1
        WHERE {PERMISSION_FILTER}
        ORDER BY embedding <=> %(vector)s
        LIMIT %(limit)s
    ) AS hits
    ORDER BY score
"""

# plainto_tsquery joins the words with AND ('alpin' & '2' & 'tent'); a question rarely has every
# word in one chunk, so we switch to OR and let ts_rank_cd reward chunks that match more words.
KEYWORD_SQL = f"""
    WITH q AS (
        SELECT replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | ')::tsquery AS query
    )
    SELECT id, source_table, source_id, text, metadata, updated_at,
           ts_rank_cd(tsv, q.query) AS score
    FROM chunks_v1, q
    WHERE tsv @@ q.query AND {PERMISSION_FILTER}
    ORDER BY score DESC
    LIMIT %(limit)s
"""


def user_groups(conn: psycopg.Connection, user_id: UUID) -> list[int]:
    """The user's groups, from the index DB's replica (kept in sync by CDC in Stage 2)."""
    rows = conn.execute(
        "SELECT group_id FROM user_groups_replica WHERE user_id = %s ORDER BY group_id",
        (user_id,),
    ).fetchall()
    return [row[0] for row in rows]


def rrf_merge(ranked_lists: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion: score = sum of 1 / (RRF_K + rank) over every list a chunk is in.

    Only ranks are used, never raw scores, so a cosine distance and a text rank (which are on
    completely different scales) can be combined fairly.
    """
    merged: dict[Any, dict[str, Any]] = {}
    for name, hits in ranked_lists.items():
        for rank, hit in enumerate(hits, start=1):
            entry = merged.setdefault(hit["id"], {**hit, "rrf": 0.0, "ranks": {}})
            entry["rrf"] += 1 / (RRF_K + rank)
            entry["ranks"][name] = rank
    return sorted(merged.values(), key=lambda h: h["rrf"], reverse=True)


class Retriever:
    """Holds the models, so a long-running service loads them once and reuses them."""

    # cached_property: each model loads the first time it is needed, then stays in memory.
    @cached_property
    def embedder(self) -> Embedder:
        return Embedder()

    @cached_property
    def reranker(self) -> Reranker:
        return Reranker()

    def search(self, conn: psycopg.Connection, query: str, groups: list[int],
               k: int = 6, mode: str = "rerank") -> list[dict[str, Any]]:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        params: dict[str, Any] = {"text": query, "groups": groups, "limit": CANDIDATES}
        ranked: dict[str, list[dict[str, Any]]] = {}
        register_vector(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            if mode != "keyword":
                params["vector"] = self.embedder.embed_query(query)
                # With a filter, HNSW may run out of candidates before it finds enough allowed
                # rows. Iterative scan (pgvector 0.8+) keeps walking the graph until it has enough.
                cur.execute("SET hnsw.iterative_scan = relaxed_order")
                ranked["vec"] = cur.execute(VECTOR_SQL, params).fetchall()
            if mode != "vector":
                ranked["kw"] = cur.execute(KEYWORD_SQL, params).fetchall()
        merged = rrf_merge(ranked)
        if mode == "rerank":
            merged = self.rerank(query, merged[:CANDIDATES])
        return merged[:k]

    def rerank(self, query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Re-order candidates by a cross-encoder that reads the query and each chunk together."""
        scores = self.reranker.score(query, [hit["text"] for hit in hits])
        for position, (hit, score) in enumerate(zip(hits, scores), start=1):
            hit["ranks"]["rrf"] = position  # where RRF had placed it, to compare
            hit["rerank"] = score
        return sorted(hits, key=lambda h: h["rerank"], reverse=True)