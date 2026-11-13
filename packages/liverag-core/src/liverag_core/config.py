"""Settings shared by every service, read from the repo's .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

# config.py -> liverag_core -> src -> liverag-core -> packages -> repo root
ROOT = Path(__file__).resolve().parents[4]
load_dotenv(ROOT / ".env")


def dsn(prefix: str) -> str:
    """Connection string for SOURCE_DB or INDEX_DB, built from the .env variables."""
    return (
        f"host=localhost port={os.environ[f'{prefix}_PORT']} "
        f"dbname={os.environ[f'{prefix}_NAME']} "
        f"user={os.environ[f'{prefix}_USER']} "
        f"password={os.environ[f'{prefix}_PASSWORD']}"
    )