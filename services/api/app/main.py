"""LiveRAG API.

Run from services/api:  uv run uvicorn app.main:app --reload
Interactive docs:       http://localhost:8000/docs
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from liverag_core.config import dsn
from liverag_core.retrieval import Retriever
from psycopg_pool import ConnectionPool

from app.auth import CurrentUserDep
from app.auth import router as auth_router
from app.schemas import CurrentUser, SearchHit, SearchRequest, SearchResponse


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
    yield
    app.state.source_pool.close()
    app.state.index_pool.close()


app = FastAPI(title="LiveRAG API", version="0.1.0", lifespan=lifespan)
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


# A plain `def` (not `async def`): FastAPI runs it in a worker thread, so the slow model and
# database calls do not block other requests.
@app.post("/api/v1/search")
def search(body: SearchRequest, request: Request, user: CurrentUserDep) -> SearchResponse:
    with request.app.state.index_pool.connection() as conn:
        hits = request.app.state.retriever.search(conn, body.query, user.groups,
                                                  body.k, body.mode)
    return SearchResponse(query=body.query, mode=body.mode,
                          hits=[to_hit(hit, body.mode) for hit in hits])