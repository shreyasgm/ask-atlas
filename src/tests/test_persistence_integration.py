"""Integration tests for checkpoint persistence.

Tests real PostgresSaver connectivity and MemorySaver fallback behavior
against a live Postgres instance (Docker app-db on port 5434).
"""

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver

from src.persistence import (
    AsyncCheckpointerManager,
    CheckpointerManager,
    setup_app_tables,
    setup_app_tables_sync,
)


@pytest.mark.db
class TestPersistenceIntegration:
    """Validate real checkpointer behavior against Postgres and fallback."""

    def test_checkpointer_with_real_checkpoint_db(self, checkpoint_db_url):
        """PostgresSaver is used when CHECKPOINT_DB_URL is configured."""
        manager = CheckpointerManager(db_url=checkpoint_db_url)
        try:
            cp = manager.checkpointer
            assert not isinstance(cp, MemorySaver)
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

    async def test_async_checkpointer_also_creates_conversations(
        self, checkpoint_db_url
    ):
        """get_checkpointer() creates conversations table as a side effect."""
        # Drop the table first so we can verify it's created
        with psycopg.connect(checkpoint_db_url) as conn:
            conn.execute("DROP TABLE IF EXISTS conversations")
            conn.commit()

        manager = AsyncCheckpointerManager(db_url=checkpoint_db_url)
        try:
            await manager.get_checkpointer()

            # Verify conversations table exists
            with psycopg.connect(checkpoint_db_url) as conn:
                row = conn.execute(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables"
                    "  WHERE table_name = 'conversations'"
                    ")"
                ).fetchone()
                assert row[0] is True
        finally:
            await manager.close()


@pytest.mark.db
class TestAppTableSetup:
    """Validate the conversations table DDL setup functions."""

    def test_conversations_table_created(self, checkpoint_db_url):
        """setup_app_tables_sync creates the conversations table."""
        # Drop first to ensure clean slate
        with psycopg.connect(checkpoint_db_url) as conn:
            conn.execute("DROP TABLE IF EXISTS conversations")
            conn.commit()

        setup_app_tables_sync(checkpoint_db_url)

        with psycopg.connect(checkpoint_db_url) as conn:
            row = conn.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_name = 'conversations'"
                ")"
            ).fetchone()
            assert row[0] is True

    def test_conversations_table_idempotent(self, checkpoint_db_url):
        """Calling setup_app_tables_sync twice does not raise."""
        setup_app_tables_sync(checkpoint_db_url)
        setup_app_tables_sync(checkpoint_db_url)  # should not raise

    def test_conversations_table_has_expected_columns(self, checkpoint_db_url):
        """The conversations table has all 5 expected columns."""
        setup_app_tables_sync(checkpoint_db_url)

        with psycopg.connect(checkpoint_db_url) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'conversations'"
                " ORDER BY ordinal_position"
            ).fetchall()

        columns = [r[0] for r in rows]
        assert columns == ["id", "session_id", "title", "created_at", "updated_at"]

    async def test_async_setup_app_tables(self, checkpoint_db_url):
        """Async setup_app_tables creates the conversations table."""
        with psycopg.connect(checkpoint_db_url) as conn:
            conn.execute("DROP TABLE IF EXISTS conversations")
            conn.commit()

        await setup_app_tables(checkpoint_db_url)

        with psycopg.connect(checkpoint_db_url) as conn:
            row = conn.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables"
                "  WHERE table_name = 'conversations'"
                ")"
            ).fetchone()
            assert row[0] is True
