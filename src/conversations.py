"""Conversation CRUD layer for Ask-Atlas.

Provides an ABC (``ConversationStore``) with two implementations:

* ``InMemoryConversationStore`` — dict-backed, for dev/test without Postgres.
* ``PostgresConversationStore`` — raw ``psycopg`` async, matches persistence.py style.

Also exposes ``derive_title()`` for auto-generating conversation titles from
the first user message.
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ConversationRow:
    """A single conversation record."""

    id: str
    session_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# derive_title
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?]")


def derive_title(message: str, max_length: int = 50) -> str:
    """Derive a short title from the first user message.

    Strategy: take the first sentence (delimited by ``.``, ``!``, or ``?``),
    then truncate to *max_length* on a word boundary if still too long.

    Args:
        message: The raw user message.
        max_length: Maximum title length in characters.

    Returns:
        A title string, possibly truncated with ``...`` suffix.
    """
    if not message or not message.strip():
        return message

    # First sentence: find earliest sentence-ending punctuation
    match = _SENTENCE_END.search(message)
    if match:
        title = message[: match.end()]
    else:
        title = message

    # Truncate on word boundary if too long
    if len(title) <= max_length:
        return title

    # Reserve space for the "..." suffix
    truncated = title[: max_length - 3]
    # Find last space to avoid cutting mid-word
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "..."


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class ConversationStore(ABC):
    """Abstract interface for conversation persistence."""

    @abstractmethod
    async def create(
        self, thread_id: str, session_id: str, title: str | None
    ) -> ConversationRow:
        """Create a conversation. Idempotent — returns existing if already present."""

    @abstractmethod
    async def list_by_session(self, session_id: str) -> list[ConversationRow]:
        """List conversations for a session, ordered by updated_at DESC."""

    @abstractmethod
    async def get(self, thread_id: str) -> ConversationRow | None:
        """Get a single conversation by thread_id, or None."""

    @abstractmethod
    async def delete(self, thread_id: str) -> None:
        """Delete a conversation. No-op if not found."""

    @abstractmethod
    async def update_timestamp(self, thread_id: str) -> None:
        """Touch updated_at to NOW for a conversation."""


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryConversationStore(ConversationStore):
    """Dict-backed conversation store for dev/test."""

    def __init__(self) -> None:
        self._data: dict[str, ConversationRow] = {}

    async def create(
        self, thread_id: str, session_id: str, title: str | None
    ) -> ConversationRow:
        if thread_id in self._data:
            return self._data[thread_id]
        now = datetime.now(UTC)
        row = ConversationRow(
            id=thread_id,
            session_id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._data[thread_id] = row
        return row

    async def list_by_session(self, session_id: str) -> list[ConversationRow]:
        rows = [r for r in self._data.values() if r.session_id == session_id]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    async def get(self, thread_id: str) -> ConversationRow | None:
        return self._data.get(thread_id)

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)

    async def update_timestamp(self, thread_id: str) -> None:
        row = self._data.get(thread_id)
        if row is not None:
            row.updated_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresConversationStore(ConversationStore):
    """Postgres-backed conversation store using a shared ``AsyncConnectionPool``.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool) -> None:
        self._pool = pool

    def _row_to_conversation(self, row) -> ConversationRow:
        return ConversationRow(
            id=row["id"],
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def create(
        self, thread_id: str, session_id: str, title: str | None
    ) -> ConversationRow:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO conversations (id, session_id, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET updated_at = NOW()
                RETURNING id, session_id, title, created_at, updated_at
                """,
                (thread_id, session_id, title),
            )
            row = await cur.fetchone()
            assert row is not None
            return self._row_to_conversation(row)

    async def list_by_session(self, session_id: str) -> list[ConversationRow]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM conversations WHERE session_id = %s "
                "ORDER BY updated_at DESC",
                (session_id,),
            )
            rows = await cur.fetchall()
            return [self._row_to_conversation(r) for r in rows]

    async def get(self, thread_id: str) -> ConversationRow | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM conversations WHERE id = %s",
                (thread_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return self._row_to_conversation(row)

    async def delete(self, thread_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE id = %s",
                (thread_id,),
            )

    async def update_timestamp(self, thread_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                (thread_id,),
            )
