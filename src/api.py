"""FastAPI application for the Ask-Atlas backend."""

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.text_to_sql import AtlasTextToSQL

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Body for /chat and /chat/stream."""

    question: str
    thread_id: str | None = None


class ChatResponse(BaseModel):
    """Response for /chat."""

    answer: str
    thread_id: str


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


@dataclass
class _AppState:
    """Holds the shared AtlasTextToSQL instance."""

    atlas_sql: AtlasTextToSQL | None = None


_state = _AppState()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create AtlasTextToSQL at startup; tear down on shutdown."""
    logger.info("Starting Ask-Atlas API — initialising AtlasTextToSQL (async)")
    _state.atlas_sql = await AtlasTextToSQL.create_async()
    yield
    logger.info("Shutting down Ask-Atlas API")
    if _state.atlas_sql is not None:
        await _state.atlas_sql.aclose()
        _state.atlas_sql = None


app = FastAPI(title="Ask-Atlas API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    """Apply a timeout to all requests."""
    import asyncio

    try:
        return await asyncio.wait_for(call_next(request), timeout=REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"detail": "Request timed out."})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_atlas_sql() -> AtlasTextToSQL:
    """Return the shared instance or raise 503."""
    if _state.atlas_sql is None:
        raise _ServiceUnavailable()
    return _state.atlas_sql


class _ServiceUnavailable(Exception):
    """Raised when AtlasTextToSQL is not initialised."""


@app.exception_handler(_ServiceUnavailable)
async def _service_unavailable_handler(request: Request, exc: _ServiceUnavailable):
    return JSONResponse(
        status_code=503,
        content={"detail": "Service not ready. Please try again shortly."},
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/threads")
async def create_thread() -> dict:
    """Generate a new conversation thread ID."""
    return {"thread_id": str(uuid.uuid4())}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """Non-streaming chat endpoint."""
    atlas_sql = _get_atlas_sql()
    thread_id = body.thread_id or str(uuid.uuid4())
    answer = await atlas_sql.aanswer_question(body.question, thread_id=thread_id)
    return ChatResponse(answer=answer, thread_id=thread_id)


@app.post("/chat/stream")
async def chat_stream(body: ChatRequest) -> EventSourceResponse:
    """SSE streaming chat endpoint.

    Event types:
        thread_id      – the conversation thread ID (sent first)
        agent_talk     – text chunks from the agent
        tool_output    – output from tool execution
        tool_call      – notification that a tool is being called
        node_start     – pipeline node begins (payload emitted directly)
        pipeline_state – pipeline node completed (payload emitted directly)
        done           – final event with aggregate stats
    """
    atlas_sql = _get_atlas_sql()
    thread_id = body.thread_id or str(uuid.uuid4())

    async def _event_generator() -> AsyncGenerator[dict, None]:
        t_start = time.monotonic()

        # Aggregate stats for the done event
        total_queries = 0
        total_rows = 0
        total_execution_time_ms = 0

        # First event: thread_id
        yield {
            "event": "thread_id",
            "data": json.dumps({"thread_id": thread_id}),
        }

        async for stream_data in atlas_sql.aanswer_question_stream(
            body.question, thread_id=thread_id
        ):
            if stream_data.message_type in ("node_start", "pipeline_state"):
                # New event types: emit payload directly (no wrapper)
                yield {
                    "event": stream_data.message_type,
                    "data": json.dumps(stream_data.payload or {}),
                }

                # Track aggregates from execute_sql pipeline_state
                if (
                    stream_data.message_type == "pipeline_state"
                    and stream_data.payload
                    and stream_data.payload.get("stage") == "execute_sql"
                ):
                    total_queries += 1
                    total_rows += stream_data.payload.get("row_count", 0)
                    total_execution_time_ms += stream_data.payload.get(
                        "execution_time_ms", 0
                    )
            else:
                # Existing event types: wrap in {source, content, message_type}
                yield {
                    "event": stream_data.message_type,
                    "data": json.dumps({
                        "source": stream_data.source,
                        "content": stream_data.content,
                        "message_type": stream_data.message_type,
                    }),
                }

        # Final event: done with aggregate stats
        total_time_ms = int((time.monotonic() - t_start) * 1000)
        yield {
            "event": "done",
            "data": json.dumps({
                "thread_id": thread_id,
                "total_queries": total_queries,
                "total_rows": total_rows,
                "total_execution_time_ms": total_execution_time_ms,
                "total_time_ms": total_time_ms,
            }),
        }

    return EventSourceResponse(_event_generator())
