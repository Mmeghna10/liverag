"""LiveRAG API.

Run from services/api:  uv run uvicorn app.main:app --reload --reload-dir app
Interactive docs:       http://127.0.0.1:8000/docs
"""

import json
import logging
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from liverag_core.config import dsn
from liverag_core.retrieval import Retriever
from psycopg_pool import ConnectionPool

from app.auth import CurrentUserDep, effective_user, require_admin
from app.auth import router as auth_router
from app.chat import CONTEXT_CHUNKS, answer, stream_answer
from app.llm import get_provider
from app.schemas import (ChatRequest, ChatResponse, CurrentUser, SearchHit, SearchRequest,
                         SearchResponse, UserSummary)

log = logging.getLogger("liverag.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Runs once at startup (before the first request) and once at shutdown."""
    # Fail loudly now, rather than with a 500 error on the first login.
    if len(os.environ.get("JWT_SECRET", "")) < 32:
        raise RuntimeError("Set JWT_SECRET in .env to a random string of at least 32 characters")
    # A pool keeps a few database connections open and lends them out, instead of
    # opening a new connection (slow) on every request.
    app.state.source_pool = ConnectionPool(dsn("SOURCE_DB"), min_size=1, max_size=4, open=True)
    app.state.index_pool = ConnectionPool(dsn("INDEX_DB"), min_size=1, max_size=8, open=True)
    retriever = Retriever()
    retriever.embedder, retriever.reranker  # load both models now, not on the first request
    app.state.retriever = retriever
    app.state.llm = get_provider()
    yield
    app.state.llm.close()
    app.state.source_pool.close()
    app.state.index_pool.close()


app = FastAPI(title="LiveRAG API", version="0.3.0", lifespan=lifespan)
app.include_router(auth_router)


def to_hit(hit: dict[str, Any], mode: str) -> SearchHit:
    meta = hit["metadata"]
    if hit["source_table"] == "products":
        title = f"{meta['name']} ({meta['sku']})"
    else:
        title = f"{meta['title']} > {meta['heading']}" if meta["heading"] else meta["title"]
    return SearchHit(
        chunk_id=hit["id"],
        source_table=hit["source_table"],
        source_id=hit["source_id"],
        title=title,
        text=hit["text"],
        score=hit["rerank"] if mode == "rerank" else hit["rrf"],
        updated_at=hit["updated_at"],
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/me")
def me(user: CurrentUserDep) -> CurrentUser:
    return user


@app.get("/api/v1/users")
def users(request: Request, user: CurrentUserDep) -> list[UserSummary]:
    """Admins only: every active user with their groups, for the "view as" picker."""
    require_admin(user)
    with request.app.state.source_pool.connection() as conn:
        people = conn.execute("SELECT id, email FROM users WHERE is_active ORDER BY email").fetchall()
    with request.app.state.index_pool.connection() as conn:
        rows = conn.execute("SELECT user_id, group_id FROM user_groups_replica ORDER BY group_id").fetchall()
    groups: dict[Any, list[int]] = {}
    for user_id, group_id in rows:
        groups.setdefault(user_id, []).append(group_id)
    return [UserSummary(email=email, groups=groups.get(user_id, [])) for user_id, email in people]



# A plain `def` (not `async def`): FastAPI runs it in a worker thread, so the slow model and
# database calls do not block other requests.
@app.post("/api/v1/search")
def search(body: SearchRequest, request: Request, user: CurrentUserDep) -> SearchResponse:
    with request.app.state.index_pool.connection() as conn:
        hits = request.app.state.retriever.search(conn, body.query, user.groups,
                                                  body.k, body.mode)
    return SearchResponse(query=body.query, mode=body.mode,
                          hits=[to_hit(hit, body.mode) for hit in hits])


@app.post("/api/v1/chat")
def chat(body: ChatRequest, request: Request, user: CurrentUserDep) -> ChatResponse:
    started = time.perf_counter()
    user = effective_user(request, user, body.view_as)
    # Same permission-aware retrieval as /search: the LLM can only ever see chunks this user may read.
    with request.app.state.index_pool.connection() as conn:
        hits = request.app.state.retriever.search(conn, body.question, user.groups,
                                                  CONTEXT_CHUNKS, "rerank")
    return answer(body.question, [to_hit(hit, "rerank") for hit in hits],
                  request.app.state.llm, started)


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event: an event name, one line of JSON, then a blank line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/v1/chat/stream")
def chat_stream(body: ChatRequest, request: Request, user: CurrentUserDep) -> StreamingResponse:
    started = time.perf_counter()
    user = effective_user(request, user, body.view_as)
    # Retrieve BEFORE the stream starts: a database error still becomes a normal HTTP 500.
    with request.app.state.index_pool.connection() as conn:
        hits = request.app.state.retriever.search(conn, body.question, user.groups,
                                                  CONTEXT_CHUNKS, "rerank")
    search_hits = [to_hit(hit, "rerank") for hit in hits]
    llm = request.app.state.llm

    def events() -> Iterator[str]:
        try:
            for event, data in stream_answer(body.question, search_hits, llm, started):
                yield sse(event, data)
        except Exception:
            # The 200 status line is already sent, so report failures as an event instead.
            log.exception("chat stream failed")
            yield sse("error", {"detail": "The answer could not be generated. Please try again."})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})