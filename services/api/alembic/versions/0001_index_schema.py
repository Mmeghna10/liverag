"""Index DB schema v1: index versions, chunks_v1, LSN guard, offsets, ACL replica, embedding cache.

Revision ID: 0001
Revises:
Create Date: 2026-11-13
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS vector",

    # Every embedding model gets its own version and its own chunks table (blue/green switch).
    """
    CREATE TABLE index_versions (
        id              integer PRIMARY KEY,
        embedding_model text NOT NULL,
        dims            integer NOT NULL CHECK (dims > 0),
        status          text NOT NULL CHECK (status IN ('building', 'active', 'retired')),
        created_at      timestamptz NOT NULL DEFAULT now(),
        activated_at    timestamptz
    )
    """,
    # At most one row may be 'active': a partial unique index only covers rows matching WHERE.
    """
    CREATE UNIQUE INDEX index_versions_one_active
        ON index_versions (status) WHERE status = 'active'
    """,
    """
    INSERT INTO index_versions (id, embedding_model, dims, status, activated_at)
    VALUES (1, 'BAAI/bge-small-en-v1.5', 384, 'active', now())
    """,

    """
    CREATE TABLE chunks_v1 (
        id                uuid PRIMARY KEY,
        source_table      text NOT NULL,
        source_id         uuid NOT NULL,
        chunk_no          integer NOT NULL CHECK (chunk_no >= 0),
        text              text NOT NULL,
        tsv               tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
        embedding         vector(384) NOT NULL,
        content_hash      text NOT NULL,
        is_public         boolean NOT NULL,
        allowed_group_ids integer[] NOT NULL DEFAULT '{}',
        metadata          jsonb NOT NULL DEFAULT '{}',
        source_lsn        pg_lsn NOT NULL,
        updated_at        timestamptz NOT NULL DEFAULT now(),
        UNIQUE (source_table, source_id, chunk_no)
    )
    """,
    # Approximate nearest-neighbour search on cosine distance (the <=> operator).
    """
    CREATE INDEX chunks_v1_embedding_hnsw ON chunks_v1
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
    """,
    # Keyword search: GIN indexes every word of the tsvector.
    "CREATE INDEX chunks_v1_tsv_gin ON chunks_v1 USING gin (tsv)",
    # Permission filter: GIN makes `allowed_group_ids && ARRAY[...]` fast.
    "CREATE INDEX chunks_v1_allowed_groups_gin ON chunks_v1 USING gin (allowed_group_ids)",

    # LSN guard: the newest change applied for each source row (source_id is text because
    # some source keys are not UUIDs, e.g. user_groups is (user_id, group_id)).
    """
    CREATE TABLE source_state (
        source_table text NOT NULL,
        source_id    text NOT NULL,
        last_lsn     pg_lsn NOT NULL,
        deleted      boolean NOT NULL DEFAULT false,
        updated_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (source_table, source_id)
    )
    """,

    # Kafka consumer positions, written in the same transaction as the chunks they produced.
    """
    CREATE TABLE sink_offsets (
        consumer    text NOT NULL,
        topic       text NOT NULL,
        partition   integer NOT NULL,
        next_offset bigint NOT NULL CHECK (next_offset >= 0),
        updated_at  timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (consumer, topic, partition)
    )
    """,

    # Who is in which group, as of the last applied change. Read on every query.
    """
    CREATE TABLE user_groups_replica (
        user_id  uuid NOT NULL,
        group_id integer NOT NULL,
        last_lsn pg_lsn NOT NULL,
        PRIMARY KEY (user_id, group_id)
    )
    """,

    # Same text + same model = same vector, so never pay to embed it twice.
    # `vector` without a size: one cache serves models of any dimension.
    """
    CREATE TABLE embedding_cache (
        model        text NOT NULL,
        content_hash text NOT NULL,
        embedding    vector NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (model, content_hash)
    )
    """,
]

DOWNGRADE = [
    "DROP TABLE embedding_cache",
    "DROP TABLE user_groups_replica",
    "DROP TABLE sink_offsets",
    "DROP TABLE source_state",
    "DROP TABLE chunks_v1",
    "DROP TABLE index_versions",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)