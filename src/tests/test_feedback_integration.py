"""Integration tests for PostgresFeedbackStore — requires a live app-db.

Run with:
    docker compose -f docker-compose.test.yml up -d --wait
    CHECKPOINT_DB_URL=postgresql://ask_atlas_app:testpass@localhost:5434/ask_atlas_app \
        PYTHONPATH=$(pwd) uv run pytest src/tests/test_feedback_integration.py -v -m db
"""

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from src.feedback import FeedbackRow, PostgresFeedbackStore
from src.persistence import CONVERSATIONS_DDL


@pytest.fixture()
def db_url(checkpoint_db_url: str) -> str:
    """Reuse the conftest checkpoint_db_url fixture."""
    return checkpoint_db_url


@pytest.fixture(autouse=True)
def _ensure_table_and_clean(db_url: str):
    """Create the message_feedback table if needed; truncate before each test."""
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute(CONVERSATIONS_DDL)
            conn.execute("DELETE FROM message_feedback")
            conn.execute("ALTER SEQUENCE message_feedback_id_seq RESTART WITH 1")
            conn.commit()
    except Exception:
        pytest.skip("App DB not reachable")
    yield
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute("DELETE FROM message_feedback")
            conn.commit()
    except Exception:
        pass


@pytest.fixture()
async def pool(db_url: str):
    """Create an async connection pool for the store."""
    p = AsyncConnectionPool(
        db_url,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await p.open()
    yield p
    await p.close()


@pytest.fixture()
def store(pool: AsyncConnectionPool) -> PostgresFeedbackStore:
    return PostgresFeedbackStore(pool)


@pytest.mark.db
class TestPostgresFeedbackStore:
    """Integration tests for PostgresFeedbackStore."""

    @pytest.mark.asyncio
    async def test_create_returns_row(self, store: PostgresFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1")
        assert isinstance(row, FeedbackRow)
        assert row.thread_id == "t1"
        assert row.turn_index == 0
        assert row.rating == 1
        assert row.session_id == "s1"
        assert row.id >= 1

    @pytest.mark.asyncio
    async def test_create_with_comment_and_context(
        self, store: PostgresFeedbackStore
    ) -> None:
        ctx = {"turns": [{"role": "human", "content": "hello"}]}
        row = await store.create("t1", 0, -1, "s1", comment="bad", context=ctx)
        assert row.comment == "bad"
        assert row.context == ctx

    @pytest.mark.asyncio
    async def test_create_upserts_on_conflict(
        self, store: PostgresFeedbackStore
    ) -> None:
        row1 = await store.create("t1", 0, 1, "s1", comment="good")
        row2 = await store.create("t1", 0, -1, "s1", comment="bad")
        assert row1.id == row2.id
        assert row2.rating == -1
        assert row2.comment == "bad"

    @pytest.mark.asyncio
    async def test_different_sessions_separate(
        self, store: PostgresFeedbackStore
    ) -> None:
        row1 = await store.create("t1", 0, 1, "s1")
        row2 = await store.create("t1", 0, -1, "s2")
        assert row1.id != row2.id

    @pytest.mark.asyncio
    async def test_update_changes_rating(self, store: PostgresFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1")
        updated = await store.update(row.id, -1, "s1", comment="changed")
        assert updated is not None
        assert updated.rating == -1
        assert updated.comment == "changed"

    @pytest.mark.asyncio
    async def test_update_refreshes_context(self, store: PostgresFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1", context={"old": True})
        updated = await store.update(row.id, 1, "s1", context={"new": True})
        assert updated is not None
        assert updated.context == {"new": True}

    @pytest.mark.asyncio
    async def test_update_wrong_session_returns_none(
        self, store: PostgresFeedbackStore
    ) -> None:
        row = await store.create("t1", 0, 1, "s1")
        result = await store.update(row.id, -1, "wrong")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(
        self, store: PostgresFeedbackStore
    ) -> None:
        result = await store.update(9999, 1, "s1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_thread(self, store: PostgresFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t1", 1, -1, "s1")
        await store.create("t2", 0, 1, "s1")
        rows = await store.get_by_thread("t1", "s1")
        assert len(rows) == 2
        assert all(r.thread_id == "t1" for r in rows)

    @pytest.mark.asyncio
    async def test_get_by_thread_filters_session(
        self, store: PostgresFeedbackStore
    ) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t1", 1, -1, "s2")
        rows = await store.get_by_thread("t1", "s1")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_get_by_id(self, store: PostgresFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1")
        found = await store.get_by_id(row.id)
        assert found is not None
        assert found.id == row.id

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, store: PostgresFeedbackStore) -> None:
        assert await store.get_by_id(9999) is None

    @pytest.mark.asyncio
    async def test_list_all(self, store: PostgresFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t2", 0, -1, "s2")
        rows = await store.list_all()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_list_all_rating_filter(self, store: PostgresFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t2", 0, -1, "s2")
        rows = await store.list_all(rating=-1)
        assert len(rows) == 1
        assert rows[0].rating == -1

    @pytest.mark.asyncio
    async def test_list_all_pagination(self, store: PostgresFeedbackStore) -> None:
        for i in range(5):
            await store.create(f"t{i}", 0, 1, "s1")
        rows = await store.list_all(limit=2, offset=1)
        assert len(rows) == 2
