"""Chaos test for the CDC pipeline: random changes + hard kills, then prove the index is still right.

Run from the evals folder (Docker up; API, reader and indexer NOT running):
  uv run python -m evals.chaos                      # 80 changes, ~6 kills
  uv run python -m evals.chaos --changes 200 --kill-every 10

What it does:
  1. Checks the index matches the source before starting (otherwise: run the batch indexer).
  2. Starts the CDC reader and the streaming indexer as child processes.
  3. Makes random changes to the source (prices, stock status, document permissions, group
     memberships, soft deletes), several in one transaction now and then.
  4. Every few changes, HARD-KILLS the reader or the indexer (like a power cut) and restarts it.
  5. Undoes every change, so the source ends exactly as it started.
  6. Waits until the indexer has caught up, then compares fingerprints:
       index == source, source == before, index == before.
  7. Reports freshness (lag from source commit to index commit) and writes results/chaos.json.

If it is interrupted half-way, reset everything: run the simulator seed, then the batch indexer.
"""

import argparse
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from confluent_kafka import Consumer, TopicPartition
from liverag_core.config import ROOT, dsn

HERE = Path(__file__).resolve().parent.parent
RESULTS = HERE / "results" / "chaos.json"
LOGS = HERE / "results" / "chaos-logs"
TOPIC, CONSUMER = "liverag.changes", "liverag-indexer"
PRODUCT_STATUSES = ["in_stock", "low_stock", "out_of_stock", "discontinued"]

# Fingerprints: an md5 over every row that matters, so "equal" means equal everywhere.
SOURCE_PRINTS = {
    "products": "SELECT md5(string_agg(sku || price_inr || status, ',' ORDER BY sku)) FROM products",
    "memberships": "SELECT md5(string_agg(user_id::text || '/' || group_id, ',' "
                   "ORDER BY user_id, group_id)) FROM user_groups",
    "documents": """SELECT md5(string_agg(d.id::text || ':' || coalesce((SELECT string_agg(
                        group_id::text, ',' ORDER BY group_id) FROM document_acl a
                        WHERE a.document_id = d.id), ''), ';' ORDER BY d.id))
                    FROM documents d WHERE d.deleted_at IS NULL""",
}
INDEX_PRINTS = {
    "products": "SELECT md5(string_agg((metadata->>'sku') || (metadata->>'price_inr') || "
                "(metadata->>'status'), ',' ORDER BY metadata->>'sku')) FROM chunks_v1 "
                "WHERE source_table = 'products'",
    "memberships": "SELECT md5(string_agg(user_id::text || '/' || group_id, ',' "
                   "ORDER BY user_id, group_id)) FROM user_groups_replica",
    "documents": """SELECT md5(string_agg(source_id::text || ':' ||
                        array_to_string(allowed_group_ids, ','), ';' ORDER BY source_id))
                    FROM (SELECT DISTINCT source_id, allowed_group_ids FROM chunks_v1
                          WHERE source_table = 'documents') x""",
}
CHUNKS_PRINT = ("SELECT md5(string_agg(id::text || content_hash || allowed_group_ids::text, ',' "
                "ORDER BY id)) FROM chunks_v1")


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def prints(conn: psycopg.Connection, queries: dict[str, str]) -> dict[str, str]:
    return {name: conn.execute(sql).fetchone()[0] for name, sql in queries.items()}


class Service:
    """A pipeline process we can start and hard-kill. Runs the service's own venv Python
    directly (not through `uv run`), so kill() really stops the process, like a power cut."""

    def __init__(self, name: str, folder: str, module: str) -> None:
        self.name, self.module = name, module
        self.cwd = ROOT / "services" / folder
        scripts = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        self.python = self.cwd / ".venv" / scripts
        if not self.python.exists():
            raise SystemExit(f"{self.python} not found: run `uv sync` in {self.cwd} first")
        self.proc: subprocess.Popen | None = None
        self.runs = 0
        self.ready_text = "Streaming from slot" if name == "reader" else "Consuming"

    def start(self) -> None:
        self.runs += 1
        self.logfile = LOGS / f"{self.name}-{self.runs}.log"
        out = self.logfile.open("w", encoding="utf-8")
        self.proc = subprocess.Popen([str(self.python), "-m", self.module], cwd=self.cwd,
                                     stdout=subprocess.DEVNULL, stderr=out)
        deadline = time.time() + 180  # the indexer loads the embedding model first
        while self.ready_text not in self.logfile.read_text(encoding="utf-8", errors="replace"):
            if self.proc.poll() is not None:
                raise SystemExit(f"{self.name} exited at start; see {self.logfile}")
            if time.time() > deadline:
                raise SystemExit(f"{self.name} did not become ready; see {self.logfile}")
            time.sleep(0.5)

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()  # TerminateProcess on Windows, SIGKILL elsewhere: no clean-up runs
            self.proc.wait()


class Workload:
    """Random, reversible changes to the source. Remembers how to undo each one."""

    def __init__(self, conn: psycopg.Connection, rng: random.Random) -> None:
        self.conn, self.rng = conn, rng
        self.undo: list[tuple[str, tuple]] = []  # SQL + params, applied in reverse at the end
        self.products = [r[0] for r in conn.execute("SELECT id FROM products")]
        self.acl = conn.execute("SELECT document_id, group_id FROM document_acl").fetchall()
        self.members = conn.execute("SELECT user_id, group_id FROM user_groups").fetchall()
        self.public_docs = [r[0] for r in conn.execute(
            "SELECT id FROM documents WHERE is_public AND deleted_at IS NULL")]
        self.counts: dict[str, int] = {}

    def one_change(self, cur: psycopg.Cursor) -> None:
        # Only pick kinds that can still happen (e.g. once every membership is removed, stop).
        kinds = ["price", "price", "status"] + (["acl"] if self.acl else []) + (
            ["membership"] if self.members else []) + (["soft_delete"] if self.public_docs else [])
        kind = self.rng.choice(kinds)
        self.counts[kind] = self.counts.get(kind, 0) + 1
        if kind == "price":
            pid = self.rng.choice(self.products)
            old = cur.execute("SELECT price_inr FROM products WHERE id = %s", (pid,)).fetchone()[0]
            cur.execute("UPDATE products SET price_inr = %s WHERE id = %s",
                        (self.rng.randrange(499, 30000, 100), pid))
            self.undo.append(("UPDATE products SET price_inr = %s WHERE id = %s", (old, pid)))
        elif kind == "status":
            pid = self.rng.choice(self.products)
            old = cur.execute("SELECT status FROM products WHERE id = %s", (pid,)).fetchone()[0]
            cur.execute("UPDATE products SET status = %s WHERE id = %s",
                        (self.rng.choice(PRODUCT_STATUSES), pid))
            self.undo.append(("UPDATE products SET status = %s WHERE id = %s", (old, pid)))
        elif kind == "acl":  # revoke a group's access to a document
            doc, group = self.acl.pop(self.rng.randrange(len(self.acl)))
            cur.execute("DELETE FROM document_acl WHERE document_id = %s AND group_id = %s",
                        (doc, group))
            self.undo.append(("INSERT INTO document_acl (document_id, group_id) VALUES (%s, %s)",
                              (doc, group)))
        elif kind == "membership":  # remove someone from a group
            user, group = self.members.pop(self.rng.randrange(len(self.members)))
            cur.execute("DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                        (user, group))
            self.undo.append(("INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)",
                              (user, group)))
        elif kind == "soft_delete":
            doc = self.public_docs.pop(self.rng.randrange(len(self.public_docs)))
            cur.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (doc,))
            self.undo.append(("UPDATE documents SET deleted_at = NULL WHERE id = %s", (doc,)))

    def transaction(self) -> int:
        """One source transaction with 1 change, or now and then 2-5 together."""
        size = 1 if self.rng.random() < 0.8 else self.rng.randint(2, 5)
        with self.conn.transaction(), self.conn.cursor() as cur:
            for _ in range(size):
                self.one_change(cur)
        return size

    def undo_all(self) -> int:
        with self.conn.transaction(), self.conn.cursor() as cur:
            for sql, params in reversed(self.undo):
                cur.execute(sql, params)
        return len(self.undo)


def topic_end(bootstrap: str) -> int:
    """Offset of the next message to be written to the topic (the 'high watermark')."""
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": "liverag-chaos-probe"})
    try:
        return consumer.get_watermark_offsets(TopicPartition(TOPIC, 0), timeout=10)[1]
    finally:
        consumer.close()


def wait_caught_up(source: psycopg.Connection, index: psycopg.Connection, bootstrap: str,
                   timeout: float = 300) -> float:
    """Wait until the reader has confirmed all WAL so far AND the indexer applied every message."""
    started = time.time()
    target = source.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    source.execute("UPDATE canary SET value = value + 1, written_at = now() WHERE id = 1")  # nudge
    while time.time() - started < timeout:
        confirmed = source.execute("SELECT confirmed_flush_lsn >= %s FROM pg_replication_slots "
                                   "WHERE slot_name = 'liverag_slot'", (target,)).fetchone()
        end = topic_end(bootstrap)
        row = index.execute("SELECT next_offset FROM sink_offsets WHERE consumer = %s",
                            (CONSUMER,)).fetchone()
        if confirmed and confirmed[0] and row and row[0] >= end:
            return time.time() - started
        time.sleep(1)
    raise SystemExit("Pipeline did not catch up within the timeout")


def drop_slot(source: psycopg.Connection) -> None:
    """Drop the slot so it does not hold WAL. Postgres needs a few seconds to notice that the
    killed reader is gone; until then the slot counts as active and cannot be dropped."""
    for _ in range(30):
        row = source.execute("SELECT active FROM pg_replication_slots "
                             "WHERE slot_name = 'liverag_slot'").fetchone()
        if row is None:
            return
        if not row[0]:
            source.execute("SELECT pg_drop_replication_slot('liverag_slot')")
            log("Dropped the replication slot.")
            return
        time.sleep(1)
    log("WARNING: slot still active after 30 s; drop it with `cdc.reader --drop-slot`.")



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changes", type=int, default=80, help="source transactions to make")
    parser.add_argument("--kill-every", type=int, default=12, help="kill a service every N")
    parser.add_argument("--seed", type=int, default=7, help="random seed (same seed = same run)")
    parser.add_argument("--keep-slot", action="store_true", help="do not drop the slot at the end")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092")

    LOGS.mkdir(parents=True, exist_ok=True)
    for old in LOGS.glob("*.log"):  # logs of this run only
        old.unlink()
    source = psycopg.connect(dsn("SOURCE_DB"), autocommit=True)
    index = psycopg.connect(dsn("INDEX_DB"), autocommit=True)

    source_before = prints(source, SOURCE_PRINTS)
    index_before = prints(index, INDEX_PRINTS)
    chunks_before = index.execute(CHUNKS_PRINT).fetchone()[0]
    if source_before != index_before:
        raise SystemExit("The index does not match the source BEFORE the test. Run "
                         "`uv run python -m indexer.batch` in services/indexer first.")
    log("Before: index matches source.")

    reader = Service("reader", "cdc", "cdc.reader")
    indexer = Service("indexer", "indexer", "indexer.stream")
    services = [reader, indexer]
    kills = {"reader": 0, "indexer": 0}
    workload = Workload(source, rng)
    try:
        reader.start()
        indexer.start()
        log(f"Reader and indexer running. Making {args.changes} source transactions...")
        rows_changed = 0
        for i in range(1, args.changes + 1):
            rows_changed += workload.transaction()
            if i % args.kill_every == 0:
                victim = rng.choice(services)
                victim.kill()
                kills[victim.name] += 1
                log(f"  change {i}: KILLED {victim.name} (hard)")
                time.sleep(rng.uniform(0.5, 3))   # it stays down while changes keep coming
                for _ in range(rng.randint(1, 4)):
                    rows_changed += workload.transaction()
                victim.start()
                log(f"  {victim.name} restarted")
            time.sleep(rng.uniform(0.05, 0.4))

        undone = workload.undo_all()
        log(f"Made {rows_changed} row changes; undid them all in one transaction ({undone}).")
        catch_up = wait_caught_up(source, index, bootstrap)
        log(f"Pipeline caught up {catch_up:.1f} s after the last change.")
    finally:
        for service in services:
            service.kill()
            if not args.keep_slot:
             drop_slot(source)

    source_after = prints(source, SOURCE_PRINTS)
    index_after = prints(index, INDEX_PRINTS)
    chunks_after = index.execute(CHUNKS_PRINT).fetchone()[0]
    checks = {
        "index_equals_source": index_after == source_after,
        "source_restored": source_after == source_before,
        "index_restored": index_after == index_before and chunks_after == chunks_before,
    }

    # Freshness and work done, from the indexer logs of this run.
    lags, embedded, applied, skipped = [], 0, 0, 0
    pattern = re.compile(r"applied (\d+), skipped (\d+), embedded (\d+), lag ([\d.]+) s")
    for logfile in sorted(LOGS.glob("indexer-*.log")):
        for m in pattern.finditer(logfile.read_text(encoding="utf-8", errors="replace")):
            applied += int(m[1]); skipped += int(m[2]); embedded += int(m[3])
            lags.append(float(m[4]))
    lags.sort()
    summary = {
        "source_transactions": args.changes, "row_changes": rows_changed,
        "change_mix": workload.counts, "kills": kills,
        "messages_applied": len(lags), "entities_applied": applied,
        "entities_skipped_by_lsn_guard": skipped, "texts_embedded": embedded,
        "lag_p50_s": statistics.median(lags) if lags else None,
        "lag_p95_s": lags[min(len(lags) - 1, round(0.95 * (len(lags) - 1)))] if lags else None,
        "lag_max_s": lags[-1] if lags else None,
        "catch_up_s": round(catch_up, 1), **checks,
    }
    print()
    for key, value in summary.items():
        print(f"  {key:<32} {value}")
    ok = all(checks.values())
    print(f"\n{'PASS' if ok else 'FAIL'}: "
          f"{'index is exactly right after all kills' if ok else 'see checks above'}")
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({"run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                   "args": vars(args), "summary": summary}, indent=2) + "\n",
                       encoding="utf-8")
    print(f"Saved {RESULTS.relative_to(HERE)}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()