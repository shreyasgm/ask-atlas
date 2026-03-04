"""Feedback store layer for Ask-Atlas.

Provides an ABC (``FeedbackStore``) with two implementations:

* ``InMemoryFeedbackStore`` — dict-backed, for dev/test without Postgres.
* ``PostgresFeedbackStore`` — uses an ``AsyncConnectionPool`` from psycopg_pool.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FeedbackRow:
    """A single feedback record."""

    id: int
    thread_id: str
    turn_index: int
    rating: int  # 1 = up, -1 = down
    comment: str | None
    context: dict | None
    session_id: str
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Rating helpers
# ---------------------------------------------------------------------------


def rating_from_str(s: str) -> int:
    """Convert ``"up"``/``"down"`` to ``1``/``-1``."""
    if s == "up":
        return 1
    if s == "down":
        return -1
    raise ValueError(f"Invalid rating string: {s!r}")


def rating_to_str(r: int) -> str:
    """Convert ``1``/``-1`` to ``"up"``/``"down"``."""
    if r == 1:
        return "up"
    if r == -1:
        return "down"
    raise ValueError(f"Invalid rating int: {r!r}")


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------


class FeedbackStore(ABC):
    """Abstract base class for feedback persistence."""

    @abstractmethod
    async def create(
        self,
        thread_id: str,
        turn_index: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow:
        """Create or upsert a feedback row (ON CONFLICT by thread+turn+session)."""

    @abstractmethod
    async def update(
        self,
        feedback_id: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow | None:
        """Update a feedback row. Returns None if not found or wrong session."""

    @abstractmethod
    async def get_by_thread(self, thread_id: str, session_id: str) -> list[FeedbackRow]:
        """Return all feedback for a thread by a given session."""

    @abstractmethod
    async def get_by_id(self, feedback_id: int) -> FeedbackRow | None:
        """Return a single feedback row by ID."""

    @abstractmethod
    async def list_all(
        self,
        rating: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackRow]:
        """List feedback rows with optional rating filter and pagination."""


# ---------------------------------------------------------------------------
# InMemoryFeedbackStore
# ---------------------------------------------------------------------------


class InMemoryFeedbackStore(FeedbackStore):
    """Dict-backed feedback store for testing."""

    def __init__(self) -> None:
        self._rows: dict[int, FeedbackRow] = {}
        self._next_id = 1
        # Index for upsert: (thread_id, turn_index, session_id) -> id
        self._upsert_index: dict[tuple[str, int, str], int] = {}

    async def create(
        self,
        thread_id: str,
        turn_index: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow:
        key = (thread_id, turn_index, session_id)
        now = datetime.now(timezone.utc)

        existing_id = self._upsert_index.get(key)
        if existing_id is not None:
            row = self._rows[existing_id]
            row.rating = rating
            row.comment = comment
            row.context = context
            row.updated_at = now
            return row

        row_id = self._next_id
        self._next_id += 1
        row = FeedbackRow(
            id=row_id,
            thread_id=thread_id,
            turn_index=turn_index,
            rating=rating,
            comment=comment,
            context=context,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )
        self._rows[row_id] = row
        self._upsert_index[key] = row_id
        return row

    async def update(
        self,
        feedback_id: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow | None:
        row = self._rows.get(feedback_id)
        if row is None or row.session_id != session_id:
            return None
        row.rating = rating
        row.comment = comment
        if context is not None:
            row.context = context
        row.updated_at = datetime.now(timezone.utc)
        return row

    async def get_by_thread(self, thread_id: str, session_id: str) -> list[FeedbackRow]:
        return [
            r
            for r in self._rows.values()
            if r.thread_id == thread_id and r.session_id == session_id
        ]

    async def get_by_id(self, feedback_id: int) -> FeedbackRow | None:
        return self._rows.get(feedback_id)

    async def list_all(
        self,
        rating: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackRow]:
        rows = list(self._rows.values())
        if rating is not None:
            rows = [r for r in rows if r.rating == rating]
        return rows[offset : offset + limit]


# ---------------------------------------------------------------------------
# PostgresFeedbackStore
# ---------------------------------------------------------------------------


class PostgresFeedbackStore(FeedbackStore):
    """Postgres-backed feedback store using an AsyncConnectionPool."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def _row_to_feedback(self, row: dict) -> FeedbackRow:
        return FeedbackRow(
            id=row["id"],
            thread_id=row["thread_id"],
            turn_index=row["turn_index"],
            rating=row["rating"],
            comment=row["comment"],
            context=row["context"],
            session_id=row["session_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def create(
        self,
        thread_id: str,
        turn_index: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow:
        from psycopg.types.json import Jsonb

        sql = """\
            INSERT INTO message_feedback
                (thread_id, turn_index, rating, comment, context, session_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (thread_id, turn_index, session_id)
            DO UPDATE SET rating = EXCLUDED.rating,
                          comment = EXCLUDED.comment,
                          context = EXCLUDED.context,
                          updated_at = NOW()
            RETURNING id, thread_id, turn_index, rating, comment, context,
                      session_id, created_at, updated_at
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                sql,
                (thread_id, turn_index, rating, comment, Jsonb(context), session_id),
            )
            row = await cur.fetchone()
            return self._row_to_feedback(row)

    async def update(
        self,
        feedback_id: int,
        rating: int,
        session_id: str,
        comment: str | None = None,
        context: dict | None = None,
    ) -> FeedbackRow | None:
        from psycopg.types.json import Jsonb

        sql = """\
            UPDATE message_feedback
            SET rating = %s, comment = %s, context = %s, updated_at = NOW()
            WHERE id = %s AND session_id = %s
            RETURNING id, thread_id, turn_index, rating, comment, context,
                      session_id, created_at, updated_at
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                sql, (rating, comment, Jsonb(context), feedback_id, session_id)
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return self._row_to_feedback(row)

    async def get_by_thread(self, thread_id: str, session_id: str) -> list[FeedbackRow]:
        sql = """\
            SELECT id, thread_id, turn_index, rating, comment, context,
                   session_id, created_at, updated_at
            FROM message_feedback
            WHERE thread_id = %s AND session_id = %s
            ORDER BY turn_index
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, (thread_id, session_id))
            rows = await cur.fetchall()
            return [self._row_to_feedback(r) for r in rows]

    async def get_by_id(self, feedback_id: int) -> FeedbackRow | None:
        sql = """\
            SELECT id, thread_id, turn_index, rating, comment, context,
                   session_id, created_at, updated_at
            FROM message_feedback
            WHERE id = %s
        """
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, (feedback_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            return self._row_to_feedback(row)

    async def list_all(
        self,
        rating: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[FeedbackRow]:
        if rating is not None:
            sql = """\
                SELECT id, thread_id, turn_index, rating, comment, context,
                       session_id, created_at, updated_at
                FROM message_feedback
                WHERE rating = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            params = (rating, limit, offset)
        else:
            sql = """\
                SELECT id, thread_id, turn_index, rating, comment, context,
                       session_id, created_at, updated_at
                FROM message_feedback
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """
            params = (limit, offset)

        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
            return [self._row_to_feedback(r) for r in rows]
