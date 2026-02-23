"""Unit tests for CheckpointerManager and AsyncCheckpointerManager.

Tests behavioral contracts only — no third-party API exercising (MemorySaver put/get/list).
"""

from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from src.persistence import AsyncCheckpointerManager, CheckpointerManager


class TestCheckpointerManager:
    """Tests for the synchronous CheckpointerManager class."""

    def test_fallback_to_memory_saver_when_no_url(self):
        """No DB URL in settings and no explicit URL -> MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            assert isinstance(manager.checkpointer, MemorySaver)

    def test_explicit_none_url_uses_memory_saver(self):
        """Passing db_url=None explicitly also falls back to MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager(db_url=None)
            assert isinstance(manager.checkpointer, MemorySaver)

    def test_explicit_url_overrides_settings(self):
        """Explicit db_url takes precedence over settings.checkpoint_db_url."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                checkpoint_db_url="postgresql://from-settings"
            )
            manager = CheckpointerManager(db_url="postgresql://explicit")
            assert manager._db_url == "postgresql://explicit"

    def test_settings_url_used_when_no_explicit_url(self):
        """When no explicit URL is passed, settings.checkpoint_db_url is used."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                checkpoint_db_url="postgresql://from-settings:5432/db"
            )
            manager = CheckpointerManager()
            assert manager._db_url == "postgresql://from-settings:5432/db"

    def test_lazy_initialization(self):
        """_checkpointer is None until .checkpointer property is accessed."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            assert manager._checkpointer is None
            _ = manager.checkpointer
            assert manager._checkpointer is not None

    def test_postgres_failure_falls_back_to_memory(self):
        """Bad Postgres URL -> catches exception, falls back to MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager(db_url="postgresql://bad-host:5432/nope")
            cp = manager.checkpointer
            assert isinstance(cp, MemorySaver)

    def test_close_without_init_is_safe(self):
        """close() before .checkpointer is accessed does not raise."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = CheckpointerManager()
            manager.close()  # no-op, should not raise


class TestAsyncCheckpointerManager:
    """Tests for the AsyncCheckpointerManager class."""

    async def test_fallback_to_memory_saver_when_no_url(self):
        """No DB URL -> MemorySaver fallback."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager()
            cp = await manager.get_checkpointer()
            assert isinstance(cp, MemorySaver)

    async def test_explicit_url_overrides_settings(self):
        """Explicit db_url takes precedence over settings."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                checkpoint_db_url="postgresql://from-settings"
            )
            manager = AsyncCheckpointerManager(db_url="postgresql://explicit")
            assert manager._db_url == "postgresql://explicit"

    async def test_lazy_initialization(self):
        """_checkpointer is None until get_checkpointer() is awaited."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager()
            assert manager._checkpointer is None
            await manager.get_checkpointer()
            assert manager._checkpointer is not None

    async def test_postgres_failure_falls_back_to_memory(self):
        """Bad Postgres URL -> catches exception, falls back to MemorySaver."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager(db_url="postgresql://bad-host:5432/nope")
            cp = await manager.get_checkpointer()
            assert isinstance(cp, MemorySaver)

    async def test_close_without_init_is_safe(self):
        """close() before get_checkpointer() does not raise."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager()
            await manager.close()  # no-op, should not raise

    async def test_cached_checkpointer_returned(self):
        """Multiple get_checkpointer() calls return the same cached instance."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager()
            cp1 = await manager.get_checkpointer()
            cp2 = await manager.get_checkpointer()
            assert cp1 is cp2

    async def test_close_with_memorysaver_is_noop(self):
        """close() doesn't reset state when using MemorySaver (no _async_conn)."""
        with patch("src.persistence.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(checkpoint_db_url=None)
            manager = AsyncCheckpointerManager()
            await manager.get_checkpointer()
            assert manager._checkpointer is not None
            await manager.close()
            # MemorySaver doesn't set _async_conn, so close is a no-op
            assert manager._checkpointer is not None
