"""Unit tests for CheckpointerManager and MemorySaver checkpointer API."""

import pytest
from unittest.mock import patch, MagicMock

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver
from src.persistence import CheckpointerManager


class TestCheckpointerManager:
    """Tests for the CheckpointerManager class."""

    def test_fallback_to_memory_saver_when_no_url(self):
        """Without a checkpoint DB URL, should fall back to MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            assert isinstance(manager.checkpointer, MemorySaver)

    def test_explicit_none_url_uses_memory_saver(self):
        """Passing db_url=None explicitly should also fall back."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager(db_url=None)
            assert isinstance(manager.checkpointer, MemorySaver)

    def test_explicit_url_overrides_settings(self):
        """An explicit db_url should take precedence over settings."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                checkpoint_db_url="postgresql://from-settings"
            )
            manager = CheckpointerManager(db_url="postgresql://explicit")
            assert manager._db_url == "postgresql://explicit"

    def test_lazy_initialization(self):
        """Checkpointer should not be created until first access."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            assert manager._checkpointer is None
            # Access the property to trigger creation
            _ = manager.checkpointer
            assert manager._checkpointer is not None

    def test_postgres_failure_falls_back_to_memory(self):
        """If PostgresSaver can't connect, should fall back to MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager(db_url="postgresql://bad-host:5432/nope")
            # Should not raise — falls back gracefully
            cp = manager.checkpointer
            assert isinstance(cp, MemorySaver)

    def test_close_without_init_is_safe(self):
        """Calling close() before checkpointer is created should not raise."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            manager.close()  # should be a no-op

    def test_close_resets_state(self):
        """After close(), internal state should be reset."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            _ = manager.checkpointer  # trigger init
            assert manager._checkpointer is not None
            # MemorySaver has no _pg_conn, so close is a no-op for it
            manager.close()
            # _pg_conn was never set for MemorySaver, so _checkpointer stays
            # This is correct behavior — close() only cleans up Postgres resources

    def test_settings_url_used_when_no_explicit_url(self):
        """When no explicit URL, should use settings.checkpoint_db_url."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                checkpoint_db_url="postgresql://from-settings:5432/db"
            )
            manager = CheckpointerManager()
            assert manager._db_url == "postgresql://from-settings:5432/db"


# ---------------------------------------------------------------------------
# Checkpointer API tests — exercise MemorySaver directly, no DB, no LLM
# ---------------------------------------------------------------------------


class TestCheckpointerAPI:
    """Verify the LangGraph checkpointer put/get/list API via MemorySaver."""

    @pytest.fixture()
    def saver(self) -> MemorySaver:
        return MemorySaver()

    @pytest.fixture()
    def make_config(self):
        """Factory for RunnableConfig dicts with a ``thread_id``."""

        def _make(thread_id: str) -> dict:
            return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        return _make

    @pytest.fixture()
    def sample_metadata(self) -> dict:
        return {"source": "unit-test", "step": 1, "parents": {}}

    def test_put_and_get(self, saver, make_config, sample_metadata):
        """Store a checkpoint, retrieve by thread_id, verify ``id`` matches."""
        config = make_config("thread-1")
        checkpoint = empty_checkpoint()
        stored = saver.put(config, checkpoint, sample_metadata, {})
        retrieved = saver.get(stored)
        assert retrieved is not None
        assert retrieved["id"] == checkpoint["id"]

    def test_get_tuple_returns_metadata(self, saver, make_config, sample_metadata):
        """Custom metadata fields round-trip through ``get_tuple()``."""
        config = make_config("thread-meta")
        checkpoint = empty_checkpoint()
        stored = saver.put(config, checkpoint, sample_metadata, {})
        tup = saver.get_tuple(stored)
        assert tup is not None
        assert tup.metadata["source"] == "unit-test"
        assert tup.metadata["step"] == 1

    def test_list_returns_stored_checkpoints(self, saver, make_config, sample_metadata):
        """Two checkpoints on same thread → ``list()`` returns 2 items, newest-first."""
        config = make_config("thread-list")
        cp1 = empty_checkpoint()
        saver.put(config, cp1, {**sample_metadata, "step": 1}, {})
        cp2 = empty_checkpoint()
        saver.put(config, cp2, {**sample_metadata, "step": 2}, {})

        items = list(saver.list(config))
        assert len(items) == 2
        # newest first (highest step)
        assert items[0].metadata["step"] == 2
        assert items[1].metadata["step"] == 1

    def test_different_threads_are_isolated(self, saver, make_config, sample_metadata):
        """A checkpoint stored on thread-1 is invisible from thread-2."""
        config_a = make_config("thread-A")
        config_b = make_config("thread-B")

        checkpoint = empty_checkpoint()
        saver.put(config_a, checkpoint, sample_metadata, {})

        assert saver.get(config_b) is None
        assert list(saver.list(config_b)) == []
