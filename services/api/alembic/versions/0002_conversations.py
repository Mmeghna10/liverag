"""Saved conversations: each user's chats and the messages in them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    # user_id is the id from the source DB's users table (no foreign key across databases).
    """
    CREATE TABLE conversations (
        id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id    uuid NOT NULL,
        title      text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX conversations_user_recent ON conversations (user_id, updated_at DESC)",
    """
    CREATE TABLE messages (
        id              bigserial PRIMARY KEY,
        conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
        role            text NOT NULL CHECK (role IN ('user', 'assistant')),
        content         text NOT NULL,
        citations       jsonb NOT NULL DEFAULT '[]',
        abstained       boolean NOT NULL DEFAULT false,
        reason          text,
        model           text,
        took_ms         integer,
        viewed_as       text,          -- set when an admin asked "as" another user
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX messages_conversation ON messages (conversation_id, id)",
]

DOWNGRADE = [
    "DROP TABLE messages",
    "DROP TABLE conversations",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
