# /// script
# requires-python = ">=3.12"
# dependencies = ["psycopg[binary]>=3.2", "python-dotenv>=1.0"]
# ///
"""Prove both LiveRAG databases are up and configured correctly."""

import os
import sys
from collections.abc import Callable
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def dsn(prefix: str) -> str:
    return (
        f"host=localhost port={os.environ[f'{prefix}_PORT']} "
        f"dbname={os.environ[f'{prefix}_NAME']} "
        f"user={os.environ[f'{prefix}_USER']} "
        f"password={os.environ[f'{prefix}_PASSWORD']} "
        "connect_timeout=5"
    )


def check(label: str, ok: bool, detail: object) -> bool:
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: {detail}")
    return ok


def show(conn: psycopg.Connection, setting: str) -> str:
    return conn.execute(f"SHOW {setting}").fetchone()[0]


def check_source() -> bool:
    print(f"source-db (localhost:{os.environ['SOURCE_DB_PORT']})")
    with psycopg.connect(dsn("SOURCE_DB")) as conn:
        version = show(conn, "server_version")
        wal_level = show(conn, "wal_level")
        slots = int(show(conn, "max_replication_slots"))
        senders = int(show(conn, "max_wal_senders"))
    return all([
        check("server version", version.startswith("16"), version),
        check("wal_level", wal_level == "logical", wal_level),
        check("max_replication_slots", slots >= 4, slots),
        check("max_wal_senders", senders >= 4, senders),
    ])


def check_index() -> bool:
    print(f"index-db (localhost:{os.environ['INDEX_DB_PORT']})")
    with psycopg.connect(dsn("INDEX_DB")) as conn:
        version = show(conn, "server_version")
        row = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        results = [
            check("server version", version.startswith("16"), version),
            check("pgvector installed", row is not None, row[0] if row else "missing"),
        ]
        if row is not None:
            # <=> is cosine distance; orthogonal vectors are exactly 1.0 apart.
            distance = conn.execute(
                "SELECT '[1,0,0]'::vector <=> '[0,1,0]'::vector"
            ).fetchone()[0]
            results.append(
                check("cosine distance [1,0,0] vs [0,1,0]", abs(distance - 1.0) < 1e-9, distance)
            )
    return all(results)


def run(fn: Callable[[], bool]) -> bool:
    try:
        return fn()
    except psycopg.OperationalError as exc:
        first_line = str(exc).strip().splitlines()[0]
        return check("connect", False, f"{first_line} (is the container running?)")


def main() -> int:
    results = [run(check_source), run(check_index)]
    print("All checks passed." if all(results) else "Some checks FAILED.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())