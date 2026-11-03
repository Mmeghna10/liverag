"""Reset the source DB and load Northwind Gear's deterministic demo data.

Run from services/simulator:  uv run python -m simulator.seed
"""

import os
from pathlib import Path

import psycopg
from argon2 import PasswordHasher
from dotenv import load_dotenv

from simulator import catalog

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


def source_dsn() -> str:
    return (
        f"host=localhost port={os.environ['SOURCE_DB_PORT']} "
        f"dbname={os.environ['SOURCE_DB_NAME']} "
        f"user={os.environ['SOURCE_DB_USER']} "
        f"password={os.environ['SOURCE_DB_PASSWORD']}"
    )


def main() -> None:
    data = catalog.build()
    hasher = PasswordHasher()
    password = os.environ["DEMO_USER_PASSWORD"]
    documents = data.articles + data.policies
    memberships = [(u.id, catalog.GROUPS[g]) for u in data.users for g in u.groups]
    acl = [(d.id, catalog.GROUPS[g]) for d in documents for g in d.groups]

    # One transaction: either the whole reset + load lands, or nothing does.
    with psycopg.connect(source_dsn()) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE user_groups, document_acl, documents, products, users")
        cur.executemany(
            "INSERT INTO users (id, email, display_name, password_hash) VALUES (%s, %s, %s, %s)",
            [(u.id, u.email, u.display_name, hasher.hash(password)) for u in data.users],
        )
        cur.executemany("INSERT INTO user_groups (user_id, group_id) VALUES (%s, %s)", memberships)
        cur.executemany(
            "INSERT INTO products (id, sku, name, description, price_inr, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [(p.id, p.sku, p.name, p.description, p.price_inr, p.status) for p in data.products],
        )
        cur.executemany(
            "INSERT INTO documents (id, kind, title, body, is_public) VALUES (%s, %s, %s, %s, %s)",
            [(d.id, d.kind, d.title, d.body, d.is_public) for d in documents],
        )
        cur.executemany("INSERT INTO document_acl (document_id, group_id) VALUES (%s, %s)", acl)

    print("Reset source DB and loaded Northwind Gear data:")
    print(f"  users:     {len(data.users):>4}  ({len(memberships)} group memberships)")
    print(f"  products:  {len(data.products):>4}")
    print(f"  articles:  {len(data.articles):>4}  (public)")
    print(f"  policies:  {len(data.policies):>4}  ({len(acl)} ACL rows)")


if __name__ == "__main__":
    main()