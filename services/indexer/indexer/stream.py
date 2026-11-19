"""Stage 2 streaming indexer: apply each change event from Redpanda to the index, exactly once.

Run from services/indexer:
  uv run python -m indexer.stream                   # follow the topic until Ctrl+C
  uv run python -m indexer.stream --max-messages 5  # stop after 5 messages (testing)

How one message (= one source transaction) is applied, inside ONE index-DB transaction:
  1. Work out which entities it touched: a product, a document (also via its ACL rows),
     or a user's group memberships.
  2. LSN guard: skip an entity whose source_state.last_lsn is already >= this commit LSN
     (a duplicate or replayed event).
  3. Re-read the entity's CURRENT state from the source DB and re-sync it: rebuild its chunks
     (embedding only text that changed), or delete them if the row is gone.
  4. Store the next Kafka offset in sink_offsets.
  5. COMMIT. Chunks, guard and offset change together, or not at all.
A crash before step 5 changes nothing, and the message is processed again on restart.
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
from confluent_kafka import OFFSET_BEGINNING, Consumer, KafkaException, TopicPartition
from liverag_core.config import dsn
from liverag_core.embedder import Embedder
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from indexer.batch import embed_with_cache
from indexer.chunking import Chunk, document_chunks, product_chunks

TOPIC = "liverag.changes"
CONSUMER = "liverag-indexer"
PARTITION = 0  # the topic has exactly one partition (see services/cdc/cdc/sinks.py)

Entity = tuple[str, str]  # ("products" | "documents" | "user_groups", id as text)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def entities_of(tx: dict[str, Any], conn: psycopg.Connection) -> set[Entity]:
    """Which indexable things did this transaction touch?"""
    found: set[Entity] = set()
    for change in tx["changes"]:
        table, key = change["table"], change["key"]
        if change["op"] == "truncate":
            # Everything we know of in that table must be re-checked (a re-seed does this).
            kind = {"public.products": "products", "public.documents": "documents",
                    "public.document_acl": "documents", "public.user_groups": "user_groups"}.get(table)
            if kind:
                rows = conn.execute("SELECT source_id FROM source_state WHERE source_table = %s",
                                    (kind,)).fetchall()
                found |= {(kind, r[0]) for r in rows}
        elif table == "public.products":
            found.add(("products", key["id"]))
        elif table == "public.documents":
            found.add(("documents", key["id"]))
        elif table == "public.document_acl":
            found.add(("documents", key["document_id"]))
        elif table == "public.user_groups":
            found.add(("user_groups", key["user_id"]))
        # public.canary is a heartbeat: nothing to index.
    return found


def read_current(source: psycopg.Connection, kind: str, entity_id: str) -> Any:
    """The entity as it is in the source NOW: a row, a list of group ids, or None if gone."""
    if kind == "products":
        return source.execute(
            "SELECT id, sku, name, description, price_inr, status FROM products WHERE id = %s",
            (entity_id,)).fetchone()
    if kind == "documents":
        return source.execute("""
            SELECT d.id, d.kind, d.title, d.body, d.is_public,
                   coalesce(array_agg(da.group_id ORDER BY da.group_id)
                            FILTER (WHERE da.group_id IS NOT NULL), '{}') AS group_ids
            FROM documents d LEFT JOIN document_acl da ON da.document_id = d.id
            WHERE d.id = %s AND d.deleted_at IS NULL
            GROUP BY d.id""", (entity_id,)).fetchone()
    rows = source.execute("SELECT group_id FROM user_groups WHERE user_id = %s ORDER BY group_id",
                          (entity_id,)).fetchall()
    return [r["group_id"] for r in rows]


def sync_chunks(index: psycopg.Connection, embedder: Embedder, kind: str, entity_id: str,
                chunks: list[Chunk], lsn: str) -> int:
    """Make the index hold exactly these chunks for the entity. Returns texts newly embedded."""
    vectors, embedded = embed_with_cache(index, embedder, chunks) if chunks else ({}, 0)
    with index.cursor() as cur:
        cur.execute("DELETE FROM chunks_v1 WHERE source_table = %s AND source_id = %s "
                    "AND NOT (chunk_no = ANY(%s))", (kind, entity_id, [c.chunk_no for c in chunks]))
        cur.executemany("""
            INSERT INTO chunks_v1 (id, source_table, source_id, chunk_no, text, embedding,
                                   content_hash, is_public, allowed_group_ids, metadata, source_lsn)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::pg_lsn)
            ON CONFLICT (source_table, source_id, chunk_no) DO UPDATE SET
                text = EXCLUDED.text, embedding = EXCLUDED.embedding,
                content_hash = EXCLUDED.content_hash, is_public = EXCLUDED.is_public,
                allowed_group_ids = EXCLUDED.allowed_group_ids, metadata = EXCLUDED.metadata,
                source_lsn = EXCLUDED.source_lsn, updated_at = now()""",
            [(c.id, c.source_table, c.source_id, c.chunk_no, c.text, vectors[c.content_hash],
              c.content_hash, c.is_public, c.allowed_group_ids, Jsonb(c.metadata), lsn)
             for c in chunks])
    return embedded


def apply_entity(index: psycopg.Connection, source: psycopg.Connection, embedder: Embedder,
                 kind: str, entity_id: str, lsn: str) -> tuple[str, int]:
    """Re-sync one entity. Returns (what happened, texts embedded)."""
    current = read_current(source, kind, entity_id)
    embedded = 0
    if kind == "user_groups":
        index.execute("DELETE FROM user_groups_replica WHERE user_id = %s", (entity_id,))
        with index.cursor() as cur:
            cur.executemany("INSERT INTO user_groups_replica (user_id, group_id, last_lsn) "
                            "VALUES (%s, %s, %s::pg_lsn)", [(entity_id, g, lsn) for g in current])
        outcome, gone = f"groups={current}", False
    elif current is None:
        index.execute("DELETE FROM chunks_v1 WHERE source_table = %s AND source_id = %s",
                      (kind, entity_id))
        outcome, gone = "deleted", True
    else:
        chunks = product_chunks(current) if kind == "products" else document_chunks(current)
        embedded = sync_chunks(index, embedder, kind, entity_id, chunks, lsn)
        outcome, gone = f"{len(chunks)} chunk(s)", False
    index.execute("""
        INSERT INTO source_state (source_table, source_id, last_lsn, deleted)
        VALUES (%s, %s, %s::pg_lsn, %s)
        ON CONFLICT (source_table, source_id) DO UPDATE SET
            last_lsn = EXCLUDED.last_lsn, deleted = EXCLUDED.deleted, updated_at = now()""",
        (kind, entity_id, lsn, gone))
    return outcome, embedded


def apply_message(index: psycopg.Connection, source: psycopg.Connection, embedder: Embedder,
                  offset: int, tx: dict[str, Any]) -> None:
    lsn = tx["commit_lsn"]
    applied, skipped, embedded = [], 0, 0
    with index.transaction():
        for kind, entity_id in sorted(entities_of(tx, index)):
            row = index.execute("SELECT last_lsn >= %s::pg_lsn FROM source_state "
                                "WHERE source_table = %s AND source_id = %s",
                                (lsn, kind, entity_id)).fetchone()
            if row and row[0]:
                skipped += 1  # already applied this change, or a newer one
                continue
            outcome, n = apply_entity(index, source, embedder, kind, entity_id, lsn)
            applied.append(f"{kind}:{entity_id[:8]} {outcome}")
            embedded += n
        index.execute("""
            INSERT INTO sink_offsets (consumer, topic, partition, next_offset)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (consumer, topic, partition) DO UPDATE SET
                next_offset = EXCLUDED.next_offset, updated_at = now()""",
            (CONSUMER, TOPIC, PARTITION, offset + 1))
    # Freshness: time from the source commit until this index commit.
    lag = (datetime.now(UTC) - datetime.fromisoformat(tx["commit_time"])).total_seconds()
    log(f"offset {offset} lsn {lsn} xid {tx['xid']}: applied {len(applied)}, skipped {skipped}, "
        f"embedded {embedded}, lag {lag:.2f} s" + (f"  [{'; '.join(applied[:3])}"
                                                  f"{' ...' if len(applied) > 3 else ''}]"
                                                  if applied else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-messages", type=int, help="stop after this many messages")
    args = parser.parse_args()

    embedder = Embedder()
    embedder.embed_passages(["warm-up"])  # load the model now, not inside the first transaction
    index = psycopg.connect(dsn("INDEX_DB"), autocommit=True)
    register_vector(index)
    source = psycopg.connect(dsn("SOURCE_DB"), autocommit=True, row_factory=dict_row)

    # Our position lives in the index DB, next to the data it produced, not in Redpanda.
    row = index.execute("SELECT next_offset FROM sink_offsets WHERE consumer = %s AND topic = %s "
                        "AND partition = %s", (CONSUMER, TOPIC, PARTITION)).fetchone()
    start = row[0] if row else OFFSET_BEGINNING
    consumer = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092"),
        "group.id": CONSUMER,
        "enable.auto.commit": False,  # Redpanda never stores our offset; the index DB does
        "fetch.message.max.bytes": 16 * 1024 * 1024,
    })
    consumer.assign([TopicPartition(TOPIC, PARTITION, start)])
    log(f"Consuming {TOPIC} from offset {'beginning' if row is None else start}. Ctrl+C to stop.")

    seen = 0
    try:
        while args.max_messages is None or seen < args.max_messages:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                raise KafkaException(msg.error())
            started = time.perf_counter()
            apply_message(index, source, embedder, msg.offset(), json.loads(msg.value()))
            seen += 1
            if time.perf_counter() - started > 5:
                log(f"  (took {time.perf_counter() - started:.1f} s)")
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        index.close()
        source.close()
        log(f"Stopped after {seen} message(s).")


if __name__ == "__main__":
    main()