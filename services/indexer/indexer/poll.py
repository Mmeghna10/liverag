"""Baseline B: timestamp polling (the common alternative to CDC), for comparison only.

Run from services/indexer:  uv run python -m indexer.poll --interval 5

Every `interval` seconds it asks the source for products and documents whose `updated_at` is newer
than the last poll, and re-syncs those rows exactly like the streaming indexer does.

Built-in blind spots (that is the point of the comparison):
  - hard DELETEs leave no row behind, so there is no updated_at to find;
  - document_acl and user_groups have no updated_at, and changing them does not touch the
    document's updated_at, so permission changes are never seen.
"""

import argparse
import sys
import time

import psycopg
from liverag_core.config import dsn
from liverag_core.embedder import Embedder
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from indexer.stream import apply_entity

# Re-check a little before the last watermark: a transaction that started earlier can commit
# after we polled, carrying an older updated_at. Re-syncing a row twice is harmless.
OVERLAP = "10 seconds"


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    args = parser.parse_args()

    embedder = Embedder()
    embedder.embed_passages(["warm-up"])
    index = psycopg.connect(dsn("INDEX_DB"), autocommit=True)
    register_vector(index)
    source = psycopg.connect(dsn("SOURCE_DB"), autocommit=True, row_factory=dict_row)

    watermark = source.execute("SELECT now() AS t").fetchone()["t"]
    log(f"Polling every {args.interval} s for rows updated after {watermark:%H:%M:%S}. Ctrl+C to stop.")
    try:
        while True:
            started = time.perf_counter()
            rows = source.execute(f"""
                SELECT 'products' AS kind, id::text, updated_at FROM products
                 WHERE updated_at > %(w)s::timestamptz - interval '{OVERLAP}'
                UNION ALL
                SELECT 'documents', id::text, updated_at FROM documents
                 WHERE updated_at > %(w)s::timestamptz - interval '{OVERLAP}'""",
                {"w": watermark}).fetchall()
            lsn = source.execute("SELECT pg_current_wal_lsn()::text AS l").fetchone()["l"]
            embedded = 0
            with index.transaction():
                for row in rows:
                    _, n = apply_entity(index, source, embedder, row["kind"], row["id"], lsn)
                    embedded += n
            if rows:
                watermark = max(watermark, max(r["updated_at"] for r in rows))
            log(f"poll: re-synced {len(rows)} rows, embedded {embedded} "
                f"in {time.perf_counter() - started:.2f} s")
            time.sleep(max(0.0, args.interval - (time.perf_counter() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        index.close()
        source.close()


if __name__ == "__main__":
    main()