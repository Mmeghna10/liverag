"""Stream committed changes from the source database's write-ahead log into Redpanda, one message
per transaction (or to the screen with --sink stdout).

Run from services/cdc:
  uv run python -m cdc.reader                  # publish to Redpanda until Ctrl+C
  uv run python -m cdc.reader --sink stdout    # print instead (debugging)
  uv run python -m cdc.reader --max-tx 3       # stop after 3 transactions
  uv run python -m cdc.reader --drop-slot      # remove the slot (do this when you stop using CDC!)

A transaction's LSN is confirmed to Postgres only AFTER the sink has stored it. If the reader
crashes in between, Postgres sends that transaction again on restart: nothing is lost (at-least-once).
"""

import argparse
import dataclasses
import sys

import psycopg2
import psycopg2.extras
from liverag_core.config import dsn

from cdc.pgoutput import Decoder, str_to_lsn
from cdc.sinks import Sink, make_sink

SLOT = "liverag_slot"
PUBLICATION = "liverag_pub"  # created in infra/postgres/source-db/init/03-publication.sql


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)  # stdout carries only the events


def ensure_slot(slot: str) -> None:
    """Create the slot once. From that moment Postgres keeps WAL until we confirm it."""
    with psycopg2.connect(dsn("SOURCE_DB")) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_replication_slots WHERE slot_name = %s", (slot,))
        if cur.fetchone():
            log(f"Using existing slot {slot}")
            return
        cur.execute("SELECT lsn FROM pg_create_logical_replication_slot(%s, 'pgoutput')", (slot,))
        log(f"Created slot {slot} at LSN {cur.fetchone()[0]}")


def drop_slot(slot: str) -> None:
    with psycopg2.connect(dsn("SOURCE_DB")) as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
                    "WHERE slot_name = %s", (slot,))
        log(f"Dropped slot {slot}" if cur.rowcount else f"No slot named {slot}")


def stream(slot: str, publication: str, sink: Sink, max_tx: int | None) -> None:
    conn = psycopg2.connect(dsn("SOURCE_DB"),
                            connection_factory=psycopg2.extras.LogicalReplicationConnection)
    cur = conn.cursor()
    cur.start_replication(slot_name=slot, decode=False, status_interval=10,
                          options={"proto_version": "1", "publication_names": publication})
    decoder = Decoder()
    seen = 0
    log(f"Streaming from slot {slot} (publication {publication}). Ctrl+C to stop.")

    def consume(msg: psycopg2.extras.ReplicationMessage) -> None:
        nonlocal seen
        tx = decoder.feed(msg.payload)
        if tx is None:
            return
        sink.send(dataclasses.asdict(tx))
        # Confirm only now: everything up to this commit is safely stored by the sink.
        # force=True sends it at once; otherwise psycopg2 only queues it, and a stop right
        # after this line would lose the confirmation (the transaction would be sent again).
        msg.cursor.send_feedback(flush_lsn=str_to_lsn(tx.end_lsn), force=True)
        seen += 1
        if max_tx and seen >= max_tx:
            raise psycopg2.extras.StopReplication

    try:
        cur.consume_stream(consume)
    except (KeyboardInterrupt, psycopg2.extras.StopReplication):
        pass
    finally:
        sink.close()
        conn.close()
        log(f"Stopped after {seen} transaction(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slot", default=SLOT)
    parser.add_argument("--publication", default=PUBLICATION)
    parser.add_argument("--sink", choices=["kafka", "stdout"], default="kafka")
    parser.add_argument("--max-tx", type=int, help="stop after this many transactions")
    parser.add_argument("--drop-slot", action="store_true", help="drop the slot and exit")
    args = parser.parse_args()
    if args.drop_slot:
        drop_slot(args.slot)
        return
    sink = make_sink(args.sink)  # connect to Redpanda first: no point holding a slot without it
    ensure_slot(args.slot)
    stream(args.slot, args.publication, sink, args.max_tx)


if __name__ == "__main__":
    main()