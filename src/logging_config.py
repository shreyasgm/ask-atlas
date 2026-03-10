"""Centralized logging configuration for Ask-Atlas.

Provides a single ``configure_logging()`` entry point that replaces all
scattered ``logging.basicConfig()`` calls.  Supports plain-text output
(local dev) and structured JSON output (Cloud Run / production).

Request-ID correlation is available via a ``contextvars.ContextVar`` so
that every log record emitted during a request automatically includes the
request ID when JSON formatting is active.
"""

import contextvars
import logging
import sys

# ---------------------------------------------------------------------------
# Request-ID context variable
# ---------------------------------------------------------------------------

_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def get_request_id() -> str | None:
    """Return the current request ID, or ``None`` outside a request context."""
    return _request_id_var.get()


def set_request_id(request_id: str | None) -> contextvars.Token:
    """Set the request ID for the current context.  Returns a reset token."""
    return _request_id_var.set(request_id)


# ---------------------------------------------------------------------------
# Logging filter that injects request_id into every record
# ---------------------------------------------------------------------------


class _RequestIdFilter(logging.Filter):
    """Attach ``request_id`` from the contextvar to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_PLAINTEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"


def configure_logging(*, json_format: bool = False, log_level: str = "INFO") -> None:
    """Configure the root logger with a single handler.

    Args:
        json_format: If ``True``, emit JSON lines (for Cloud Run).
                     If ``False``, use human-readable plaintext.
        log_level: Python log-level name (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    root = logging.getLogger()

    # Clear any previously attached handlers (idempotent re-calls are safe)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if json_format:
        from pythonjsonlogger.json import JsonFormatter

        formatter = JsonFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "asctime": "timestamp"},
            timestamp=True,
        )
    else:
        formatter = logging.Formatter(_PLAINTEXT_FORMAT, datefmt="%H:%M:%S")

    handler.setFormatter(formatter)
    handler.addFilter(_RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(log_level.upper())
