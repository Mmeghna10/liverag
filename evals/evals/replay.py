"""Replay real edits from The Great Outdoors Stack Exchange through the live CDC pipeline.

Run from the evals folder after `simulator.outdoors seed` and `indexer.bootstrap --force`
(Docker up; reader, indexer and API NOT running; this script starts and stops them):
  uv run python -m evals.replay
  uv run python -m evals.replay --gap 0.5

The 150 most recent real edit sessions (title, body and tag edits made by the community) are
applied to the source database in their original order, one source transaction per session.
A tag edit that adds or removes a restricted tag changes the thread's access rule, so some
sessions are real permission changes. For every session it records when the index reflects it:
  staleness = seconds from the source commit until source_state shows the session applied
At the end it checks that the index matches the source (fingerprints), and that the streamed
index is identical to a fresh batch rebuild of the same source state.
Writes results/outdoors_replay.json.
"""

import argparse
import json
import statistics
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from liverag_core.config import ROOT, dsn

from evals.baselines import run_indexer_module
from evals.chaos import (
    CHUNKS_PRINT,
    INDEX_PRINTS,
    LOGS,
    SOURCE_PRINTS,
    Service,
    drop_slot,
    prints,
    wait_caught_up,
)

HERE = Path(__file__).resolve().parent.parent
PREPARED = ROOT / "data" / "outdoors" / "prepared"
RESULTS = HERE / "results" / "outdoors_replay.json"
# Must match simulator/outdoors.py (same rule, same body layout).
RESTRICTED_TAGS = {"first-aid", "health"}
RESTRICTED_GROUP_IDS = [1, 3]  # support, managers


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def load(name: str) -> list[dict[str, Any]]:
    with (PREPARED / name).open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def thread_body(question: str, answers: list[dict[str, Any]]) -> str:
    parts = [f"## Question\n\n{question}"]
    for i, ans in enumerate(answers, start=1):
        label = "accepted, " if ans["accepted"] else ""
        parts.append(f"## Answer {i} ({label}score {ans['score']})\n\n{ans['text']}")
    return "\n\n".join(parts)


def apply_session(source: psycopg.Connection, thread: dict[str, Any], state: dict[str, Any],
                  changes: dict[str, Any]) -> dict[str, Any]:
    """Apply one real edit session as one source transaction; return what to wait for."""
    was_restricted = bool(RESTRICTED_TAGS & set(state["tags"]))
    state.update(changes)
    restricted = bool(RESTRICTED_TAGS & set(state["tags"]))
    with source.transaction():
        # Anything this transaction writes lands after this WAL position.
        before = source.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
        source.execute("UPDATE documents SET title = %s, body = %s, is_public = %s WHERE id = %s",
                       (state["title"], thread_body(state["body"], thread["answers"]), not restricted,
                        thread["id"]))
        if restricted and not was_restricted:
            for g in RESTRICTED_GROUP_IDS:
                source.execute("INSERT INTO document_acl (document_id, group_id) VALUES (%s, %s)",
                               (thread["id"], g))
        elif was_restricted and not restricted:
            source.execute("DELETE FROM document_acl WHERE document_id = %s", (thread["id"],))
    return {"id": thread["id"], "after_lsn": before, "committed": time.time(),
            "kinds": sorted(changes), "permission_change": restricted != was_restricted}


class Monitor(threading.Thread):
    """Checks pending sessions 4 times a second: applied once source_state passed the commit."""

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__(daemon=True)
        self.sessions, self.stop_flag = sessions, threading.Event()

    def run(self) -> None:
        with psycopg.connect(dsn("INDEX_DB"), autocommit=True) as index:
            while not self.stop_flag.is_set():
                now = time.time()
                for s in list(self.sessions):
                    if "seen" in s:
                        continue
                    row = index.execute(
                        "SELECT last_lsn > %s::pg_lsn FROM source_state "
                        "WHERE source_table = 'documents' AND source_id = %s",
                        (s["after_lsn"], s["id"])).fetchone()
                    if row and row[0]:
                        s["seen"] = now
                time.sleep(0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gap", type=float, default=1.0, help="seconds between edit sessions")
    args = parser.parse_args()

    threads = {t["post_id"]: t for t in load("threads.jsonl")}
    replay = load("replay.jsonl")
    states = {qid: {"title": t.get("initial", {}).get("title", t["title"]),
                    "body": t.get("initial", {}).get("body", t["question"]),
                    "tags": t.get("initial", {}).get("tags", t["tags"])}
              for qid, t in threads.items()}

    LOGS.mkdir(parents=True, exist_ok=True)
    for old in LOGS.glob("*.log"):
        old.unlink()
    source = psycopg.connect(dsn("SOURCE_DB"), autocommit=True)
    index = psycopg.connect(dsn("INDEX_DB"), autocommit=True)
    if prints(source, SOURCE_PRINTS) != prints(index, INDEX_PRINTS):
        raise SystemExit("The index does not match the source before the replay: run the bootstrap.")
    log(f"Before: index matches source. Replaying {len(replay)} real edit sessions.")

    reader = Service("reader", "cdc", "cdc.reader")
    indexer = Service("indexer", "indexer", "indexer.stream")
    sessions: list[dict[str, Any]] = []
    monitor = Monitor(sessions)
    try:
        reader.start()
        indexer.start()
        monitor.start()
        for i, s in enumerate(replay, start=1):
            sessions.append(apply_session(source, threads[s["post_id"]], states[s["post_id"]],
                                          s["changes"]))
            if i % 25 == 0:
                log(f"  {i} sessions applied")
            time.sleep(args.gap)
        catch_up = wait_caught_up(source, index, "127.0.0.1:19092")
        deadline = time.time() + 30
        while any("seen" not in s for s in sessions) and time.time() < deadline:
            time.sleep(0.5)
        monitor.stop_flag.set()
        log(f"Pipeline caught up {catch_up:.1f} s after the last session.")
    finally:
        for service in (reader, indexer):
            service.kill()

    index_matches = prints(index, INDEX_PRINTS) == prints(source, SOURCE_PRINTS)
    streamed = index.execute(CHUNKS_PRINT).fetchone()[0]
    streamed_chunks = index.execute("SELECT count(*) FROM chunks_v1").fetchone()[0]
    log("Rebuilding the index with the batch indexer to compare...")
    run_indexer_module("indexer.batch")
    rebuilt = index.execute(CHUNKS_PRINT).fetchone()[0]
    drop_slot(source)
    log("Recreating the slot with the bootstrap (so CDC can start again later)...")
    run_indexer_module("indexer.bootstrap", "--force")

    lag = sorted(s["seen"] - s["committed"] for s in sessions if "seen" in s)
    perm = sorted(s["seen"] - s["committed"] for s in sessions if s["permission_change"] and "seen" in s)
    pct = lambda xs, p: xs[min(len(xs) - 1, round(p * (len(xs) - 1)))]
    summary = {
        "sessions": len(sessions), "threads_edited": len({s["id"] for s in sessions}),
        "change_kinds": {k: sum(k in s["kinds"] for s in sessions) for k in ("title", "body", "tags")},
        "permission_changes": sum(s["permission_change"] for s in sessions),
        "visible": len(lag), "missed": len(sessions) - len(lag),
        "staleness_p50_s": round(statistics.median(lag), 2),
        "staleness_p95_s": round(pct(lag, 0.95), 2),
        "staleness_max_s": round(lag[-1], 2),
        "permission_staleness_max_s": round(perm[-1], 2) if perm else None,
        "catch_up_s": round(catch_up, 1),
        "index_equals_source": index_matches,
        "streamed_equals_batch_rebuild": streamed == rebuilt,
        "chunks_in_index": streamed_chunks,
    }
    RESULTS.write_text(json.dumps({"run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                   "args": vars(args), "summary": summary}, indent=2) + "\n",
                       encoding="utf-8")
    log(json.dumps(summary, indent=2))
    if not (index_matches and streamed == rebuilt and not summary["missed"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
