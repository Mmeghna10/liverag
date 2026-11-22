"""Saved chats: list, open, rename and delete your conversations. Only ever your own."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from psycopg.types.json import Jsonb

from app.auth import CurrentUserDep
from app.schemas import ConversationDetail, ConversationSummary, MessageOut, RenameRequest

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def owned(conn: Any, conversation_id: UUID, user_id: UUID) -> None:
    """404 unless this conversation exists AND belongs to this user (no hint which one failed)."""
    row = conn.execute("SELECT 1 FROM conversations WHERE id = %s AND user_id = %s",
                       (conversation_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


def start_or_continue(conn: Any, conversation_id: UUID | None, user_id: UUID,
                      question: str) -> UUID:
    """Return the conversation to save into: the given one (if it is yours) or a new one."""
    if conversation_id:
        owned(conn, conversation_id, user_id)
        return conversation_id
    title = question if len(question) <= 60 else question[:57].rstrip() + "..."
    return conn.execute("INSERT INTO conversations (user_id, title) VALUES (%s, %s) RETURNING id",
                        (user_id, title)).fetchone()[0]


def save_message(conn: Any, conversation_id: UUID, role: str, content: str,
                 result: dict[str, Any] | None = None, viewed_as: str | None = None) -> None:
    result = result or {}
    conn.execute("""
        INSERT INTO messages (conversation_id, role, content, citations, abstained, reason,
                              model, took_ms, viewed_as)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (conversation_id, role, content, Jsonb(result.get("citations", [])),
         result.get("abstained", False), result.get("reason"), result.get("model"),
         result.get("took_ms"), viewed_as))
    conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,))


@router.get("")
def list_conversations(request: Request, user: CurrentUserDep) -> list[ConversationSummary]:
    with request.app.state.index_pool.connection() as conn:
        rows = conn.execute("SELECT id, title, updated_at FROM conversations WHERE user_id = %s "
                            "ORDER BY updated_at DESC LIMIT 100", (user.id,)).fetchall()
    return [ConversationSummary(id=r[0], title=r[1], updated_at=r[2]) for r in rows]


@router.get("/{conversation_id}")
def get_conversation(conversation_id: UUID, request: Request,
                     user: CurrentUserDep) -> ConversationDetail:
    with request.app.state.index_pool.connection() as conn:
        owned(conn, conversation_id, user.id)
        title = conn.execute("SELECT title FROM conversations WHERE id = %s",
                             (conversation_id,)).fetchone()[0]
        rows = conn.execute("""
            SELECT id, role, content, citations, abstained, reason, model, took_ms, viewed_as,
                   created_at
            FROM messages WHERE conversation_id = %s ORDER BY id""", (conversation_id,)).fetchall()
    messages = [MessageOut(id=r[0], role=r[1], content=r[2], citations=r[3], abstained=r[4],
                           reason=r[5], model=r[6], took_ms=r[7], viewed_as=r[8],
                           created_at=r[9]) for r in rows]
    return ConversationDetail(id=conversation_id, title=title, messages=messages)


@router.patch("/{conversation_id}")
def rename(conversation_id: UUID, body: RenameRequest, request: Request,
           user: CurrentUserDep) -> ConversationSummary:
    with request.app.state.index_pool.connection() as conn:
        owned(conn, conversation_id, user.id)
        row = conn.execute("UPDATE conversations SET title = %s, updated_at = now() WHERE id = %s "
                           "RETURNING id, title, updated_at",
                           (body.title.strip(), conversation_id)).fetchone()
    return ConversationSummary(id=row[0], title=row[1], updated_at=row[2])


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(conversation_id: UUID, request: Request, user: CurrentUserDep) -> None:
    with request.app.state.index_pool.connection() as conn:
        owned(conn, conversation_id, user.id)
        conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))
