"""Stage 1 batch indexer: snapshot the source DB, chunk, embed (with cache), rebuild chunks_v1.

Run from services/indexer:  uv run python -m indexer.batch
"""


import time
from typing import Any

import numpy as np
import psycopg
from liverag_core.config import dsn
from liverag_core.embedder import Embedder
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from indexer.chunking import Chunk, document_chunks, product_chunks




def read_source(snapshot: str | None = None, snapshot_lsn: str | None = None
                ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read everything from one consistent snapshot, plus the WAL position it matches.

    With `snapshot` (exported when a replication slot is created, see indexer/bootstrap.py) the
    read sees the database exactly at the slot's start, `snapshot_lsn`: no gap, no overlap.
    """
    with psycopg.connect(dsn("SOURCE_DB"), row_factory=dict_row) as conn:
        # REPEATABLE READ: every query below sees the database exactly as it was at the first one.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        if snapshot:
            # Must be the first statement of the transaction. It takes no $1 parameter, so the
            # snapshot name is embedded as a safely quoted literal.
            conn.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot)))
            lsn = snapshot_lsn
        else:
            lsn = conn.execute("SELECT pg_current_wal_lsn()::text AS lsn").fetchone()["lsn"]
        products = conn.execute(
            "SELECT id, sku, name, description, price_inr, status FROM products"
        ).fetchall()
        documents = conn.execute("""
            SELECT d.id, d.kind, d.title, d.body, d.is_public,
                   coalesce(array_agg(da.group_id ORDER BY da.group_id)
                            FILTER (WHERE da.group_id IS NOT NULL), '{}') AS group_ids
            FROM documents d
            LEFT JOIN document_acl da ON da.document_id = d.id
            WHERE d.deleted_at IS NULL
            GROUP BY d.id
        """).fetchall()
        memberships = conn.execute("SELECT user_id, group_id FROM user_groups").fetchall()
    return lsn, products, documents, memberships


def embed_with_cache(conn: psycopg.Connection, embedder: Embedder,
                     chunks: list[Chunk]) -> tuple[dict[str, np.ndarray], int]:
    """Return a vector for every content hash, embedding only texts not already cached."""
    text_by_hash = {c.content_hash: c.text for c in chunks}
    hashes = sorted(text_by_hash)
    rows = conn.execute(
        "SELECT content_hash, embedding FROM embedding_cache "
        "WHERE model = %s AND content_hash = ANY(%s)",
        (embedder.model_name, hashes),
    ).fetchall()
    vectors = {h: v for h, v in rows}

    missing = [h for h in hashes if h not in vectors]
    if missing:
        new_vectors = embedder.embed_passages([text_by_hash[h] for h in missing])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO embedding_cache (model, content_hash, embedding) VALUES (%s, %s, %s)",
                [(embedder.model_name, h, v) for h, v in zip(missing, new_vectors)],
            )
        vectors.update(zip(missing, new_vectors))
    return vectors, len(missing)


def write_index(conn: psycopg.Connection, lsn: str, chunks: list[Chunk],
                vectors: dict[str, np.ndarray], memberships: list[dict[str, Any]]) -> None:
    """Replace the index contents. Runs inside the caller's transaction."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE chunks_v1, source_state, user_groups_replica")
        cur.executemany(
            "INSERT INTO chunks_v1 (id, source_table, source_id, chunk_no, text, embedding, "
            "content_hash, is_public, allowed_group_ids, metadata, source_lsn) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::pg_lsn)",
            [(c.id, c.source_table, c.source_id, c.chunk_no, c.text, vectors[c.content_hash],
              c.content_hash, c.is_public, c.allowed_group_ids, Jsonb(c.metadata), lsn)
             for c in chunks],
        )
        cur.executemany(
            "INSERT INTO source_state (source_table, source_id, last_lsn) "
            "VALUES (%s, %s, %s::pg_lsn)",
            sorted({(c.source_table, str(c.source_id), lsn) for c in chunks}),
        )
        cur.executemany(
            "INSERT INTO user_groups_replica (user_id, group_id, last_lsn) "
            "VALUES (%s, %s, %s::pg_lsn)",
            [(m["user_id"], m["group_id"], lsn) for m in memberships],
        )


def main() -> None:
    started = time.perf_counter()
    lsn, products, documents, memberships = read_source()
    chunks = ([c for p in products for c in product_chunks(p)]
              + [c for d in documents for c in document_chunks(d)])
    print(f"Snapshot at LSN {lsn}: {len(products)} products, {len(documents)} documents "
          f"-> {len(chunks)} chunks")

    # Load the model before opening the transaction: the first run downloads it (~130 MB).
    embedder = Embedder()

    # One transaction: readers see the old index or the new one, never half of each.
    with psycopg.connect(dsn("INDEX_DB")) as conn:
        register_vector(conn)
        vectors, embedded = embed_with_cache(conn, embedder, chunks)
        write_index(conn, lsn, chunks, vectors, memberships)

    print(f"Embedded {embedded} new texts, reused {len(vectors) - embedded} from the cache")
    print(f"Wrote {len(chunks)} chunks, {len(memberships)} group memberships "
          f"in {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    main()