"""FastAPI application for the Ask-Atlas backend."""

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator

from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.conversations import (
    ConversationStore,
    InMemoryConversationStore,
    PostgresConversationStore,
    derive_title,
)
from src.text_to_sql import AtlasTextToSQL, _build_turn_summary

# ---------------------------------------------------------------------------
# Logging setup — always show timestamps, level, and logger name
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    force=True,
)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Body for /chat and /chat/stream."""

    question: str
    thread_id: str | None = None
    override_schema: Literal["hs92", "hs12", "sitc"] | None = None
    override_direction: Literal["exports", "imports"] | None = None
    override_mode: Literal["goods", "services"] | None = None


class QueryResultResponse(BaseModel):
    """A single executed query with its results."""

    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_time_ms: int
    tables: list[str] = []
    schema_name: str | None = None


class ChatResponse(BaseModel):
    """Response for /chat."""

    answer: str
    thread_id: str
    queries: list[QueryResultResponse] | None = None
    resolved_products: dict | None = None
    schemas_used: list[str] | None = None
    total_rows: int | None = None
    total_execution_time_ms: int | None = None


class ConversationSummary(BaseModel):
    """A conversation in the list response."""

    thread_id: str
    title: str | None
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    """A single message in the history response."""

    role: str
    content: str


class OverridesResponse(BaseModel):
    """Trade toggle overrides stored in LangGraph state."""

    override_schema: Literal["hs92", "hs12", "sitc"] | None = None
    override_direction: Literal["exports", "imports"] | None = None
    override_mode: Literal["goods", "services"] | None = None


class TurnSummaryResponse(BaseModel):
    """Per-turn pipeline summary returned from history."""

    entities: dict | None = None
    queries: list[dict] = []
    total_rows: int = 0
    total_execution_time_ms: int = 0


class ThreadMessagesResponse(BaseModel):
    """Response for GET /threads/{id}/messages."""

    messages: list[MessageResponse]
    overrides: OverridesResponse
    turn_summaries: list[TurnSummaryResponse] = []


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------


@dataclass
class _AppState:
    """Holds the shared AtlasTextToSQL instance and conversation store."""

    atlas_sql: AtlasTextToSQL | None = None
    conversation_store: ConversationStore | None = None


_state = _AppState()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create AtlasTextToSQL and ConversationStore at startup; tear down on shutdown."""
    pid = os.getpid()
    logger.info("=" * 60)
    logger.info("Ask-Atlas API starting  (pid=%d)", pid)
    logger.info("Initialising AtlasTextToSQL (async) …")
    _state.atlas_sql = await AtlasTextToSQL.create_async()
    logger.info("AtlasTextToSQL ready — accepting requests  (pid=%d)", pid)

    # Conversation store — Postgres if available, else in-memory
    checkpoint_db_url = getattr(_state.atlas_sql, "_async_checkpointer_manager", None)
    if checkpoint_db_url is not None:
        checkpoint_db_url = checkpoint_db_url.db_url
    if checkpoint_db_url:
        _state.conversation_store = PostgresConversationStore(checkpoint_db_url)
        logger.info("Using PostgresConversationStore")
    else:
        _state.conversation_store = InMemoryConversationStore()
        logger.info("Using InMemoryConversationStore")

    logger.info("=" * 60)
    yield
    logger.info("Shutting down Ask-Atlas API  (pid=%d)", pid)
    if _state.atlas_sql is not None:
        await _state.atlas_sql.aclose()
        _state.atlas_sql = None
    _state.conversation_store = None


app = FastAPI(title="Ask-Atlas API", version="0.1.0", lifespan=lifespan)


def _build_cors_origins() -> list[str]:
    """Build the CORS allow_origins list from hardcoded + env var origins."""
    origins = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:4173",
        "https://ask-atlas-gl.web.app",
        "https://ask-atlas-gl.firebaseapp.com",
    ]
    extra = os.environ.get("CORS_ORIGINS", "")
    if extra:
        origins.extend(o.strip() for o in extra.split(",") if o.strip())
    return origins


app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every incoming request with method, path, origin, and response status/duration."""
    t0 = time.monotonic()
    origin = request.headers.get("origin", "-")
    logger.info(
        "→ %s %s  (origin=%s)",
        request.method,
        request.url.path,
        origin,
    )
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "✗ %s %s  EXCEPTION after %dms",
            request.method,
            request.url.path,
            elapsed_ms,
        )
        raise
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "← %s %s  status=%d  %dms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    """Apply a timeout to all requests."""
    import asyncio

    try:
        return await asyncio.wait_for(
            call_next(request), timeout=REQUEST_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            "⏱ %s %s  TIMEOUT after %.0fs",
            request.method,
            request.url.path,
            REQUEST_TIMEOUT_SECONDS,
        )
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
    logger.warning(
        "503 Service Unavailable — AtlasTextToSQL not initialised  %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=503,
        content={"detail": "Service not ready. Please try again shortly."},
    )


async def _track_conversation(request: Request, thread_id: str, question: str) -> None:
    """Create or update a conversation row if X-Session-Id is present."""
    session_id = request.headers.get("x-session-id")
    store = _state.conversation_store
    if not session_id or store is None:
        return
    existing = await store.get(thread_id)
    if existing is None:
        title = derive_title(question)
        await store.create(thread_id, session_id, title)
    else:
        await store.update_timestamp(thread_id)


# ---------------------------------------------------------------------------
# API Router — all business routes mounted at /api
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok"}


@router.get("/debug/caches")
async def cache_stats() -> dict:
    """Read-only diagnostic endpoint for monitoring cache hit rates."""
    from src.cache import registry

    return registry.stats()


@router.post("/threads")
async def create_thread() -> dict:
    """Generate a new conversation thread ID."""
    return {"thread_id": str(uuid.uuid4())}


@router.get("/threads")
async def list_threads(request: Request) -> list[ConversationSummary]:
    """List conversations for a session (requires X-Session-Id header)."""
    session_id = request.headers.get("x-session-id")
    if not session_id:
        return JSONResponse(
            status_code=400,
            content={"detail": "X-Session-Id header is required."},
        )
    store = _state.conversation_store
    if store is None:
        return []
    rows = await store.list_by_session(session_id)
    return [
        ConversationSummary(
            thread_id=r.id,
            title=r.title,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/threads/{thread_id}/messages")
async def get_thread_messages(thread_id: str) -> ThreadMessagesResponse:
    """Retrieve message history and trade overrides for a thread from LangGraph state."""
    atlas_sql = _get_atlas_sql()
    config = {"configurable": {"thread_id": thread_id}}
    state = await atlas_sql.agent.aget_state(config)

    values = state.values or {}
    messages = values.get("messages")
    if not messages:
        return JSONResponse(
            status_code=404,
            content={"detail": "No messages found for this thread."},
        )

    result = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            result.append(MessageResponse(role="human", content=msg.content))
        elif isinstance(msg, AIMessage) and msg.content:
            result.append(MessageResponse(role="ai", content=msg.content))
        # Skip ToolMessages and empty AI messages

    overrides = OverridesResponse(
        override_schema=values.get("override_schema"),
        override_direction=values.get("override_direction"),
        override_mode=values.get("override_mode"),
    )

    turn_summaries = [
        TurnSummaryResponse(**ts) for ts in values.get("turn_summaries", [])
    ]

    return ThreadMessagesResponse(
        messages=result, overrides=overrides, turn_summaries=turn_summaries
    )


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str) -> Response:
    """Delete a conversation and its checkpoints."""
    # Delete from conversation store
    store = _state.conversation_store
    if store is not None:
        await store.delete(thread_id)

    # Delete from checkpoint tables
    atlas_sql = _state.atlas_sql
    if atlas_sql is not None:
        try:
            checkpointer = getattr(
                getattr(atlas_sql, "agent", None), "checkpointer", None
            )
            if checkpointer is not None:
                from langgraph.checkpoint.memory import MemorySaver

                if isinstance(checkpointer, MemorySaver):
                    checkpointer.storage.pop(thread_id, None)
                else:
                    # Postgres checkpointer — delete from checkpoint tables
                    import psycopg

                    manager = getattr(atlas_sql, "_async_checkpointer_manager", None)
                    db_url = manager.db_url if manager else None
                    if db_url:
                        async with await psycopg.AsyncConnection.connect(
                            db_url
                        ) as conn:
                            for table in (
                                "checkpoint_writes",
                                "checkpoint_blobs",
                                "checkpoints",
                            ):
                                await conn.execute(
                                    f"DELETE FROM {table} WHERE thread_id = %s",  # noqa: S608
                                    (thread_id,),
                                )
                            await conn.commit()
        except Exception:
            logger.warning(
                "Failed to delete checkpoints for thread %s",
                thread_id,
                exc_info=True,
            )

    return Response(status_code=204)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Non-streaming chat endpoint."""
    atlas_sql = _get_atlas_sql()
    thread_id = body.thread_id or str(uuid.uuid4())
    result = await atlas_sql.aanswer_question(
        body.question,
        thread_id=thread_id,
        override_schema=body.override_schema,
        override_direction=body.override_direction,
        override_mode=body.override_mode,
    )

    # Lazy conversation tracking
    await _track_conversation(request, thread_id, body.question)

    return ChatResponse(
        answer=result.answer,
        thread_id=thread_id,
        queries=[QueryResultResponse(**q) for q in result.queries] or None,
        resolved_products=result.resolved_products,
        schemas_used=result.schemas_used or None,
        total_rows=result.total_rows if result.queries else None,
        total_execution_time_ms=(
            result.total_execution_time_ms if result.queries else None
        ),
    )


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> EventSourceResponse:
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

    logger.info(
        "SSE stream starting  thread=%s  question=%r",
        thread_id,
        body.question[:80],
    )

    async def _event_generator() -> AsyncGenerator[dict, None]:
        t_start = time.monotonic()
        event_count = 0

        # Aggregate stats for the done event
        total_queries = 0
        total_rows = 0
        total_execution_time_ms = 0

        # Turn summary tracking for checkpoint persistence
        stream_queries: list[dict] = []
        stream_entities: dict | None = None

        # First event: thread_id
        event_count += 1
        logger.info("  SSE #%d  event=thread_id  thread=%s", event_count, thread_id)
        yield {
            "event": "thread_id",
            "data": json.dumps({"thread_id": thread_id}),
        }

        # Lazy conversation tracking
        await _track_conversation(request, thread_id, body.question)

        try:
            async for stream_data in atlas_sql.aanswer_question_stream(
                body.question,
                thread_id=thread_id,
                override_schema=body.override_schema,
                override_direction=body.override_direction,
                override_mode=body.override_mode,
            ):
                event_count += 1
                if stream_data.message_type in ("node_start", "pipeline_state"):
                    stage = (stream_data.payload or {}).get(
                        "stage", (stream_data.payload or {}).get("node", "?")
                    )
                    logger.info(
                        "  SSE #%d  event=%-16s  stage=%s",
                        event_count,
                        stream_data.message_type,
                        stage,
                    )
                    # New event types: emit payload directly (no wrapper)
                    yield {
                        "event": stream_data.message_type,
                        "data": json.dumps(stream_data.payload or {}),
                    }

                    # Track aggregates and turn summary data from pipeline_state
                    if (
                        stream_data.message_type == "pipeline_state"
                        and stream_data.payload
                    ):
                        stage = stream_data.payload.get("stage")
                        if stage == "extract_products":
                            stream_entities = {
                                "schemas": stream_data.payload.get("schemas", []),
                                "products": stream_data.payload.get("products", []),
                                "countries": stream_data.payload.get("countries", []),
                            }
                        elif stage == "execute_sql":
                            total_queries += 1
                            total_rows += stream_data.payload.get("row_count", 0)
                            total_execution_time_ms += stream_data.payload.get(
                                "execution_time_ms", 0
                            )
                            stream_queries.append(
                                {
                                    "sql": stream_data.payload.get("sql", ""),
                                    "columns": stream_data.payload.get("columns", []),
                                    "rows": stream_data.payload.get("rows", []),
                                    "row_count": stream_data.payload.get(
                                        "row_count", 0
                                    ),
                                    "execution_time_ms": stream_data.payload.get(
                                        "execution_time_ms", 0
                                    ),
                                    "tables": stream_data.payload.get("tables", []),
                                    "schema_name": stream_data.payload.get("schema"),
                                }
                            )
                else:
                    # Log content preview for non-pipeline events
                    preview = (stream_data.content or "")[:60]
                    logger.info(
                        "  SSE #%d  event=%-16s  content=%r",
                        event_count,
                        stream_data.message_type,
                        preview,
                    )
                    # Existing event types: wrap in {source, content, message_type}
                    yield {
                        "event": stream_data.message_type,
                        "data": json.dumps(
                            {
                                "source": stream_data.source,
                                "content": stream_data.content,
                                "message_type": stream_data.message_type,
                            }
                        ),
                    }
        except Exception:
            logger.exception(
                "SSE stream error  thread=%s  after %d events",
                thread_id,
                event_count,
            )
            raise

        # Persist turn summary to checkpoint
        try:
            summary = _build_turn_summary(stream_queries, stream_entities)
            config = {"configurable": {"thread_id": thread_id}}
            await atlas_sql.agent.aupdate_state(config, {"turn_summaries": [summary]})
        except Exception:
            logger.warning(
                "Failed to persist turn summary for thread %s",
                thread_id,
                exc_info=True,
            )

        # Final event: done with aggregate stats
        total_time_ms = int((time.monotonic() - t_start) * 1000)
        event_count += 1
        logger.info(
            "  SSE #%d  event=done  thread=%s  queries=%d  rows=%d  %dms",
            event_count,
            thread_id,
            total_queries,
            total_rows,
            total_time_ms,
        )
        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "thread_id": thread_id,
                    "total_queries": total_queries,
                    "total_rows": total_rows,
                    "total_execution_time_ms": total_execution_time_ms,
                    "total_time_ms": total_time_ms,
                }
            ),
        }

    return EventSourceResponse(_event_generator())


# ---------------------------------------------------------------------------
# Mount router and root-level health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def root_health() -> dict:
    """Root health check for Cloud Run probes and Docker HEALTHCHECK."""
    return {"status": "ok"}


app.include_router(router, prefix="/api")
