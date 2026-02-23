"""Checkpointer management for LangGraph agent persistence.

Provides PostgresSaver-backed persistence when a checkpoint DB URL is
configured, falling back to in-memory MemorySaver otherwise.  Also creates
the application-owned ``conversations`` table alongside checkpoint tables.
"""

import logging
from typing import Optional

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from src.config import get_settings

logger = logging.getLogger(__name__)

CONVERSATIONS_DDL = """\
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR PRIMARY KEY,
    session_id VARCHAR NOT NULL,
    title VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
"""


def setup_app_tables_sync(db_url: str) -> None:
    """Create application-owned tables (e.g. ``conversations``) synchronously.

    Args:
        db_url: Postgres connection string for the app database.
    """
    with psycopg.connect(db_url) as conn:
        conn.execute(CONVERSATIONS_DDL)
        conn.commit()
    logger.info("App tables created/verified (sync)")


async def setup_app_tables(db_url: str) -> None:
    """Create application-owned tables (e.g. ``conversations``) asynchronously.

    Args:
        db_url: Postgres connection string for the app database.
    """
    async with await psycopg.AsyncConnection.connect(db_url) as conn:
        await conn.execute(CONVERSATIONS_DDL)
        await conn.commit()
    logger.info("App tables created/verified (async)")


class CheckpointerManager:
    """Lazy-initializing checkpointer that picks PostgresSaver or MemorySaver.

    Args:
        db_url: Optional Postgres connection string for persistent checkpoints.
            If ``None``, falls back to ``settings.checkpoint_db_url``, then
            to an in-memory ``MemorySaver``.
    """

    def __init__(self, db_url: str | None = None) -> None:
        settings = get_settings()
        self._db_url: str | None = db_url or settings.checkpoint_db_url
        self._checkpointer: Optional[BaseCheckpointSaver] = None
        self._pg_conn = None  # holds the context-manager for PostgresSaver

    @property
    def checkpointer(self) -> BaseCheckpointSaver:
        """Lazily create and return the checkpointer instance."""
        if self._checkpointer is None:
            self._checkpointer = self._create_checkpointer()
        return self._checkpointer

    def _create_checkpointer(self) -> BaseCheckpointSaver:
        """Create the appropriate checkpointer based on configuration."""
        if self._db_url:
            try:
                from langgraph.checkpoint.postgres import PostgresSaver

                self._pg_conn = PostgresSaver.from_conn_string(self._db_url)
                saver = self._pg_conn.__enter__()
                saver.setup()
                setup_app_tables_sync(self._db_url)
                logger.info("Using PostgresSaver for checkpoint persistence")
                return saver
            except Exception:
                logger.warning(
                    "Failed to initialize PostgresSaver, falling back to MemorySaver",
                    exc_info=True,
                )

        logger.info("Using MemorySaver for checkpoint persistence")
        return MemorySaver()

    def close(self) -> None:
        """Release resources held by the checkpointer."""
        if self._pg_conn is not None:
            try:
                self._pg_conn.__exit__(None, None, None)
            except Exception:
                logger.warning("Error closing PostgresSaver connection", exc_info=True)
            finally:
                self._pg_conn = None
                self._checkpointer = None


class AsyncCheckpointerManager:
    """Async checkpointer for FastAPI / async graph execution.

    Uses ``AsyncPostgresSaver`` when a checkpoint DB URL is configured,
    falling back to ``MemorySaver`` (which has working async pass-throughs).

    Args:
        db_url: Optional Postgres connection string for persistent checkpoints.
            If ``None``, falls back to ``settings.checkpoint_db_url``, then
            to an in-memory ``MemorySaver``.
    """

    def __init__(self, db_url: str | None = None) -> None:
        settings = get_settings()
        self._db_url: str | None = db_url or settings.checkpoint_db_url
        self._checkpointer: Optional[BaseCheckpointSaver] = None
        self._async_conn = None  # holds the async context manager

    @property
    def db_url(self) -> str | None:
        """The configured database URL, if any."""
        return self._db_url

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Lazily create and return the async checkpointer instance."""
        if self._checkpointer is None:
            self._checkpointer = await self._create_checkpointer()
        return self._checkpointer

    async def _create_checkpointer(self) -> BaseCheckpointSaver:
        """Create the appropriate async checkpointer based on configuration."""
        if self._db_url:
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                self._async_conn = AsyncPostgresSaver.from_conn_string(self._db_url)
                saver = await self._async_conn.__aenter__()
                await saver.setup()
                await setup_app_tables(self._db_url)
                logger.info("Using AsyncPostgresSaver for checkpoint persistence")
                return saver
            except Exception:
                logger.warning(
                    "Failed to initialize AsyncPostgresSaver, falling back to MemorySaver",
                    exc_info=True,
                )

        logger.info("Using MemorySaver for checkpoint persistence (async)")
        return MemorySaver()

    async def close(self) -> None:
        """Release resources held by the async checkpointer."""
        if self._async_conn is not None:
            try:
                await self._async_conn.__aexit__(None, None, None)
            except Exception:
                logger.warning(
                    "Error closing AsyncPostgresSaver connection", exc_info=True
                )
            finally:
                self._async_conn = None
                self._checkpointer = None
