"""Alembic environment: runs index-DB migrations against the database described in .env."""

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models yet: migrations are hand-written SQL, so autogenerate has nothing to compare.
target_metadata = None


def database_url() -> str:
    return (
        f"postgresql+psycopg://{os.environ['INDEX_DB_USER']}:{os.environ['INDEX_DB_PASSWORD']}"
        f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ['INDEX_DB_PORT']}/{os.environ['INDEX_DB_NAME']}"
    )


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (alembic upgrade head --sql)."""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()