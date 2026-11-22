"""Request and response shapes. FastAPI validates every request against these and documents them."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class CurrentUser(BaseModel):
    id: UUID
    email: str
    groups: list[int]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=6, ge=1, le=20)
    mode: Literal["rerank", "hybrid", "vector", "keyword"] = "rerank"


class SearchHit(BaseModel):
    chunk_id: UUID
    source_table: str
    source_id: UUID
    title: str
    text: str
    score: float  # reranker score (0-1) in rerank mode, RRF score otherwise
    updated_at: datetime


class SearchResponse(BaseModel):
    query: str
    mode: str
    hits: list[SearchHit]




class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Continue this saved conversation (streaming endpoint only); omit to start a new one.
    conversation_id: UUID | None = None
    # Admins only: answer as if this user (by email) had asked. Used for demos and debugging.
    view_as: str | None = Field(default=None, max_length=254)


class UserSummary(BaseModel):
    email: str
    groups: list[int]

class Citation(BaseModel):
    ref: str  # the short id the model cited, e.g. "S1"
    chunk_id: UUID
    source_table: str
    source_id: UUID
    title: str
    score: float  # reranker score of the cited chunk


class ChatResponse(BaseModel):
    question: str
    answer: str
    abstained: bool
    reason: str | None  # why we abstained: low_retrieval_score, model_declined, no_valid_citations
    citations: list[Citation]
    top_score: float | None  # best reranker score among the chunks this user may see
    model: str | None  # None when we abstained without calling the LLM
    took_ms: int


class SignupRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=200)


class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class MeResponse(CurrentUser):
    display_name: str
    created_at: datetime
    conversations: int
    questions: int


class ConversationSummary(BaseModel):
    id: UUID
    title: str
    updated_at: datetime


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[dict]
    abstained: bool
    reason: str | None
    model: str | None
    took_ms: int | None
    viewed_as: str | None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: UUID
    title: str
    messages: list[MessageOut]


class GroupInfo(BaseModel):
    id: int
    name: str


class AdminUser(BaseModel):
    id: UUID
    email: str
    display_name: str
    is_active: bool
    created_at: datetime
    groups: list[int]            # what the source database says
    effective_groups: list[int]  # what search currently uses (synced by CDC)


class SyncStatus(BaseModel):
    state: Literal["live", "catching_up", "paused", "off"]
    messages_waiting: int | None
    last_applied_at: datetime | None


class PipelineStatus(SyncStatus):
    reader_connected: bool
    slot_wal_bytes: int | None
    confirmed_lsn: str | None
    indexer_offset: int | None
    topic_end: int | None
    chunks: int
    last_change_at: datetime | None
