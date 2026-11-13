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