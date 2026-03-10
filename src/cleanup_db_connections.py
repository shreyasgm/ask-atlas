"""Inspect and clean up idle connections on the Atlas Data DB.

Usage:
    uv run python -m src.cleanup_db_connections                    # dry-run (default)
    uv run python -m src.cleanup_db_connections --terminate        # actually kill
    uv run python -m src.cleanup_db_connections --idle-minutes 10  # custom age threshold

Dry-run mode (the default) lists connection stats and shows which connections
*would* be terminated, without touching anything.  Pass ``--terminate`` to
actually issue ``pg_terminate_backend`` calls.
"""

import argparse
import logging

import psycopg

from src.config import get_settings
from src.logging_config import configure_logging

logger = logging.getLogger(__name__)

# SQL -------------------------------------------------------------------

_STATS_SQL = """\
SELECT state, usename, count(*)
FROM pg_stat_activity
WHERE usename != 'rdsadmin'
GROUP BY state, usename
ORDER BY usename, state;
"""

_IDLE_CONNECTIONS_SQL = """\
SELECT pid, usename, state,
       extract(epoch FROM (now() - state_change))::int AS idle_seconds,
       application_name, client_addr
FROM pg_stat_activity
WHERE state IN ('idle', 'idle in transaction', 'idle in transaction (aborted)')
  AND usename != 'rdsadmin'
  AND pid != pg_backend_pid()
  AND extract(epoch FROM (now() - state_change)) > %(min_idle_seconds)s
ORDER BY idle_seconds DESC;
"""

_TERMINATE_SQL = """\
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state IN ('idle', 'idle in transaction', 'idle in transaction (aborted)')
  AND usename != 'rdsadmin'
  AND pid != pg_backend_pid()
  AND extract(epoch FROM (now() - state_change)) > %(min_idle_seconds)s;
"""


# Public helpers (unit-testable) ----------------------------------------


def fetch_connection_stats(conn: psycopg.Connection) -> list[tuple]:
    """Return ``(state, usename, count)`` rows from ``pg_stat_activity``."""
    with conn.cursor() as cur:
        cur.execute(_STATS_SQL)
        return cur.fetchall()


def fetch_idle_connections(
    conn: psycopg.Connection,
    min_idle_seconds: int = 300,
) -> list[tuple]:
    """Return idle connections older than *min_idle_seconds*.

    Each row is ``(pid, usename, state, idle_seconds, application_name,
    client_addr)``.
    """
    with conn.cursor() as cur:
        cur.execute(_IDLE_CONNECTIONS_SQL, {"min_idle_seconds": min_idle_seconds})
        return cur.fetchall()


def terminate_idle_connections(
    conn: psycopg.Connection,
    min_idle_seconds: int = 300,
) -> int:
    """Terminate idle connections older than *min_idle_seconds*.

    Returns the number of backends signalled.
    """
    with conn.cursor() as cur:
        cur.execute(_TERMINATE_SQL, {"min_idle_seconds": min_idle_seconds})
        rows = cur.fetchall()
    conn.commit()
    return len(rows)


# CLI -------------------------------------------------------------------


def _configure_logging() -> None:
    configure_logging(json_format=False, log_level="INFO")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect and clean up idle Atlas DB connections."
    )
    parser.add_argument(
        "--terminate",
        action="store_true",
        default=False,
        help="Actually terminate idle connections (default is dry-run).",
    )
    parser.add_argument(
        "--idle-minutes",
        type=int,
        default=5,
        help="Only target connections idle for longer than this (default: 5).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for the cleanup script."""
    _configure_logging()
    args = _parse_args(argv)
    min_idle_seconds = args.idle_minutes * 60

    settings = get_settings()
    db_url = settings.atlas_db_url

    logger.info("Connecting to Atlas DB …")
    with psycopg.connect(db_url, autocommit=False) as conn:
        # 1. Show connection stats
        stats = fetch_connection_stats(conn)
        logger.info("Connections by state and user:")
        for state, user, count in stats:
            logger.info("  %-10s | %-30s | %3d", user, state or "null", count)

        # 2. Show idle connections matching the age filter
        idle = fetch_idle_connections(conn, min_idle_seconds)
        logger.info(
            "Idle connections older than %d min: %d",
            args.idle_minutes,
            len(idle),
        )
        for pid, user, state, secs, app, addr in idle:
            logger.info(
                "  pid=%s  user=%s  state=%s  idle=%ds  app=%s  addr=%s",
                pid,
                user,
                state,
                secs,
                app,
                addr,
            )

        # 3. Terminate (or dry-run)
        if args.terminate:
            killed = terminate_idle_connections(conn, min_idle_seconds)
            logger.info("Terminated %d connection(s).", killed)
        else:
            logger.info("Dry-run mode — pass --terminate to actually kill connections.")


if __name__ == "__main__":
    main()
