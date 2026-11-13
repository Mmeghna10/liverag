"""Command-line vector search over chunks_v1, filtered by what the user is allowed to read.

Run from services/indexer:
    uv run python -m indexer.search "How much is the Alpine 2 tent?"
    uv run python -m indexer.search "discount on a damaged tent" --as agent@northwind.test
"""

import argparse
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from indexer.batch import dsn
from indexer.embedder import Embedder

GROUP_NAMES = {1: "support", 2: "sales", 3: "managers", 4: "hr", 5: "admins"}

# The permission filter sits INSIDE the query, next to the vector ordering:
# a chunk the user may not read is never a candidate, so it cannot leak through scores or ranks.
# The inner query may come back slightly out of order (see relaxed_order below), so the outer
# query sorts the final rows by distance.
SEARCH_SQL = """
    SELECT * FROM (
        SELECT id, source_table, text, metadata,
               embedding <=> %(query)s AS distance
        FROM chunks_v1
        WHERE is_public OR allowed_group_ids && %(groups)s::int[]
        ORDER BY embedding <=> %(query)s
        LIMIT %(k)s
    ) AS hits
    ORDER BY distance
"""


def groups_for(email: str | None) -> list[int]:
    """Anonymous visitors see public data only; a user also sees their groups' documents."""
    if email is None:
        return []
    with psycopg.connect(dsn("SOURCE_DB")) as source:
        row = source.execute(
            "SELECT id FROM users WHERE email = %s AND is_active", (email,)
        ).fetchone()
    if row is None:
        raise SystemExit(f"No active user with email {email}")
    # Groups come from the index DB's replica, the same place the API will read them at query time.
    with psycopg.connect(dsn("INDEX_DB")) as index:
        rows = index.execute(
            "SELECT group_id FROM user_groups_replica WHERE user_id = %s ORDER BY group_id",
            (row[0],),
        ).fetchall()
    return [group_id for (group_id,) in rows]


def search(query: str, groups: list[int], k: int) -> list[dict[str, Any]]:
    vector = Embedder().embed_query(query)
    with psycopg.connect(dsn("INDEX_DB"), row_factory=dict_row) as conn:
        register_vector(conn)
        # With a filter, HNSW may run out of candidates before it finds k allowed rows.
        # Iterative scan (pgvector 0.8+) lets it keep walking the graph until it has enough.
        conn.execute("SET hnsw.iterative_scan = relaxed_order")
        return conn.execute(SEARCH_SQL, {"query": vector, "groups": groups, "k": k}).fetchall()


def label(hit: dict[str, Any]) -> str:
    meta = hit["metadata"]
    if hit["source_table"] == "products":
        return f"product  {meta['sku']}"
    heading = f" > {meta['heading']}" if meta["heading"] else ""
    return f"{meta['kind']:<8} {meta['title']}{heading}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Vector search over the LiveRAG index.")
    parser.add_argument("query")
    parser.add_argument("--as", dest="email", help="search as this user (default: anonymous)")
    parser.add_argument("-k", type=int, default=6, help="number of results (default 6)")
    args = parser.parse_args()

    groups = groups_for(args.email)
    hits = search(args.query, groups, args.k)

    names = ", ".join(GROUP_NAMES.get(g, str(g)) for g in groups) or "none"
    print(f'\n"{args.query}"  as {args.email or "anonymous"}  (groups: {names})\n')
    for rank, hit in enumerate(hits, start=1):
        snippet = " ".join(hit["text"].split())[:90]
        print(f"{rank}. {hit['distance']:.3f}  {label(hit)}")
        print(f"   {snippet}")
    if not hits:
        print("No results.")


if __name__ == "__main__":
    main()