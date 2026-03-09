"""Tests for src/db_pool_health and src/cleanup_db_connections."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch


from src.db_pool_health import (
    _on_checkin,
    _on_invalidate,
    _pool_stats,
    attach_pool_listeners,
    get_pool_stats,
)

# ---------------------------------------------------------------------------
# Pool event listener tests
# ---------------------------------------------------------------------------


class TestOnCheckin:
    def test_warns_on_slow_checkout(self, caplog):
        record = MagicMock()
        # Simulate a connection checked out 6 seconds ago
        record.info = {
            "checkout_time": time.monotonic() - 6.0,
            "engine_label": "test",
        }
        with caplog.at_level("WARNING", logger="src.db_pool_health"):
            _on_checkin(MagicMock(), record)
        assert "possible leak" in caplog.text.lower()

    def test_no_warning_on_fast_checkout(self, caplog):
        record = MagicMock()
        record.info = {
            "checkout_time": time.monotonic() - 0.1,
            "engine_label": "test",
        }
        with caplog.at_level("WARNING", logger="src.db_pool_health"):
            _on_checkin(MagicMock(), record)
        assert "possible leak" not in caplog.text.lower()

    def test_no_crash_when_checkout_time_missing(self):
        record = MagicMock()
        record.info = {}
        # Should not raise
        _on_checkin(MagicMock(), record)


class TestOnInvalidate:
    def test_logs_exception(self, caplog):
        with caplog.at_level("WARNING", logger="src.db_pool_health"):
            _on_invalidate(MagicMock(), MagicMock(), RuntimeError("broken pipe"))
        assert "broken pipe" in caplog.text

    def test_logs_soft_invalidation(self, caplog):
        with caplog.at_level("INFO", logger="src.db_pool_health"):
            _on_invalidate(MagicMock(), MagicMock(), None)
        assert "soft" in caplog.text.lower()


# ---------------------------------------------------------------------------
# attach_pool_listeners
# ---------------------------------------------------------------------------


class TestAttachPoolListeners:
    def test_attaches_to_sync_engine(self):
        pool = MagicMock()
        engine = MagicMock()
        engine.pool = pool
        with patch("src.db_pool_health.event") as mock_event:
            attach_pool_listeners(engine)
        assert mock_event.listen.call_count == 3

    def test_attaches_to_async_engine(self):
        """AsyncEngine stores pool on sync_engine.pool."""
        pool = MagicMock()
        sync_engine = MagicMock()
        sync_engine.pool = pool
        engine = MagicMock(spec=[])  # no .pool attribute
        engine.sync_engine = sync_engine
        with patch("src.db_pool_health.event") as mock_event:
            attach_pool_listeners(engine)
        assert mock_event.listen.call_count == 3

    def test_warns_when_no_pool_found(self, caplog):
        engine = MagicMock(spec=[])  # no .pool, no .sync_engine
        with caplog.at_level("WARNING", logger="src.db_pool_health"):
            attach_pool_listeners(engine)
        assert "could not find pool" in caplog.text.lower()


# ---------------------------------------------------------------------------
# Pool stats helpers
# ---------------------------------------------------------------------------


class TestPoolStats:
    def test_returns_metrics(self):
        pool = MagicMock()
        pool.size.return_value = 3
        pool.checkedin.return_value = 2
        pool.checkedout.return_value = 1
        pool.overflow.return_value = 0
        pool.status.return_value = "Pool size: 3"
        result = _pool_stats(pool)
        assert result["pool_size"] == 3
        assert result["checked_in"] == 2
        assert result["checked_out"] == 1
        assert result["overflow"] == 0


class TestGetPoolStats:
    def test_both_engines(self):
        sync_pool = MagicMock()
        sync_pool.size.return_value = 3
        sync_pool.checkedin.return_value = 3
        sync_pool.checkedout.return_value = 0
        sync_pool.overflow.return_value = 0
        sync_pool.status.return_value = ""

        async_pool = MagicMock()
        async_pool.size.return_value = 5
        async_pool.checkedin.return_value = 4
        async_pool.checkedout.return_value = 1
        async_pool.overflow.return_value = 0
        async_pool.status.return_value = ""

        sync_engine = MagicMock()
        sync_engine.pool = sync_pool

        # AsyncEngine: pool lives on sync_engine
        inner_sync = MagicMock()
        inner_sync.pool = async_pool
        async_engine = MagicMock()
        async_engine.sync_engine = inner_sync

        result = get_pool_stats(sync_engine, async_engine)
        assert "sync" in result
        assert "async" in result
        assert result["sync"]["pool_size"] == 3
        assert result["async"]["pool_size"] == 5

    def test_none_engines(self):
        result = get_pool_stats(None, None)
        # No sync/async pool keys, but rolling metrics are always present
        assert "sync" not in result
        assert "async" not in result
        assert "query_latency" in result
        assert "connection_hold" in result
        assert "recent_slow_queries" in result


# ---------------------------------------------------------------------------
# Cleanup script tests
# ---------------------------------------------------------------------------


class TestCleanupParseArgs:
    def test_defaults(self):
        from src.cleanup_db_connections import _parse_args

        args = _parse_args([])
        assert args.terminate is False
        assert args.idle_minutes == 5

    def test_terminate_flag(self):
        from src.cleanup_db_connections import _parse_args

        args = _parse_args(["--terminate"])
        assert args.terminate is True

    def test_idle_minutes(self):
        from src.cleanup_db_connections import _parse_args

        args = _parse_args(["--idle-minutes", "10"])
        assert args.idle_minutes == 10


class TestCleanupFunctions:
    """Test the SQL-executing helpers with a mocked connection."""

    def test_fetch_connection_stats(self):
        from src.cleanup_db_connections import fetch_connection_stats

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("active", "readonly", 3),
            ("idle", "readonly", 2),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = fetch_connection_stats(mock_conn)
        assert len(result) == 2
        assert result[0] == ("active", "readonly", 3)

    def test_fetch_idle_connections(self):
        from src.cleanup_db_connections import fetch_idle_connections

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (123, "readonly", "idle", 600, "app", "10.0.0.1"),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = fetch_idle_connections(mock_conn, min_idle_seconds=300)
        assert len(result) == 1
        assert result[0][0] == 123  # pid

    def test_terminate_idle_connections(self):
        from src.cleanup_db_connections import terminate_idle_connections

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [(True,), (True,)]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        count = terminate_idle_connections(mock_conn, min_idle_seconds=300)
        assert count == 2
        mock_conn.commit.assert_called_once()
