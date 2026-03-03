"""Connection pool health monitoring for the Atlas Data DB.

Provides SQLAlchemy pool event listeners that track checkout timing and
invalidation events, plus a helper to surface pool statistics via the
debug endpoint.
"""

import logging
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.pool import Pool

logger = logging.getLogger(__name__)

# Threshold (seconds) before a held connection triggers a warning.
_SLOW_CHECKOUT_THRESHOLD = 5.0


# ---------------------------------------------------------------------------
# Pool event listeners
# ---------------------------------------------------------------------------


def _on_checkout(
    dbapi_conn: Any, connection_record: Any, connection_proxy: Any
) -> None:
    """Record the wall-clock time when a connection is checked out."""
    connection_record.info["checkout_time"] = time.monotonic()


def _on_checkin(dbapi_conn: Any, connection_record: Any) -> None:
    """Warn if a connection was held longer than the slow threshold."""
    checkout_time = connection_record.info.pop("checkout_time", None)
    if checkout_time is None:
        return
    held_seconds = time.monotonic() - checkout_time
    if held_seconds > _SLOW_CHECKOUT_THRESHOLD:
        logger.warning(
            "Connection held for %.1fs (threshold %.0fs) — possible leak",
            held_seconds,
            _SLOW_CHECKOUT_THRESHOLD,
        )


def _on_invalidate(
    dbapi_conn: Any, connection_record: Any, exception: BaseException | None
) -> None:
    """Log when a connection is invalidated (e.g. broken pipe)."""
    if exception is not None:
        logger.warning("Connection invalidated due to: %s", exception)
    else:
        logger.info("Connection invalidated (soft)")


def attach_pool_listeners(engine: Any) -> None:
    """Register checkout/checkin/invalidate listeners on *engine*'s pool.

    Works for both sync ``Engine`` and async ``AsyncEngine`` (the async
    variant exposes its underlying sync pool via ``.pool``).

    Args:
        engine: A SQLAlchemy ``Engine`` or ``AsyncEngine``.
    """
    pool = getattr(engine, "pool", None)
    if pool is None:
        # AsyncEngine stores the pool on the inner sync engine.
        sync_engine = getattr(engine, "sync_engine", None)
        pool = getattr(sync_engine, "pool", None) if sync_engine else None
    if pool is None:
        logger.warning("Could not find pool on engine %r — skipping listeners", engine)
        return

    event.listen(pool, "checkout", _on_checkout)
    event.listen(pool, "checkin", _on_checkin)
    event.listen(pool, "invalidate", _on_invalidate)
    logger.debug("Pool listeners attached to %r", engine)


# ---------------------------------------------------------------------------
# Pool statistics
# ---------------------------------------------------------------------------


def _pool_stats(pool: Pool) -> dict:
    """Return a snapshot of key pool metrics."""
    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
        "invalid": pool.status(),
    }


def get_pool_stats(
    sync_engine: Any | None = None,
    async_engine: Any | None = None,
) -> dict:
    """Build a dict of pool stats for the debug endpoint.

    Args:
        sync_engine: The synchronous SQLAlchemy Engine (optional).
        async_engine: The asynchronous SQLAlchemy AsyncEngine (optional).

    Returns:
        Dict with ``"sync"`` and/or ``"async"`` keys, each containing pool
        metric snapshots.
    """
    result: dict = {}

    if sync_engine is not None:
        pool = getattr(sync_engine, "pool", None)
        if pool is not None:
            result["sync"] = _pool_stats(pool)

    if async_engine is not None:
        sync_inner = getattr(async_engine, "sync_engine", None)
        pool = getattr(sync_inner, "pool", None) if sync_inner else None
        if pool is not None:
            result["async"] = _pool_stats(pool)

    return result
