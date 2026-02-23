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
from datetime import datetime, timezone

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
        now = datetime.now(timezone.utc)
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
            row.updated_at = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresConversationStore(ConversationStore):
    """Postgres-backed conversation store using raw psycopg async.

    Args:
        db_url: Postgres connection string.
    """

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    async def create(
        self, thread_id: str, session_id: str, title: str | None
    ) -> ConversationRow:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._db_url) as conn:
            await conn.execute(
                """
                INSERT INTO conversations (id, session_id, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (thread_id, session_id, title),
            )
            await conn.commit()

            cur = await conn.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM conversations WHERE id = %s",
                (thread_id,),
            )
            row = await cur.fetchone()
            assert row is not None
            return ConversationRow(
                id=row[0],
                session_id=row[1],
                title=row[2],
                created_at=row[3],
                updated_at=row[4],
            )

    async def list_by_session(self, session_id: str) -> list[ConversationRow]:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._db_url) as conn:
            cur = await conn.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM conversations WHERE session_id = %s "
                "ORDER BY updated_at DESC",
                (session_id,),
            )
            rows = await cur.fetchall()
            return [
                ConversationRow(
                    id=r[0],
                    session_id=r[1],
                    title=r[2],
                    created_at=r[3],
                    updated_at=r[4],
                )
                for r in rows
            ]

    async def get(self, thread_id: str) -> ConversationRow | None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._db_url) as conn:
            cur = await conn.execute(
                "SELECT id, session_id, title, created_at, updated_at "
                "FROM conversations WHERE id = %s",
                (thread_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return ConversationRow(
                id=row[0],
                session_id=row[1],
                title=row[2],
                created_at=row[3],
                updated_at=row[4],
            )

    async def delete(self, thread_id: str) -> None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._db_url) as conn:
            await conn.execute(
                "DELETE FROM conversations WHERE id = %s",
                (thread_id,),
            )
            await conn.commit()

    async def update_timestamp(self, thread_id: str) -> None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._db_url) as conn:
            await conn.execute(
                "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                (thread_id,),
            )
            await conn.commit()
