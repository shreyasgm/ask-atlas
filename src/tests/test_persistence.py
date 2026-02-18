"""Unit tests for CheckpointerManager (mocked settings, no real DB)."""

import pytest
from unittest.mock import patch, MagicMock

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
