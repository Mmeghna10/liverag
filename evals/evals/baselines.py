"""Baseline comparison: CDC vs timestamp polling vs periodic batch, on the same workload.

Run from the evals folder (Docker up; API, reader, indexer NOT running; takes ~12 minutes):
  uv run python -m evals.baselines
  uv run python -m evals.baselines --batch-interval 60 --per-type 6

For each method it makes the same 30 changes (6 each: price update, soft delete, hard delete,
ACL revoke, group removal), each on a different row, one every few seconds. A monitor checks the
index 4 times a second and records when each change becomes visible there:
  staleness  = seconds from the source commit until the index reflects the change
  missed     = changes still not reflected when the run ends
  leak window = staleness of ACL revokes and group removals (a revoked user can still read)
Afterwards every change is undone, and at the very end `indexer.bootstrap --force` rebuilds the
index and recreates the slot, so the system ends as it started (keep the slot; start CDC later).
Writes results/baselines.json.
"""

import argparse
import json
import random
import re
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from liverag_core.config import ROOT, dsn

from evals.chaos import LOGS, SOURCE_PRINTS, INDEX_PRINTS, Service, prints

HERE = Path(__file__).resolve().parent.parent
RESULTS = HERE / "results" / "baselines.json"
TYPES = ["price", "soft_delete", "hard_delete", "acl_revoke", "group_remove"]
PERMISSION_TYPES = {"acl_revoke", "group_remove"}


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def indexer_python() -> str:
    scripts = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return str(ROOT / "services" / "indexer" / ".venv" / scripts)


def run_indexer_module(*args: str) -> str:
    """Run an indexer module to completion (batch, bootstrap) and return its output."""
    done = subprocess.run([indexer_python(), "-m", *args], cwd=ROOT / "services" / "indexer",
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise SystemExit(f"indexer {' '.join(args)} failed:\n{done.stderr[-2000:]}")
    return done.stdout + done.stderr


def pick_targets(source: psycopg.Connection, rng: random.Random, n: int) -> list[dict[str, Any]]:
    """n changes of each type, every one on a different row, in a shuffled order."""
    products = [r[0] for r in source.execute("SELECT id FROM products ORDER BY id")]
    rng.shuffle(products)
    docs = [r[0] for r in source.execute(
        "SELECT id FROM documents WHERE is_public AND deleted_at IS NULL ORDER BY id")]
    acl = source.execute("SELECT DISTINCT ON (document_id) document_id, group_id FROM document_acl "
                         "ORDER BY document_id, group_id").fetchall()
    members = source.execute("SELECT DISTINCT ON (user_id) user_id, group_id FROM user_groups "
                             "ORDER BY user_id, group_id").fetchall()
    targets = (
        [{"type": "price", "id": p, "price": rng.randrange(499, 30000, 100)} for p in products[:n]]
        + [{"type": "hard_delete", "id": p} for p in products[n:2 * n]]
        + [{"type": "soft_delete", "id": d} for d in rng.sample(docs, n)]
        + [{"type": "acl_revoke", "id": d, "group": g} for d, g in rng.sample(acl, n)]
        + [{"type": "group_remove", "id": u, "group": g} for u, g in rng.sample(members, n)]
    )
    rng.shuffle(targets)
    return targets


def apply_change(source: psycopg.Connection, t: dict[str, Any]) -> None:
    """Make the change and remember how to undo it."""
    kind, tid = t["type"], t["id"]
    if kind == "price":
        t["old"] = source.execute("SELECT price_inr FROM products WHERE id = %s", (tid,)).fetchone()[0]
        source.execute("UPDATE products SET price_inr = %s WHERE id = %s", (t["price"], tid))
    elif kind == "hard_delete":
        t["row"] = source.execute("SELECT id, sku, name, description, price_inr, status, updated_at "
                                  "FROM products WHERE id = %s", (tid,)).fetchone()
        source.execute("DELETE FROM products WHERE id = %s", (tid,))
    elif kind == "soft_delete":
        source.execute("UPDATE documents SET deleted_at = now() WHERE id = %s", (tid,))
    elif kind == "acl_revoke":
        source.execute("DELETE FROM document_acl WHERE document_id = %s AND group_id = %s",
                       (tid, t["group"]))
    elif kind == "group_remove":
        source.execute("DELETE FROM user_groups WHERE user_id = %s AND group_id = %s",
                       (tid, t["group"]))


def undo_change(source: psycopg.Connection, t: dict[str, Any]) -> None:
    kind, tid = t["type"], t["id"]
    if kind == "price":
        source.execute("UPDATE products SET price_inr = %s WHERE id = %s", (t["old"], tid))
    elif kind == "hard_delete":
        source.execute("INSERT INTO products (id, sku, name, description, price_inr, status, "
                       "updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)", t["row"])
    elif kind == "soft_delete":
        source.execute("UPDATE documents SET deleted_at = NULL WHERE id = %s", (tid,))
    elif kind == "acl_revoke":
        source.execute("INSERT INTO document_acl (document_id, group_id) VALUES (%s, %s)",
                       (tid, t["group"]))
    elif kind == "group_remove":
        source.execute("INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)",
                       (tid, t["group"]))


def visible(index: psycopg.Connection, t: dict[str, Any]) -> bool:
    """Does the index already reflect this change?"""
    kind, tid = t["type"], t["id"]
    if kind == "price":
        row = index.execute("SELECT (metadata->>'price_inr')::int FROM chunks_v1 "
                            "WHERE source_table = 'products' AND source_id = %s", (tid,)).fetchone()
        return bool(row) and row[0] == t["price"]
    if kind in ("hard_delete", "soft_delete"):
        return index.execute("SELECT NOT EXISTS (SELECT 1 FROM chunks_v1 WHERE source_id = %s)",
                             (tid,)).fetchone()[0]
    if kind == "acl_revoke":
        return index.execute("SELECT NOT EXISTS (SELECT 1 FROM chunks_v1 WHERE source_id = %s "
                             "AND %s = ANY(allowed_group_ids))", (tid, t["group"])).fetchone()[0]
    return index.execute("SELECT NOT EXISTS (SELECT 1 FROM user_groups_replica "
                         "WHERE user_id = %s AND group_id = %s)", (tid, t["group"])).fetchone()[0]


class Monitor(threading.Thread):
    """Checks every pending change 4 times a second; records when it first becomes visible."""

    def __init__(self, targets: list[dict[str, Any]]) -> None:
        super().__init__(daemon=True)
        self.targets, self.stop_flag = targets, threading.Event()

    def run(self) -> None:
        with psycopg.connect(dsn("INDEX_DB"), autocommit=True) as index:
            while not self.stop_flag.is_set():
                now = time.time()
                for t in self.targets:
                    if "committed" in t and "seen" not in t and visible(index, t):
                        t["seen"] = now
                time.sleep(0.25)

    def all_seen(self) -> bool:
        return all("seen" in t for t in self.targets)


def summarise(method: str, targets: list[dict[str, Any]], embedded: int,
              chunks_written: int) -> dict[str, Any]:
    lag = sorted(t["seen"] - t["committed"] for t in targets if "seen" in t)
    leaks = sorted(t["seen"] - t["committed"] for t in targets
                   if t["type"] in PERMISSION_TYPES and "seen" in t)
    missed = {k: sum(1 for t in targets if t["type"] == k and "seen" not in t) for k in TYPES}
    pct = lambda xs, p: xs[min(len(xs) - 1, round(p * (len(xs) - 1)))] if xs else None  # noqa: E731
    return {
        "method": method, "changes": len(targets), "visible": len(lag),
        "missed_total": sum(missed.values()), "missed_by_type": missed,
        "staleness_p50_s": round(statistics.median(lag), 2) if lag else None,
        "staleness_p95_s": round(pct(lag, 0.95), 2) if lag else None,
        "staleness_max_s": round(lag[-1], 2) if lag else None,
        "leak_window_p95_s": round(pct(leaks, 0.95), 2) if leaks else None,
        "permission_changes_never_applied": missed["acl_revoke"] + missed["group_remove"],
        "texts_embedded": embedded, "chunks_written": chunks_written,
    }


def run_method(method: str, args: argparse.Namespace, seed: int) -> dict[str, Any]:
    source = psycopg.connect(dsn("SOURCE_DB"), autocommit=True)
    # Start every method from an index that matches the source exactly.
    if method == "cdc":
        run_indexer_module("indexer.bootstrap", "--force")
    else:
        run_indexer_module("indexer.batch")
    for old in LOGS.glob("*.log"):
        old.unlink()

    targets = pick_targets(source, random.Random(seed), args.per_type)
    services: list[Service] = []
    batch_outputs: list[str] = []
    stop_batch = threading.Event()
    if method == "cdc":
        services = [Service("reader", "cdc", "cdc.reader"),
                    Service("indexer", "indexer", "indexer.stream")]
    elif method == "polling":
        poller = Service("poller", "indexer", "indexer.poll")  # polls every 5 s (its default)
        poller.ready_text = "Polling every"
        services = [poller]
    for s in services:
        s.start()

    batch_thread = None
    if method == "batch":
        def batch_loop() -> None:
            next_run = time.time() + args.batch_interval
            while not stop_batch.wait(max(0.0, next_run - time.time())):
                batch_outputs.append(run_indexer_module("indexer.batch"))
                next_run += args.batch_interval
        batch_thread = threading.Thread(target=batch_loop, daemon=True)
        batch_thread.start()

    monitor = Monitor(targets)
    monitor.start()
    log(f"{method}: making {len(targets)} changes, one every {args.gap} s ...")
    for t in targets:
        apply_change(source, t)
        t["committed"] = time.time()
        time.sleep(args.gap)
    wait = (2 * args.batch_interval + 30) if method == "batch" else args.max_wait
    deadline = time.time() + wait
    while time.time() < deadline and not monitor.all_seen():
        time.sleep(1)
    monitor.stop_flag.set()
    monitor.join()
    stop_batch.set()
    if batch_thread:
        batch_thread.join()
    for s in services:
        s.kill()

    # Work done: texts embedded and chunks (re)written, from the logs / batch outputs.
    embedded = written = 0
    for f in LOGS.glob("*.log"):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"embedded (\d+)", text):
            embedded += int(m[1])
        for m in re.finditer(r"\[(.*)\]$", text, re.M):
            written += sum(int(x) for x in re.findall(r"(\d+) chunk\(s\)", m[1]))
        for m in re.finditer(r"re-synced (\d+) rows", text):
            written += int(m[1])  # polling re-syncs every changed row (about one chunk each)
    for out in batch_outputs:
        embedded += sum(int(x) for x in re.findall(r"Embedded (\d+) new texts", out))
        written += sum(int(x) for x in re.findall(r"Wrote (\d+) chunks", out))

    for t in reversed(targets):
        undo_change(source, t)
    source.close()
    result = summarise(method, targets, embedded, written)
    log(f"{method}: visible {result['visible']}/{result['changes']}, "
        f"missed {result['missed_total']}, p50 {result['staleness_p50_s']} s")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-type", type=int, default=6, help="changes per change type")
    parser.add_argument("--gap", type=float, default=3.0, help="seconds between changes")
    parser.add_argument("--batch-interval", type=float, default=60.0,
                        help="seconds between batch rebuilds (stands in for 'nightly')")
    parser.add_argument("--max-wait", type=float, default=60.0,
                        help="seconds to wait after the last change (CDC, polling)")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--methods", default="cdc,polling,batch")
    args = parser.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(dsn("SOURCE_DB"), autocommit=True) as s, \
            psycopg.connect(dsn("INDEX_DB"), autocommit=True) as i:
        source_before = prints(s, SOURCE_PRINTS)
        if s.execute("SELECT active FROM pg_replication_slots WHERE slot_name = 'liverag_slot' "
                     "AND active").fetchone():
            raise SystemExit("The CDC reader is running: stop it (Ctrl+C) first.")
    results = []
    try:
        for method in args.methods.split(","):
            results.append(run_method(method, args, args.seed))
    finally:
        log("Restoring: bootstrap rebuilds the index and recreates the slot ...")
        run_indexer_module("indexer.bootstrap", "--force")

    with psycopg.connect(dsn("SOURCE_DB")) as s, psycopg.connect(dsn("INDEX_DB")) as i:
        restored = prints(s, SOURCE_PRINTS) == source_before
        consistent = prints(i, INDEX_PRINTS) == prints(s, SOURCE_PRINTS)

    cols = ["method", "visible", "missed_total", "staleness_p50_s", "staleness_p95_s",
            "staleness_max_s", "leak_window_p95_s", "permission_changes_never_applied",
            "texts_embedded", "chunks_written"]
    print("\n" + " | ".join(cols))
    for r in results:
        print(" | ".join(str(r[c]) for c in cols))
    print("\nMissed changes by type:")
    for r in results:
        print(f"  {r['method']:<8} {r['missed_by_type']}")
    print(f"\nSource restored: {restored}   Index matches source after bootstrap: {consistent}")
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "args": vars(args),
        "results": results, "source_restored": restored, "index_consistent_after": consistent,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {RESULTS.relative_to(HERE)}")


if __name__ == "__main__":
    main()