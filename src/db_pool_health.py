"""Connection pool health monitoring for the Atlas Data DB.

Provides SQLAlchemy pool event listeners that track checkout timing and
invalidation events, a rolling window of query metrics, and a helper to
surface pool statistics via the debug endpoint.
"""

import logging
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.pool import Pool

logger = logging.getLogger(__name__)

# Threshold (seconds) before a held connection triggers a warning.
_SLOW_CHECKOUT_THRESHOLD = 5.0

# Rolling window size for query and connection metrics.
_METRICS_WINDOW = 200


# ---------------------------------------------------------------------------
# Rolling metrics store (module-level singleton, resets on process restart)
# ---------------------------------------------------------------------------


@dataclass
class QueryRecord:
    """A single recorded query execution."""

    timestamp: float  # time.time()
    elapsed_ms: float
    sql_preview: str  # first ~200 chars
    engine_type: str  # "sync" or "async"


@dataclass
class ConnectionRecord:
    """A single recorded connection hold period."""

    timestamp: float
    held_seconds: float
    engine_label: str  # "sync" or "async"


@dataclass
class _MetricsStore:
    """In-memory rolling window of query and connection metrics."""

    queries: deque = field(default_factory=lambda: deque(maxlen=_METRICS_WINDOW))
    connections: deque = field(default_factory=lambda: deque(maxlen=_METRICS_WINDOW))

    def record_query(
        self, elapsed_ms: float, sql_preview: str, engine_type: str = "async"
    ) -> None:
        """Record a completed query execution."""
        self.queries.append(
            QueryRecord(
                timestamp=time.time(),
                elapsed_ms=elapsed_ms,
                sql_preview=sql_preview,
                engine_type=engine_type,
            )
        )

    def record_connection(
        self, held_seconds: float, engine_label: str = "unknown"
    ) -> None:
        """Record a connection hold period."""
        self.connections.append(
            ConnectionRecord(
                timestamp=time.time(),
                held_seconds=held_seconds,
                engine_label=engine_label,
            )
        )

    def query_latency_summary(self) -> dict:
        """Return latency percentiles for recorded queries."""
        if not self.queries:
            return {"count": 0}
        values = [q.elapsed_ms for q in self.queries]
        return {
            "count": len(values),
            "avg_ms": round(statistics.mean(values), 1),
            "p50_ms": round(statistics.median(values), 1),
            "p95_ms": round(sorted(values)[int(len(values) * 0.95)], 1),
            "max_ms": round(max(values), 1),
            "min_ms": round(min(values), 1),
        }

    def connection_hold_summary(self) -> dict:
        """Return hold-time percentiles for recorded connections."""
        if not self.connections:
            return {"count": 0}
        values = [c.held_seconds for c in self.connections]
        return {
            "count": len(values),
            "avg_s": round(statistics.mean(values), 3),
            "p50_s": round(statistics.median(values), 3),
            "p95_s": round(sorted(values)[int(len(values) * 0.95)], 3),
            "max_s": round(max(values), 3),
        }

    def recent_slow_queries(self, threshold_ms: float = 5000, limit: int = 5) -> list:
        """Return the most recent queries above a latency threshold."""
        slow = [q for q in self.queries if q.elapsed_ms >= threshold_ms]
        return [
            {
                "elapsed_ms": round(q.elapsed_ms, 1),
                "sql_preview": q.sql_preview,
                "engine_type": q.engine_type,
            }
            for q in list(slow)[-limit:]
        ]


# Module-level singleton — survives across requests, resets on worker restart.
metrics = _MetricsStore()


# ---------------------------------------------------------------------------
# Pool event listeners
# ---------------------------------------------------------------------------


def _on_checkin(dbapi_conn: Any, connection_record: Any) -> None:
    """Log connection hold duration and warn if slow."""
    checkout_time = connection_record.info.pop("checkout_time", None)
    if checkout_time is None:
        return
    held_seconds = time.monotonic() - checkout_time

    engine_label = connection_record.info.get("engine_label", "unknown")

    # Always record in metrics store
    metrics.record_connection(held_seconds, engine_label)

    # Debug log every checkin with hold time
    logger.debug(
        "Connection returned  engine=%s  held=%.3fs",
        engine_label,
        held_seconds,
    )

    if held_seconds > _SLOW_CHECKOUT_THRESHOLD:
        logger.warning(
            "Connection held for %.1fs (threshold %.0fs) — possible leak  engine=%s",
            held_seconds,
            _SLOW_CHECKOUT_THRESHOLD,
            engine_label,
        )


def _on_invalidate(
    dbapi_conn: Any, connection_record: Any, exception: BaseException | None
) -> None:
    """Log when a connection is invalidated (e.g. broken pipe)."""
    if exception is not None:
        logger.warning("Connection invalidated due to: %s", exception)
    else:
        logger.info("Connection invalidated (soft)")


def attach_pool_listeners(engine: Any, label: str = "unknown") -> None:
    """Register checkout/checkin/invalidate listeners on *engine*'s pool.

    Works for both sync ``Engine`` and async ``AsyncEngine`` (the async
    variant exposes its underlying sync pool via ``.pool``).

    Args:
        engine: A SQLAlchemy ``Engine`` or ``AsyncEngine``.
        label: Human-readable label for this engine ("sync" or "async").
    """
    pool = getattr(engine, "pool", None)
    if pool is None:
        # AsyncEngine stores the pool on the inner sync engine.
        sync_engine = getattr(engine, "sync_engine", None)
        pool = getattr(sync_engine, "pool", None) if sync_engine else None
    if pool is None:
        logger.warning("Could not find pool on engine %r — skipping listeners", engine)
        return

    def _labeled_checkout(
        dbapi_conn: Any, connection_record: Any, connection_proxy: Any
    ) -> None:
        connection_record.info["checkout_time"] = time.monotonic()
        connection_record.info["engine_label"] = label

    event.listen(pool, "checkout", _labeled_checkout)
    event.listen(pool, "checkin", _on_checkin)
    event.listen(pool, "invalidate", _on_invalidate)
    logger.debug("Pool listeners attached to %r (label=%s)", engine, label)


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
        Dict with pool snapshots, query latency summary, connection hold
        summary, and recent slow queries.
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

    # Rolling metrics
    result["query_latency"] = metrics.query_latency_summary()
    result["connection_hold"] = metrics.connection_hold_summary()
    result["recent_slow_queries"] = metrics.recent_slow_queries()

    return result
