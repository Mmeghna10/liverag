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

from confluent_kafka import Consumer
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from liverag_core.config import dsn
from liverag_core.retrieval import Retriever
from psycopg_pool import ConnectionPool

from app import account, admin, conversations
from app.auth import CurrentUserDep, effective_user
from app.auth import router as auth_router
from app.chat import CONTEXT_CHUNKS, answer, stream_answer
from app.conversations import save_message, start_or_continue
from app.llm import get_provider
from app.schemas import ChatRequest, ChatResponse, SearchHit, SearchRequest, SearchResponse

log = logging.getLogger("uvicorn.error")


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
    # Only used to read the topic's end offset for the live sync status; never consumes.
    app.state.kafka = Consumer({
        "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092"),
        "group.id": "liverag-api-status",
    })
    yield
    app.state.kafka.close()
    app.state.llm.close()
    app.state.source_pool.close()
    app.state.index_pool.close()


app = FastAPI(title="LiveRAG API", version="0.5.0", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(account.router)
app.include_router(conversations.router)
app.include_router(admin.router)


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
    """One answer as JSON (used by the evaluation scripts). Not saved to a conversation."""
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
    """The web app's chat: streams the answer and saves both sides into a conversation."""
    started = time.perf_counter()
    caller = user                       # the conversation always belongs to whoever is signed in
    user = effective_user(request, user, body.view_as)
    viewed_as = user.email if user.email != caller.email else None
    pool = request.app.state.index_pool
    with pool.connection() as conn:
        conversation_id = start_or_continue(conn, body.conversation_id, caller.id, body.question)
        save_message(conn, conversation_id, "user", body.question, viewed_as=viewed_as)
        # Retrieve BEFORE the stream starts: a database error still becomes a normal HTTP 500.
        hits = request.app.state.retriever.search(conn, body.question, user.groups,
                                                  CONTEXT_CHUNKS, "rerank")
    search_hits = [to_hit(hit, "rerank") for hit in hits]
    llm = request.app.state.llm

    def events() -> Iterator[str]:
        yield sse("conversation", {"id": str(conversation_id)})
        try:
            for event, data in stream_answer(body.question, search_hits, llm, started):
                if event == "done":
                    with pool.connection() as conn:
                        save_message(conn, conversation_id, "assistant", data["answer"], data,
                                     viewed_as)
                yield sse(event, data)
        except Exception:
            # The 200 status line is already sent, so report failures as an event instead.
            log.exception("chat stream failed")
            detail = "The answer could not be generated. Please try again."
            with pool.connection() as conn:
                save_message(conn, conversation_id, "assistant", detail,
                             {"abstained": True, "reason": "error"}, viewed_as)
            yield sse("error", {"detail": detail})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# Deployment: when WEB_DIST points at the built web app (apps/web/dist), serve it from this same
# server, so one port hosts both the UI and the API. Unset in development, where Vite serves the UI.
if os.environ.get("WEB_DIST"):
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _dist = Path(os.environ["WEB_DIST"])
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str) -> FileResponse:
        """Files from the build (favicon etc.), and index.html for client-side routes like /chat."""
        if path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        file = (_dist / path).resolve()
        if path and file.is_file() and _dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(_dist / "index.html")
