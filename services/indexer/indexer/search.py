"""Command-line search over the LiveRAG index, as a given user.

Run from services/indexer:
    uv run python -m indexer.search "How much is the Alpine 2 tent?"
    uv run python -m indexer.search "discount on a damaged tent" --as agent@northwind.test
    uv run python -m indexer.search "TNT-0025" --mode hybrid
"""

import argparse
from typing import Any

import psycopg
from liverag_core.config import dsn
from liverag_core.retrieval import MODES, Retriever, user_groups

GROUP_NAMES = {1: "support", 2: "sales", 3: "managers", 4: "hr", 5: "admins"}


def user_id_for(email: str) -> Any:
    with psycopg.connect(dsn("SOURCE_DB")) as source:
        row = source.execute(
            "SELECT id FROM users WHERE email = %s AND is_active", (email,)
        ).fetchone()
    if row is None:
        raise SystemExit(f"No active user with email {email}")
    return row[0]


def label(hit: dict[str, Any]) -> str:
    meta = hit["metadata"]
    if hit["source_table"] == "products":
        return f"product  {meta['sku']}"
    heading = f" > {meta['heading']}" if meta["heading"] else ""
    return f"{meta['kind']:<8} {meta['title']}{heading}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the LiveRAG index.")
    parser.add_argument("query")
    parser.add_argument("--as", dest="email", help="search as this user (default: anonymous)")
    parser.add_argument("-k", type=int, default=6, help="number of results (default 6)")
    parser.add_argument("--mode", choices=MODES, default="rerank")
    args = parser.parse_args()

    with psycopg.connect(dsn("INDEX_DB")) as conn:
        # Anonymous visitors see public data only; a user also sees their groups' documents.
        groups = user_groups(conn, user_id_for(args.email)) if args.email else []
        hits = Retriever().search(conn, args.query, groups, args.k, args.mode)

    names = ", ".join(GROUP_NAMES.get(g, str(g)) for g in groups) or "none"
    print(f'\n"{args.query}"  as {args.email or "anonymous"}  (groups: {names})  '
          f"mode: {args.mode}\n")
    shown = {"rerank": ["vec", "kw", "rrf"], "hybrid": ["vec", "kw"],
             "vector": ["vec"], "keyword": ["kw"]}[args.mode]
    for rank, hit in enumerate(hits, start=1):
        # Where the chunk ranked in each search; "-" means that search did not find it.
        ranks = "  ".join(f"{name}#{hit['ranks'].get(name, '-')}" for name in shown)
        score = hit["rerank"] if args.mode == "rerank" else hit["rrf"]
        snippet = " ".join(hit["text"].split())[:80]
        print(f"{rank}. {score:.4f}  [{ranks}]  {label(hit)}")
        print(f"   {snippet}")
    if not hits:
        print("No results.")


if __name__ == "__main__":
    main()