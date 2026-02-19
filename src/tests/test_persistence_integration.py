"""Integration tests for checkpoint persistence.

Tests real PostgresSaver connectivity and MemorySaver fallback behavior
against a live Postgres instance (Docker test DB on port 5433).
"""

import os

import pytest
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver

from src.config import get_settings
from src.persistence import AsyncCheckpointerManager, CheckpointerManager

BASE_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkpoint_db_url():
    """Provide the Docker test DB URL, skip if not set."""
    url = os.environ.get("ATLAS_DB_URL")
    if not url:
        pytest.skip("ATLAS_DB_URL not set")
    return url


@pytest.mark.db
class TestPersistenceIntegration:
    """Validate real checkpointer behavior against Postgres and fallback."""

    def test_checkpointer_with_real_checkpoint_db(self):
        """PostgresSaver is used when CHECKPOINT_DB_URL is configured."""
        settings = get_settings()
        if not settings.checkpoint_db_url:
            pytest.skip("CHECKPOINT_DB_URL not configured")

        manager = CheckpointerManager(db_url=settings.checkpoint_db_url)
        try:
            cp = manager.checkpointer
            assert not isinstance(cp, MemorySaver)
        finally:
            manager.close()

    def test_checkpointer_fallback_with_bad_url(self):
        """Bad URL gracefully falls back to MemorySaver (no crash)."""
        manager = CheckpointerManager(db_url="postgresql://invalid:5432/nope")
        try:
            cp = manager.checkpointer
            assert isinstance(cp, MemorySaver)
        finally:
            manager.close()

    def test_state_survives_manager_restart(self, checkpoint_db_url):
        """Checkpoint metadata persists across CheckpointerManager instances."""
        config = {
            "configurable": {
                "thread_id": "restart-test-thread",
                "checkpoint_ns": "",
            }
        }
        metadata = {"source": "restart-test", "step": 1, "parents": {}}

        # --- Manager A: store a checkpoint ---
        manager_a = CheckpointerManager(db_url=checkpoint_db_url)
        try:
            checkpoint = empty_checkpoint()
            manager_a.checkpointer.put(config, checkpoint, metadata, {})
        finally:
            manager_a.close()

        # --- Manager B: read it back after a full restart ---
        manager_b = CheckpointerManager(db_url=checkpoint_db_url)
        try:
            tup = manager_b.checkpointer.get_tuple(config)
            assert tup is not None, "Checkpoint not found after manager restart"
            assert tup.metadata["source"] == "restart-test"
            assert tup.metadata["step"] == 1
        finally:
            manager_b.close()


@pytest.mark.db
class TestAsyncPersistenceIntegration:
    """Validate async checkpointer behavior against real Postgres."""

    async def test_async_checkpointer_with_real_db(self, checkpoint_db_url):
        """AsyncCheckpointerManager returns a non-MemorySaver with a real DB URL."""
        manager = AsyncCheckpointerManager(db_url=checkpoint_db_url)
        try:
            cp = await manager.get_checkpointer()
            assert not isinstance(cp, MemorySaver)
        finally:
            await manager.close()

    async def test_async_fallback_with_bad_url(self):
        """Bad URL gracefully falls back to MemorySaver."""
        manager = AsyncCheckpointerManager(
            db_url="postgresql://invalid:5432/nope"
        )
        try:
            cp = await manager.get_checkpointer()
            assert isinstance(cp, MemorySaver)
        finally:
            await manager.close()

    async def test_async_close_resets_state(self, checkpoint_db_url):
        """With real Postgres, close() resets _checkpointer and _async_conn to None."""
        manager = AsyncCheckpointerManager(db_url=checkpoint_db_url)
        try:
            await manager.get_checkpointer()
            assert manager._checkpointer is not None
            assert manager._async_conn is not None
        finally:
            await manager.close()

        assert manager._checkpointer is None
        assert manager._async_conn is None
