"""Command-line hybrid search over chunks_v1, filtered by what the user is allowed to read.

Run from services/indexer:
    uv run python -m indexer.search "How much is the Alpine 2 tent?"
    uv run python -m indexer.search "discount on a damaged tent" --as agent@northwind.test
    uv run python -m indexer.search "TNT-0025" --mode vector
"""

import argparse
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from indexer.batch import dsn
from indexer.embedder import Embedder

GROUP_NAMES = {1: "support", 2: "sales", 3: "managers", 4: "hr", 5: "admins"}
CANDIDATES = 30  # how many results each search contributes before merging
RRF_K = 60  # standard constant from the original Reciprocal Rank Fusion paper

# Both searches use the SAME permission filter, inside the SQL (rule 1).
PERMISSION_FILTER = "(is_public OR allowed_group_ids && %(groups)s::int[])"

# The inner query may come back slightly out of order (relaxed_order), so the outer one re-sorts.
VECTOR_SQL = f"""
    SELECT * FROM (
        SELECT id, source_table, text, metadata, embedding <=> %(vector)s AS score
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
    SELECT id, source_table, text, metadata, ts_rank_cd(tsv, q.query) AS score
    FROM chunks_v1, q
    WHERE tsv @@ q.query AND {PERMISSION_FILTER}
    ORDER BY score DESC
    LIMIT %(limit)s
"""


def groups_for(email: str | None) -> list[int]:
    """Anonymous visitors see public data only; a user also sees their groups' documents."""
    if email is None:
        return []
    with psycopg.connect(dsn("SOURCE_DB")) as source:
        row = source.execute(
            "SELECT id FROM users WHERE email = %s AND is_active", (email,)
        ).fetchone()
    if row is None:
        raise SystemExit(f"No active user with email {email}")
    # Groups come from the index DB's replica, the same place the API will read them at query time.
    with psycopg.connect(dsn("INDEX_DB")) as index:
        rows = index.execute(
            "SELECT group_id FROM user_groups_replica WHERE user_id = %s ORDER BY group_id",
            (row[0],),
        ).fetchall()
    return [group_id for (group_id,) in rows]


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


def search(query: str, groups: list[int], k: int, mode: str) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"text": query, "groups": groups, "limit": CANDIDATES}
    ranked: dict[str, list[dict[str, Any]]] = {}
    with psycopg.connect(dsn("INDEX_DB"), row_factory=dict_row) as conn:
        register_vector(conn)
        if mode in ("vector", "hybrid"):
            params["vector"] = Embedder().embed_query(query)
            # With a filter, HNSW may run out of candidates before it finds enough allowed rows.
            # Iterative scan (pgvector 0.8+) lets it keep walking the graph until it has enough.
            conn.execute("SET hnsw.iterative_scan = relaxed_order")
            ranked["vec"] = conn.execute(VECTOR_SQL, params).fetchall()
        if mode in ("keyword", "hybrid"):
            ranked["kw"] = conn.execute(KEYWORD_SQL, params).fetchall()
    return rrf_merge(ranked)[:k]


def label(hit: dict[str, Any]) -> str:
    meta = hit["metadata"]
    if hit["source_table"] == "products":
        return f"product  {meta['sku']}"
    heading = f" > {meta['heading']}" if meta["heading"] else ""
    return f"{meta['kind']:<8} {meta['title']}{heading}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid search over the LiveRAG index.")
    parser.add_argument("query")
    parser.add_argument("--as", dest="email", help="search as this user (default: anonymous)")
    parser.add_argument("-k", type=int, default=6, help="number of results (default 6)")
    parser.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    args = parser.parse_args()

    groups = groups_for(args.email)
    hits = search(args.query, groups, args.k, args.mode)

    names = ", ".join(GROUP_NAMES.get(g, str(g)) for g in groups) or "none"
    print(f'\n"{args.query}"  as {args.email or "anonymous"}  (groups: {names})  '
          f"mode: {args.mode}\n")
    shown = {"hybrid": ["vec", "kw"], "vector": ["vec"], "keyword": ["kw"]}[args.mode]
    for rank, hit in enumerate(hits, start=1):
        # Where the chunk ranked in each search; "-" means that search did not find it.
        ranks = "  ".join(f"{name}#{hit['ranks'].get(name, '-')}" for name in shown)
        snippet = " ".join(hit["text"].split())[:80]
        print(f"{rank}. {hit['rrf']:.4f}  [{ranks}]  {label(hit)}")
        print(f"   {snippet}")
    if not hits:
        print("No results.")


if __name__ == "__main__":
    main()