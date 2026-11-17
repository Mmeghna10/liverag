"""Measure the final answers end to end, through the running API (login -> /chat/stream -> validated answer).

Needs: both databases, Ollama and the API running.
Run from the evals folder:   uv run python -m evals.answers
Read-only: it only logs in and asks questions.
"""

import json
import os
import re
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from liverag_core.config import ROOT  # noqa: F401  (importing config loads .env)

HERE = Path(__file__).resolve().parent.parent
GOLDEN = HERE / "golden.jsonl"
RESULTS = HERE / "results" / "answers.json"
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8000")


def normalise(text: str) -> str:
    """Lower case, no ₹ and no thousands commas, so "₹12,999" and "12999" match."""
    return re.sub(r"(?<=\d),(?=\d)", "", text.lower().replace("₹", ""))


def has_fact(answer: str, fact: str) -> bool:
    """A fact is one or more alternatives separated by "|"; any one of them is enough."""
    text = normalise(answer)
    return any(normalise(option) in text for option in fact.split("|"))


def cites_expected(citations: list[dict[str, Any]], item: dict[str, Any]) -> bool:
    if "sku" in item:
        return any(f"({item['sku']})" in c["title"] for c in citations)
    # Document citation titles look like "Returns and refunds > Return windows".
    return any(c["title"].split(" > ")[0] == item["title"] for c in citations)


def ask(client: httpx.Client, token: str, question: str) -> dict[str, Any]:
    """Call the streaming endpoint (what the web UI uses) and return the final "done" event."""
    response = client.post("/api/v1/chat/stream", json={"question": question},
                           headers={"Authorization": f"Bearer {token}"})
    response.raise_for_status()
    done = None
    for block in response.text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if lines.get("event") == "error":
            raise RuntimeError(json.loads(lines["data"])["detail"])
        if lines.get("event") == "done":
            done = json.loads(lines["data"])
    if done is None:
        raise RuntimeError("stream ended without a 'done' event")
    return done


def main() -> None:
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]
    password = os.environ["DEMO_USER_PASSWORD"]
    rows: list[dict[str, Any]] = []

    with httpx.Client(base_url=API_URL, timeout=180) as client:
        tokens = {}
        for email in sorted({item["as"] for item in golden}):
            login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            login.raise_for_status()
            tokens[email] = login.json()["access_token"]

        ask(client, tokens[golden[0]["as"]], "warm-up")  # load the LLM into the GPU first
        print(f"Asking {len(golden)} questions through {API_URL} ...\n")

        for item in golden:
            started = time.perf_counter()
            result = ask(client, tokens[item["as"]], item["question"])
            seconds = time.perf_counter() - started
            facts = item.get("facts", [])
            found = [f for f in facts if has_fact(result["answer"], f)] if not result["abstained"] else []
            row = {
                "id": item["id"], "type": item["type"], "as": item["as"],
                "abstained": result["abstained"], "reason": result["reason"],
                "fact_ok": bool(facts) and len(found) == len(facts),
                "cite_ok": cites_expected(result["citations"], item) if "facts" in item else None,
                "leaked_fact": item["type"] == "forbidden" and bool(found),
                "seconds": round(seconds, 2), "answer": result["answer"],
                "citations": [c["title"] for c in result["citations"]],
            }
            if item["type"] == "forbidden":
                row["leaked_citation"] = row["cite_ok"]  # citing the forbidden document is a leak
            rows.append(row)
            mark = "refused" if row["abstained"] else ("OK " if row["fact_ok"] else "?? ")
            print(f"  {item['id']} {item['type']:<12} {mark:<8} {seconds:5.1f}s  {item['question'][:60]}")

    answerable = [r for r in rows if r["type"] == "answerable"]
    must_abstain = [r for r in rows if r["type"] != "answerable"]
    answered = [r for r in answerable if not r["abstained"]]
    n = len(answerable)
    leaks = [r for r in rows if r.get("leaked_fact") or r.get("leaked_citation")]
    seconds = sorted(r["seconds"] for r in rows)
    summary = {
        "answered": len(answered) / n,
        "correct_fact": sum(r["fact_ok"] for r in answerable) / n,
        "correct_citation": sum(bool(r["cite_ok"]) for r in answerable) / n,
        "fully_correct": sum(r["fact_ok"] and bool(r["cite_ok"]) for r in answerable) / n,
        "fact_ok_when_answered": sum(r["fact_ok"] for r in answered) / max(len(answered), 1),
        "abstained_when_required": sum(r["abstained"] for r in must_abstain) / len(must_abstain),
        "leaks": len(leaks),
        "p50_seconds": statistics.median(seconds),
        "p95_seconds": seconds[min(len(seconds) - 1, round(0.95 * (len(seconds) - 1)))],
    }

    print(f"\nAnswerable questions ({n})")
    print(f"  answered (not refused)          {summary['answered']:.2f}")
    print(f"  contains the expected fact      {summary['correct_fact']:.2f}")
    print(f"  cites the expected source       {summary['correct_citation']:.2f}")
    print(f"  fully correct (fact + citation) {summary['fully_correct']:.2f}")
    print(f"  fact correct, when it answered  {summary['fact_ok_when_answered']:.2f}")
    print(f"\nMust-abstain questions ({len(must_abstain)})")
    print(f"  refused                         {summary['abstained_when_required']:.2f}")
    print(f"\nAnswer leaks: {len(leaks)}  (must be 0)")
    for r in leaks:
        print("  LEAK", r["id"], r["answer"])
    print(f"\nLatency per question: p50 {summary['p50_seconds']:.1f} s, p95 {summary['p95_seconds']:.1f} s")

    problems = [r for r in answerable if not (r["fact_ok"] and r["cite_ok"])]
    if problems:
        print("\nAnswerable but not fully correct:")
        for r in problems:
            what = f"refused ({r['reason']})" if r["abstained"] else (
                f"fact={'ok' if r['fact_ok'] else 'missing'} cite={'ok' if r['cite_ok'] else 'wrong'}")
            print(f"  {r['id']} {what}: {r['answer'][:110]}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "api": API_URL,
        "summary": summary, "rows": rows,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved {RESULTS.relative_to(HERE)}")
    if leaks:
        raise SystemExit(1)


if __name__ == "__main__":
    main()