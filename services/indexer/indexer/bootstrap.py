"""Stage 3 bootstrap: build the index AND start CDC from the same instant, with no gap.

Run from services/indexer, BEFORE starting the CDC reader and the streaming indexer:
  uv run python -m indexer.bootstrap            # refuses if the slot already exists
  uv run python -m indexer.bootstrap --force    # drop an existing slot first (re-bootstrap)

The problem it solves: if you build the index from a snapshot and only THEN create the
replication slot, every change committed in between is in neither the index nor the stream.

How it works:
  1. Create the replication slot and, in the same step, ask Postgres to EXPORT A SNAPSHOT.
     Postgres guarantees the snapshot shows exactly the data up to the slot's starting LSN.
  2. While that replication connection stays open, read the whole source through the snapshot.
  3. Build the index from it, record that LSN in source_state, and set the indexer's Kafka
     offset to the end of the topic (older messages are from before the snapshot).
  4. From now on the slot streams exactly the changes AFTER the snapshot: no gap, no overlap.
"""

import argparse
import os
import sys
import time

import psycopg
import psycopg2
import psycopg2.extras
from confluent_kafka import Consumer, KafkaException, TopicPartition
from liverag_core.config import dsn
from liverag_core.embedder import Embedder
from pgvector.psycopg import register_vector

from indexer.batch import embed_with_cache, read_source, write_index
from indexer.chunking import document_chunks, product_chunks
from indexer.stream import CONSUMER, PARTITION, TOPIC

SLOT = "liverag_slot"  # the slot the CDC reader uses (services/cdc/cdc/reader.py)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def topic_end() -> int:
    """Offset the next message will get; 0 if the topic does not exist yet."""
    consumer = Consumer({"bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092"),
                         "group.id": "liverag-bootstrap"})
    try:
        return max(consumer.get_watermark_offsets(TopicPartition(TOPIC, PARTITION), timeout=10)[1], 0)
    except KafkaException:  # the topic does not exist yet
        return 0
    finally:
        consumer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="drop an existing slot first")
    args = parser.parse_args()
    started = time.perf_counter()

    with psycopg.connect(dsn("SOURCE_DB"), autocommit=True) as conn:
        exists = conn.execute("SELECT active FROM pg_replication_slots WHERE slot_name = %s",
                              (SLOT,)).fetchone()
        if exists and not args.force:
            raise SystemExit(f"Slot {SLOT} already exists: CDC is already running from it. "
                             "Use --force to start over (stop the reader first).")
        if exists:
            if exists[0]:
                raise SystemExit(f"Slot {SLOT} is in use: stop the CDC reader first.")
            conn.execute("SELECT pg_drop_replication_slot(%s)", (SLOT,))
            log(f"Dropped old slot {SLOT}")

    # Load the model and find the topic end first, so the snapshot is held for as short a time
    # as possible.
    embedder = Embedder()
    next_offset = topic_end()

    # 1. Slot + exported snapshot, in one command on a replication connection.
    repl = psycopg2.connect(dsn("SOURCE_DB"),
                            connection_factory=psycopg2.extras.LogicalReplicationConnection)
    try:
        cur = repl.cursor()
        cur.execute(f"CREATE_REPLICATION_SLOT {SLOT} LOGICAL pgoutput (SNAPSHOT 'export')")
        _, lsn, snapshot, _ = cur.fetchone()
        log(f"Created slot {SLOT} at LSN {lsn} with snapshot {snapshot}")
        # 2. The snapshot only lives while this connection is open and idle: read now.
        _, products, documents, memberships = read_source(snapshot=snapshot, snapshot_lsn=lsn)
    finally:
        repl.close()  # snapshot released; the slot itself stays and keeps WAL from `lsn` on

    chunks = ([c for p in products for c in product_chunks(p)]
              + [c for d in documents for c in document_chunks(d)])
    log(f"Snapshot read: {len(products)} products, {len(documents)} documents -> "
        f"{len(chunks)} chunks")

    # 3. Index + offset in one transaction.
    with psycopg.connect(dsn("INDEX_DB")) as conn:
        register_vector(conn)
        vectors, embedded = embed_with_cache(conn, embedder, chunks)
        write_index(conn, lsn, chunks, vectors, memberships)
        conn.execute("""
            INSERT INTO sink_offsets (consumer, topic, partition, next_offset)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (consumer, topic, partition) DO UPDATE SET
                next_offset = EXCLUDED.next_offset, updated_at = now()""",
            (CONSUMER, TOPIC, PARTITION, next_offset))

    log(f"Embedded {embedded} new texts; wrote {len(chunks)} chunks at LSN {lsn}; "
        f"indexer will start at offset {next_offset}. Took {time.perf_counter() - started:.1f} s")
    log("Now start the CDC reader and the streaming indexer. The slot is holding WAL until then.")


if __name__ == "__main__":
    main()