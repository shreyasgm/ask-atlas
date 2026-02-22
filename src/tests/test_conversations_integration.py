"""Integration tests for PostgresConversationStore — requires a live app-db.

Run with:
    docker compose -f docker-compose.test.yml up -d --wait
    CHECKPOINT_DB_URL=postgresql://ask_atlas_app:testpass@localhost:5434/ask_atlas_app \
        PYTHONPATH=$(pwd) uv run pytest src/tests/test_conversations_integration.py -v -m db
"""

import asyncio
import uuid

import psycopg
import pytest

from src.conversations import PostgresConversationStore
from src.persistence import CONVERSATIONS_DDL


@pytest.fixture()
def db_url(checkpoint_db_url: str) -> str:
    """Reuse the conftest checkpoint_db_url fixture."""
    return checkpoint_db_url


@pytest.fixture(autouse=True)
def _ensure_table_and_clean(db_url: str):
    """Create the conversations table if needed; truncate before each test."""
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute(CONVERSATIONS_DDL)
            conn.execute("DELETE FROM conversations")
            conn.commit()
    except Exception:
        pytest.skip("App DB not reachable")
    yield
    # Clean up after test
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute("DELETE FROM conversations")
            conn.commit()
    except Exception:
        pass


@pytest.fixture()
def store(db_url: str) -> PostgresConversationStore:
    return PostgresConversationStore(db_url)


@pytest.mark.db
class TestPostgresConversationStore:
    """Integration tests for PostgresConversationStore."""

    @pytest.mark.asyncio
    async def test_create_and_get(self, store: PostgresConversationStore) -> None:
        row = await store.create("t1", "s1", "My title")
        assert row.id == "t1"
        assert row.session_id == "s1"
        assert row.title == "My title"

        fetched = await store.get("t1")
        assert fetched is not None
        assert fetched.id == "t1"

    @pytest.mark.asyncio
    async def test_idempotent_create(self, store: PostgresConversationStore) -> None:
        await store.create("t1", "s1", "Original")
        row2 = await store.create("t1", "s1", "Duplicate")
        assert row2.title == "Original"

    @pytest.mark.asyncio
    async def test_list_by_session(self, store: PostgresConversationStore) -> None:
        await store.create("t1", "s1", "First")
        await store.create("t2", "s1", "Second")
        await store.create("t3", "s2", "Other")

        rows = await store.list_by_session("s1")
        assert len(rows) == 2
        ids = {r.id for r in rows}
        assert ids == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_list_ordered_by_updated_desc(
        self, store: PostgresConversationStore
    ) -> None:
        await store.create("t1", "s1", "First")
        await store.create("t2", "s1", "Second")
        # Touch t1 so its updated_at is more recent
        await store.update_timestamp("t1")

        rows = await store.list_by_session("s1")
        assert rows[0].id == "t1"
        assert rows[1].id == "t2"

    @pytest.mark.asyncio
    async def test_delete(self, store: PostgresConversationStore) -> None:
        await store.create("t1", "s1", "Doomed")
        await store.delete("t1")
        row = await store.get("t1")
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(
        self, store: PostgresConversationStore
    ) -> None:
        # Should not raise
        await store.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(
        self, store: PostgresConversationStore
    ) -> None:
        row = await store.get(str(uuid.uuid4()))
        assert row is None
