"""Checkpointer management for LangGraph agent persistence.

Provides PostgresSaver-backed persistence when a checkpoint DB URL is
configured, falling back to in-memory MemorySaver otherwise.
"""

import logging
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from src.config import get_settings

logger = logging.getLogger(__name__)


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
