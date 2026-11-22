"""Real dataset: The Great Outdoors Stack Exchange (outdoors.stackexchange.com), CC BY-SA 4.0.

Turns the Stack Exchange XML dump into LiveRAG source-database rows, next to the Northwind Gear
data: each answered question becomes one `documents` row (question + its best answers). The real
edit history (PostHistory) is replayed later as live changes through CDC.

  uv run python -m simulator.outdoors prepare   # parse, analyse, clean -> data/outdoors/prepared
  uv run python -m simulator.outdoors seed      # load Northwind Gear + Outdoors threads

The raw dump goes in data/outdoors/raw (git-ignored), from
https://archive.org/download/stackexchange/outdoors.stackexchange.com.7z
"""

import argparse
import collections
import html
import json
import re
import statistics
import sys
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from defusedxml.ElementTree import iterparse

from simulator.catalog import GROUPS, stable_id

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "outdoors" / "raw"
PREPARED = ROOT / "data" / "outdoors" / "prepared"

MAX_ANSWERS = 3          # accepted answer first, then the highest-scoring ones
MIN_ANSWER_SCORE = 0     # answers voted below zero are left out
REPLAY_REVISIONS = 150   # the most recent real edits, replayed as live changes
# The public dump has no permissions. To exercise access control on real text, threads with
# these tags are treated as staff-reviewed medical advice, readable by support and managers.
RESTRICTED_TAGS = {"first-aid", "health"}
RESTRICTED_GROUPS = ["support", "managers"]


# ------------------------------------------------------------------ parsing
def rows(name: str) -> Iterator[dict[str, str]]:
    """Stream <row .../> elements; defusedxml refuses entity tricks in untrusted XML."""
    for _, el in iterparse(str(RAW / name)):
        if el.tag == "row":
            yield dict(el.attrib)
            el.clear()


class _Text(HTMLParser):
    """Post bodies are HTML: keep the text, turn blocks into lines and list items into '- '."""

    BLOCKS = frozenset({"p", "div", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.BLOCKS or tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        self.out.append(data)


def tidy(text: str) -> str:
    # A line starting with '#' would look like a section heading to the chunker.
    lines = [re.sub(r"^\s*#+\s*", "", line).rstrip() for line in text.replace("\r", "").split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def html_to_text(body: str) -> str:
    parser = _Text()
    parser.feed(body or "")
    return tidy("".join(parser.out))


def markdown_to_text(md: str) -> str:
    """PostHistory revisions are Markdown source: drop link targets, images and markup."""
    md = re.sub(r"<!--.*?-->", "", md or "", flags=re.DOTALL)
    md = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", md)          # images -> alt text
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)           # [text](url) -> text
    md = re.sub(r"\[([^\]]+)\]\[\d+\]", r"\1", md)             # [text][1] -> text
    md = re.sub(r"^\s*\[\d+\]:\s*\S+.*$", "", md, flags=re.MULTILINE)  # reference-link targets
    md = re.sub(r"<[^>]+>", "", md)                            # inline HTML tags
    md = re.sub(r"(\*\*|__)(.+?)\1", r"\2", md)
    md = re.sub(r"^\s*>\s?", "", md, flags=re.MULTILINE)               # quote markers
    return tidy(html.unescape(md))


def parse_tags(raw: str) -> list[str]:
    """Tags appear as '|a|b|' (newer dumps) or '<a><b>' (older revisions)."""
    return sorted(t for t in re.split(r"[|<>]", raw or "") if t)


def thread_body(question: str, answers: list[dict[str, Any]]) -> str:
    parts = [f"## Question\n\n{question}"]
    for i, ans in enumerate(answers, start=1):
        label = "accepted, " if ans["accepted"] else ""
        parts.append(f"## Answer {i} ({label}score {ans['score']})\n\n{ans['text']}")
    return "\n\n".join(parts)


def doc_id(post_id: str) -> str:
    return str(stable_id("document", f"outdoors:{post_id}"))


# ------------------------------------------------------------------ prepare
def prepare() -> None:
    PREPARED.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {"source": "outdoors.stackexchange.com dump (archive.org, 2024-04)"}

    posts = list(rows("Posts.xml"))
    questions = {p["Id"]: p for p in posts if p["PostTypeId"] == "1"}
    answers = [p for p in posts if p["PostTypeId"] == "2"]
    stats["raw"] = {
        "posts": len(posts), "questions": len(questions), "answers": len(answers),
        "other_posts": len(posts) - len(questions) - len(answers),
        "users": sum(1 for _ in rows("Users.xml")),
        "missing_body": sum(not p.get("Body") for p in posts),
        "missing_owner": sum("OwnerUserId" not in p for p in posts),
        "questions_without_answer": sum(int(q.get("AnswerCount", 0)) == 0 for q in questions.values()),
        "questions_closed": sum("ClosedDate" in q for q in questions.values()),
        "bodies_with_html": sum("<" in p.get("Body", "") for p in posts),
        "body_words_median": statistics.median(len(p["Body"].split()) for p in posts if p.get("Body")),
        "question_score_median": statistics.median(int(q["Score"]) for q in questions.values()),
        "questions_by_year": dict(sorted(collections.Counter(
            q["CreationDate"][:4] for q in questions.values()).items())),
    }
    tag_counts = collections.Counter(t for q in questions.values() for t in parse_tags(q.get("Tags", "")))
    stats["raw"]["distinct_tags"] = len(tag_counts)
    stats["raw"]["top_tags"] = tag_counts.most_common(15)

    # Duplicate links: the closed duplicate is left out of the corpus; its title becomes a real
    # test query whose correct answer is the original thread.
    dup_pairs = [(link["PostId"], link["RelatedPostId"]) for link in rows("PostLinks.xml")
                 if link["LinkTypeId"] == "3" and link["PostId"] in questions
                 and link["RelatedPostId"] in questions]
    duplicates = {d for d, _ in dup_pairs}

    # Full revision history of every question: titles (1, 4), bodies (2, 5), tags (3, 6).
    history: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    kinds = {"1": "title", "4": "title", "2": "body", "5": "body", "3": "tags", "6": "tags"}
    for r in rows("PostHistory.xml"):
        kind = kinds.get(r["PostHistoryTypeId"])
        if kind and r["PostId"] in questions and "Text" in r:
            history[r["PostId"]].append({"kind": kind, "at": r["CreationDate"], "rev": r["RevisionGUID"],
                                         "text": r["Text"]})
    for revs in history.values():
        revs.sort(key=lambda r: r["at"])

    by_question: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for a in answers:
        if a.get("Body") and int(a["Score"]) >= MIN_ANSWER_SCORE and a.get("ParentId") in questions:
            by_question[a["ParentId"]].append(a)

    dropped = collections.Counter()
    threads: list[dict[str, Any]] = []
    for qid, q in questions.items():
        if qid in duplicates:
            dropped["closed_as_duplicate"] += 1
            continue
        accepted = q.get("AcceptedAnswerId")
        cands = sorted(by_question.get(qid, []),
                       key=lambda a: (a["Id"] != accepted, -int(a["Score"]), a["Id"]))[:MAX_ANSWERS]
        cands = [{"text": html_to_text(a["Body"]), "score": int(a["Score"]), "accepted": a["Id"] == accepted}
                 for a in cands]
        cands = [a for a in cands if a["text"]]
        if not cands:
            dropped["no_usable_answer"] += 1
            continue
        revs = history.get(qid, [])
        body_revs = [r for r in revs if r["kind"] == "body"]
        question_text = markdown_to_text(body_revs[-1]["text"]) if body_revs else html_to_text(q["Body"])
        if not question_text:
            dropped["empty_question"] += 1
            continue
        threads.append({"post_id": qid, "id": doc_id(qid), "title": html.unescape(q["Title"]).strip(),
                        "question": question_text, "answers": cands, "tags": parse_tags(q.get("Tags", "")),
                        "score": int(q["Score"]), "created": q["CreationDate"]})

    # Titles must be unique: retrieval tests identify a thread by its title.
    seen: collections.Counter = collections.Counter()
    for t in sorted(threads, key=lambda t: int(t["post_id"])):
        seen[t["title"].lower()] += 1
        if seen[t["title"].lower()] > 1:
            t["title"] = f"{t['title']} ({seen[t['title'].lower()]})"
            dropped["titles_made_unique"] += 1

    # Replay: the most recent real edits to these threads, grouped by revision (one edit session
    # that changed title, body and tags together is one source transaction).
    kept = {t["post_id"]: t for t in threads}
    edits = [dict(r, post_id=qid) for qid, revs in history.items() if qid in kept
             for r in revs if r is not revs[0] and not _is_initial(r, revs)]
    sessions: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for e in edits:
        sessions[e["rev"]].append(e)
    ordered = sorted(sessions.values(), key=lambda s: s[0]["at"])[-REPLAY_REVISIONS:]
    replay = []
    first_replayed: dict[str, str] = {}
    for session in ordered:
        qid = session[0]["post_id"]
        first_replayed.setdefault(qid, session[0]["at"])
        replay.append({"post_id": qid, "at": session[0]["at"],
                       "changes": {e["kind"]: _clean(e["kind"], e["text"]) for e in session}})
    # Each replayed thread starts in the state it had just before its first replayed edit.
    for qid, start in first_replayed.items():
        before = [r for r in history[qid] if r["at"] < start]
        t = kept[qid]
        t["initial"] = {kind: _clean(kind, next(r["text"] for r in reversed(before) if r["kind"] == kind))
                        for kind in ("title", "body", "tags") if any(r["kind"] == kind for r in before)}

    for t in threads:
        t["restricted"] = bool(RESTRICTED_TAGS & set(t["tags"]))

    golden = []
    for n, (dup, orig) in enumerate(sorted(dup_pairs, key=lambda p: int(p[0])), start=1):
        if orig not in kept:
            continue
        target = kept[orig]
        query = html.unescape(questions[dup]["Title"]).strip()
        base = {"question": query, "title": target["title"], "source": f"duplicate of post {orig}"}
        if target["restricted"]:
            golden.append({"id": f"o{n:03d}a", "type": "answerable", "as": "agent@northwind.test", **base})
            golden.append({"id": f"o{n:03d}f", "type": "forbidden", "as": "customer@example.com", **base})
        else:
            golden.append({"id": f"o{n:03d}", "type": "answerable", "as": "customer@example.com", **base})

    stats["preprocessing"] = {
        "threads_kept": len(threads), "dropped": dict(dropped),
        "answers_kept": sum(len(t["answers"]) for t in threads),
        "restricted_threads": sum(t["restricted"] for t in threads),
        "duplicate_pairs": len(dup_pairs), "golden_questions": len(golden),
        "replay_sessions": len(replay), "replayed_threads": len(first_replayed),
        "replay_change_kinds": dict(collections.Counter(k for s in replay for k in s["changes"])),
        "thread_words_median": statistics.median(
            len(thread_body(t["question"], t["answers"]).split()) for t in threads),
    }
    _write_jsonl(PREPARED / "threads.jsonl", threads)
    _write_jsonl(PREPARED / "replay.jsonl", replay)
    _write_jsonl(PREPARED / "golden_outdoors.jsonl", golden)
    (PREPARED / "stats.json").write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=2))


def _is_initial(rev: dict[str, str], revs: list[dict[str, str]]) -> bool:
    """The revision that created the post (same GUID as the first revision) is not an edit."""
    return rev["rev"] == revs[0]["rev"]


def _clean(kind: str, text: str) -> Any:
    if kind == "title":
        return html.unescape(text).strip()
    if kind == "tags":
        return parse_tags(text)
    return markdown_to_text(text)


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def load_threads() -> list[dict[str, Any]]:
    with (PREPARED / "threads.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def thread_rows(t: dict[str, Any], state: dict[str, Any] | None = None) -> tuple[tuple, list[tuple]]:
    """(documents row, document_acl rows) for a thread in a given state (default: its initial one)."""
    state = state if state is not None else t.get("initial", {})
    title = state.get("title", t["title"])
    question = state.get("body", t["question"])
    tags = state.get("tags", t["tags"])
    restricted = bool(RESTRICTED_TAGS & set(tags))
    row = (t["id"], "article", title, thread_body(question, t["answers"]), not restricted)
    acl = [(t["id"], GROUPS[g]) for g in RESTRICTED_GROUPS] if restricted else []
    return row, acl


# ------------------------------------------------------------------ seed
def seed() -> None:
    import os

    import psycopg
    from argon2 import PasswordHasher

    from simulator import catalog
    from simulator.seed import source_dsn

    data = catalog.build()
    threads = load_threads()
    hasher = PasswordHasher()
    password = os.environ["DEMO_USER_PASSWORD"]
    documents = data.articles + data.policies
    memberships = [(u.id, catalog.GROUPS[g]) for u in data.users for g in u.groups]
    acl = [(d.id, catalog.GROUPS[g]) for d in documents for g in d.groups]
    thread_docs, thread_acl = [], []
    for t in threads:
        row, rows_acl = thread_rows(t)
        thread_docs.append(row)
        thread_acl += rows_acl

    with psycopg.connect(source_dsn()) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE user_groups, document_acl, documents, products, users")
        cur.executemany(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (%s, %s, %s, %s)",
            [(u.id, u.email, u.display_name, hasher.hash(password)) for u in data.users])
        cur.executemany("INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)", memberships)
        cur.executemany(
            "INSERT INTO products (id, sku, name, description, price_inr, status) VALUES (%s, %s, %s, %s, %s, %s)",
            [(p.id, p.sku, p.name, p.description, p.price_inr, p.status) for p in data.products])
        cur.executemany(
            "INSERT INTO documents (id, kind, title, body, is_public) VALUES (%s, %s, %s, %s, %s)",
            [(d.id, d.kind, d.title, d.body, d.is_public) for d in documents] + thread_docs)
        cur.executemany("INSERT INTO document_acl (document_id, group_id) VALUES (%s, %s)", acl + thread_acl)

    print(f"Loaded Northwind Gear ({len(data.products)} products, {len(documents)} documents) and "
          f"{len(thread_docs)} Outdoors threads ({len(thread_acl) // len(RESTRICTED_GROUPS)} restricted).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["prepare", "seed"])
    args = parser.parse_args()
    if not RAW.exists():
        sys.exit(f"Put the extracted dump in {RAW}")
    prepare() if args.command == "prepare" else seed()


if __name__ == "__main__":
    main()
