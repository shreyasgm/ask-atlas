"""Tests for the conversation store layer — no DB required.

Covers InMemoryConversationStore CRUD operations and the derive_title utility.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from src.conversations import (
    ConversationRow,
    ConversationStore,
    InMemoryConversationStore,
    derive_title,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine in a fresh event loop (test helper)."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def store() -> InMemoryConversationStore:
    return InMemoryConversationStore()


# ---------------------------------------------------------------------------
# TestDeriveTitle
# ---------------------------------------------------------------------------


class TestDeriveTitle:
    """Tests for the derive_title() pure function."""

    def test_short_message_returned_as_is(self) -> None:
        assert derive_title("Hello world") == "Hello world"

    def test_first_sentence_extracted(self) -> None:
        result = derive_title("Top exports of Brazil. What about Argentina?")
        assert result == "Top exports of Brazil."

    def test_truncated_at_max_length(self) -> None:
        long = "a " * 40  # 80 chars
        result = derive_title(long, max_length=50)
        assert len(result) <= 50

    def test_truncation_respects_word_boundary(self) -> None:
        msg = "What are the top twenty exported products from Brazil in 2020"
        result = derive_title(msg, max_length=30)
        assert len(result) <= 30
        # Should not cut a word in half
        assert not result[-1].isalpha() or result == msg[:30]

    def test_empty_message(self) -> None:
        assert derive_title("") == ""

    def test_whitespace_only(self) -> None:
        result = derive_title("   ")
        assert result.strip() == ""

    def test_question_mark_ends_sentence(self) -> None:
        result = derive_title("What are exports? Tell me more.")
        assert result == "What are exports?"

    def test_exclamation_mark_ends_sentence(self) -> None:
        result = derive_title("Show me data! Now please.")
        assert result == "Show me data!"


# ---------------------------------------------------------------------------
# TestInMemoryConversationStore
# ---------------------------------------------------------------------------


class TestInMemoryConversationStore:
    """Tests for the in-memory implementation of ConversationStore."""

    @pytest.mark.asyncio
    async def test_create_returns_row(self, store: InMemoryConversationStore) -> None:
        row = await store.create("t1", "s1", "My title")
        assert row.id == "t1"
        assert row.session_id == "s1"
        assert row.title == "My title"

    @pytest.mark.asyncio
    async def test_create_sets_timestamps(self, store: InMemoryConversationStore) -> None:
        row = await store.create("t1", "s1", "title")
        assert row.created_at is not None
        assert row.updated_at is not None
        assert row.created_at <= row.updated_at

    @pytest.mark.asyncio
    async def test_get_existing(self, store: InMemoryConversationStore) -> None:
        await store.create("t1", "s1", "title")
        row = await store.get("t1")
        assert row is not None
        assert row.id == "t1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store: InMemoryConversationStore) -> None:
        row = await store.get("nope")
        assert row is None

    @pytest.mark.asyncio
    async def test_list_by_session(self, store: InMemoryConversationStore) -> None:
        await store.create("t1", "s1", "First")
        await store.create("t2", "s1", "Second")
        await store.create("t3", "s2", "Other session")
        rows = await store.list_by_session("s1")
        assert len(rows) == 2
        ids = {r.id for r in rows}
        assert ids == {"t1", "t2"}

    @pytest.mark.asyncio
    async def test_list_by_session_ordered_by_updated_desc(
        self, store: InMemoryConversationStore
    ) -> None:
        await store.create("t1", "s1", "First")
        await store.create("t2", "s1", "Second")
        # Touch t1 so it becomes more recent
        await store.update_timestamp("t1")
        rows = await store.list_by_session("s1")
        assert rows[0].id == "t1"
        assert rows[1].id == "t2"

    @pytest.mark.asyncio
    async def test_list_by_session_empty(self, store: InMemoryConversationStore) -> None:
        rows = await store.list_by_session("no-session")
        assert rows == []

    @pytest.mark.asyncio
    async def test_delete_existing(self, store: InMemoryConversationStore) -> None:
        await store.create("t1", "s1", "title")
        await store.delete("t1")
        row = await store.get("t1")
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, store: InMemoryConversationStore) -> None:
        # Should not raise
        await store.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_update_timestamp(self, store: InMemoryConversationStore) -> None:
        row = await store.create("t1", "s1", "title")
        old_updated = row.updated_at
        await store.update_timestamp("t1")
        row = await store.get("t1")
        assert row is not None
        assert row.updated_at >= old_updated

    @pytest.mark.asyncio
    async def test_idempotent_create(self, store: InMemoryConversationStore) -> None:
        """Creating the same thread_id twice should not raise or overwrite."""
        row1 = await store.create("t1", "s1", "Original")
        row2 = await store.create("t1", "s1", "Duplicate")
        # Should return the existing row
        assert row2.title == "Original"
        rows = await store.list_by_session("s1")
        assert len(rows) == 1
