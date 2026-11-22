"""Measure retrieval, abstention and permission leaks on the golden set. No LLM involved.

Run from the evals folder:   uv run python -m evals.retrieval
Real dataset (Outdoors Stack Exchange duplicates):
  uv run python -m evals.retrieval --golden ../data/outdoors/prepared/golden_outdoors.jsonl       --results results/retrieval_outdoors.json
Read-only: it only runs SELECT queries against both databases.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from liverag_core.config import dsn
from liverag_core.retrieval import MODES, Retriever, user_groups

HERE = Path(__file__).resolve().parent.parent
GOLDEN = HERE / "golden.jsonl"
RESULTS = HERE / "results" / "retrieval.json"
K = 5  # the chat endpoint gives the LLM the top 5 chunks, so we measure the top 5
THRESHOLDS = [0.001, 0.01, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9]


def load_golden(path: Path = GOLDEN) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def groups_by_email(emails: set[str]) -> dict[str, list[int]]:
    """Look up each test user's groups the same way the API does."""
    with psycopg.connect(dsn("SOURCE_DB")) as source:
        ids = dict(source.execute("SELECT email, id FROM users WHERE email = ANY(%s)",
                                  (list(emails),)).fetchall())
    missing = emails - ids.keys()
    if missing:
        raise SystemExit(f"Golden set names users that do not exist: {sorted(missing)}")
    with psycopg.connect(dsn("INDEX_DB")) as index:
        return {email: user_groups(index, user_id) for email, user_id in ids.items()}


def is_expected(hit: dict[str, Any], item: dict[str, Any]) -> bool:
    """Is this chunk from the source record the golden item points at?"""
    meta = hit["metadata"]
    if "sku" in item:
        return meta.get("sku") == item["sku"]
    if "title" in item:
        return meta.get("title") == item["title"]
    return False  # unanswerable questions have no expected source


def find_leaks(conn: psycopg.Connection, hits: list[dict[str, Any]], groups: list[int]) -> list[str]:
    """Independent check, straight from the table: may this user read every chunk they were given?"""
    if not hits:
        return []
    rows = conn.execute("SELECT id, allowed_group_ids FROM chunks_v1 WHERE id = ANY(%s)",
                        ([hit["id"] for hit in hits],)).fetchall()
    return [str(chunk_id) for chunk_id, allowed in rows if allowed and not set(allowed) & set(groups)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="question set (JSON lines)")
    parser.add_argument("--results", type=Path, default=RESULTS, help="where to write the results")
    args = parser.parse_args()
    results = args.results.resolve()
    golden = load_golden(args.golden)
    groups = groups_by_email({item["as"] for item in golden})
    answerable = [item for item in golden if item["type"] == "answerable"]
    must_abstain = [item for item in golden if item["type"] != "answerable"]
    print(f"Golden set: {len(golden)} questions "
          f"({len(answerable)} answerable, {len(must_abstain)} must abstain)\n")

    retriever = Retriever()
    summary: dict[str, Any] = {}
    details: list[dict[str, Any]] = []
    leaks: list[dict[str, Any]] = []
    top_scores: dict[str, float] = {}  # reranker score of the best chunk, per question

    with psycopg.connect(dsn("INDEX_DB")) as conn:
        for mode in MODES:
            ranks: list[int | None] = []
            started = time.perf_counter()
            for item in golden:
                hits = retriever.search(conn, item["question"], groups[item["as"]], K, mode)

                leaked = find_leaks(conn, hits, groups[item["as"]])
                if leaked:
                    leaks.append({"id": item["id"], "mode": mode, "chunks": leaked})
                # A forbidden document must never appear, whatever its rank or score.
                if item["type"] == "forbidden" and any(is_expected(hit, item) for hit in hits):
                    leaks.append({"id": item["id"], "mode": mode, "forbidden_title": item["title"]})

                rank = next((i for i, hit in enumerate(hits, start=1) if is_expected(hit, item)), None)
                if item["type"] == "answerable":
                    ranks.append(rank)
                if mode == "rerank":
                    top_scores[item["id"]] = hits[0]["rerank"] if hits else 0.0
                details.append({"id": item["id"], "mode": mode, "rank": rank,
                                "top": hits[0]["metadata"].get("title") or hits[0]["metadata"].get("sku")
                                if hits else None})

            n = len(ranks)
            summary[mode] = {
                "hit@1": sum(r == 1 for r in ranks) / n,
                f"hit@{K}": sum(r is not None for r in ranks) / n,
                "mrr": sum(1 / r for r in ranks if r) / n,
                "ms_per_query": round((time.perf_counter() - started) * 1000 / len(golden)),
            }

    print(f"Retrieval on {len(answerable)} answerable questions (top {K})")
    print(f"{'mode':<9}{'hit@1':>8}{'hit@' + str(K):>8}{'MRR':>8}{'ms/query':>10}")
    for mode, m in summary.items():
        print(f"{mode:<9}{m['hit@1']:>8.2f}{m[f'hit@{K}']:>8.2f}{m['mrr']:>8.2f}{m['ms_per_query']:>10}")

    misses = [d for d in details if d["mode"] == "rerank" and d["rank"] != 1
              and any(i["id"] == d["id"] and i["type"] == "answerable" for i in golden)]
    if misses:
        print("\nRerank mode, expected source not at rank 1:")
        by_id = {item["id"]: item for item in golden}
        for d in misses:
            print(f"  {d['id']} rank={d['rank']}  {by_id[d['id']]['question']}  (top: {d['top']})")

    # Abstention: the API refuses when the best reranker score is below a threshold.
    print("\nAbstention threshold sweep (reranker score of the best chunk)")
    print(f"{'threshold':>10}{'wrongly refused':>18}{'wrongly answered':>18}")
    sweep = []
    for t in THRESHOLDS:
        refused = sum(top_scores[i["id"]] < t for i in answerable)
        answered = sum(top_scores[i["id"]] >= t for i in must_abstain)
        sweep.append({"threshold": t, "wrongly_refused": refused, "wrongly_answered": answered})
        print(f"{t:>10}{refused:>12}/{len(answerable):<5}{answered:>12}/{len(must_abstain):<5}")
    lowest_ok = min(top_scores[i["id"]] for i in answerable)
    highest_bad = max(top_scores[i["id"]] for i in must_abstain)
    print(f"\nLowest score of an answerable question:   {lowest_ok:.4f}")
    print(f"Highest score of a must-abstain question: {highest_bad:.4f}")

    print(f"\nPermission leaks: {len(leaks)}  (must be 0)")
    for leak in leaks:
        print("  LEAK", leak)

    results.parent.mkdir(exist_ok=True)
    results.write_text(json.dumps({
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "questions": len(golden), "k": K, "summary": summary, "threshold_sweep": sweep,
        "lowest_answerable_score": lowest_ok, "highest_must_abstain_score": highest_bad,
        "top_scores": top_scores, "leaks": leaks, "details": details,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved {results}")
    if leaks:
        raise SystemExit(1)  # a leak fails the run, so CI can catch it later


if __name__ == "__main__":
    main()