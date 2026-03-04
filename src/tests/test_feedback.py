"""Tests for the feedback store layer — no DB, no LLM.

All tests run against InMemoryFeedbackStore.
"""

import pytest

from src.feedback import (
    FeedbackRow,
    InMemoryFeedbackStore,
    rating_from_str,
    rating_to_str,
)


@pytest.fixture()
def store() -> InMemoryFeedbackStore:
    return InMemoryFeedbackStore()


class TestRatingHelpers:
    """Test rating ↔ string conversion helpers."""

    def test_up_to_int(self) -> None:
        assert rating_from_str("up") == 1

    def test_down_to_int(self) -> None:
        assert rating_from_str("down") == -1

    def test_int_to_up(self) -> None:
        assert rating_to_str(1) == "up"

    def test_int_to_down(self) -> None:
        assert rating_to_str(-1) == "down"

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError):
            rating_from_str("neutral")

    def test_invalid_int_raises(self) -> None:
        with pytest.raises(ValueError):
            rating_to_str(0)


class TestCreate:
    """Test FeedbackStore.create (upsert semantics)."""

    @pytest.mark.anyio
    async def test_create_returns_row(self, store: InMemoryFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1")
        assert isinstance(row, FeedbackRow)
        assert row.thread_id == "t1"
        assert row.turn_index == 0
        assert row.rating == 1
        assert row.session_id == "s1"
        assert row.id == 1

    @pytest.mark.anyio
    async def test_create_with_comment_and_context(
        self, store: InMemoryFeedbackStore
    ) -> None:
        ctx = {"turns": [{"role": "human", "content": "hello"}]}
        row = await store.create("t1", 0, -1, "s1", comment="bad", context=ctx)
        assert row.comment == "bad"
        assert row.context == ctx

    @pytest.mark.anyio
    async def test_create_upserts_on_conflict(
        self, store: InMemoryFeedbackStore
    ) -> None:
        row1 = await store.create("t1", 0, 1, "s1", comment="good")
        row2 = await store.create("t1", 0, -1, "s1", comment="actually bad")
        assert row1.id == row2.id
        assert row2.rating == -1
        assert row2.comment == "actually bad"

    @pytest.mark.anyio
    async def test_different_sessions_create_separate_rows(
        self, store: InMemoryFeedbackStore
    ) -> None:
        row1 = await store.create("t1", 0, 1, "s1")
        row2 = await store.create("t1", 0, -1, "s2")
        assert row1.id != row2.id


class TestUpdate:
    """Test FeedbackStore.update."""

    @pytest.mark.anyio
    async def test_update_changes_rating_and_comment(
        self, store: InMemoryFeedbackStore
    ) -> None:
        row = await store.create("t1", 0, 1, "s1", comment="good")
        updated = await store.update(row.id, -1, "s1", comment="nope")
        assert updated is not None
        assert updated.rating == -1
        assert updated.comment == "nope"

    @pytest.mark.anyio
    async def test_update_refreshes_context(self, store: InMemoryFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1", context={"old": True})
        updated = await store.update(row.id, 1, "s1", context={"new": True})
        assert updated is not None
        assert updated.context == {"new": True}

    @pytest.mark.anyio
    async def test_update_nonexistent_returns_none(
        self, store: InMemoryFeedbackStore
    ) -> None:
        result = await store.update(999, 1, "s1")
        assert result is None

    @pytest.mark.anyio
    async def test_update_wrong_session_returns_none(
        self, store: InMemoryFeedbackStore
    ) -> None:
        row = await store.create("t1", 0, 1, "s1")
        result = await store.update(row.id, -1, "wrong-session")
        assert result is None


class TestGetByThread:
    """Test FeedbackStore.get_by_thread."""

    @pytest.mark.anyio
    async def test_returns_matching_rows(self, store: InMemoryFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t1", 1, -1, "s1")
        await store.create("t2", 0, 1, "s1")  # different thread
        rows = await store.get_by_thread("t1", "s1")
        assert len(rows) == 2
        assert all(r.thread_id == "t1" for r in rows)

    @pytest.mark.anyio
    async def test_filters_by_session(self, store: InMemoryFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t1", 1, -1, "s2")
        rows = await store.get_by_thread("t1", "s1")
        assert len(rows) == 1
        assert rows[0].session_id == "s1"


class TestGetById:
    """Test FeedbackStore.get_by_id."""

    @pytest.mark.anyio
    async def test_returns_row(self, store: InMemoryFeedbackStore) -> None:
        row = await store.create("t1", 0, 1, "s1")
        found = await store.get_by_id(row.id)
        assert found is not None
        assert found.id == row.id

    @pytest.mark.anyio
    async def test_returns_none_for_missing(self, store: InMemoryFeedbackStore) -> None:
        assert await store.get_by_id(999) is None


class TestListAll:
    """Test FeedbackStore.list_all."""

    @pytest.mark.anyio
    async def test_returns_all(self, store: InMemoryFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t2", 0, -1, "s2")
        rows = await store.list_all()
        assert len(rows) == 2

    @pytest.mark.anyio
    async def test_filters_by_rating(self, store: InMemoryFeedbackStore) -> None:
        await store.create("t1", 0, 1, "s1")
        await store.create("t2", 0, -1, "s2")
        rows = await store.list_all(rating=-1)
        assert len(rows) == 1
        assert rows[0].rating == -1

    @pytest.mark.anyio
    async def test_pagination(self, store: InMemoryFeedbackStore) -> None:
        for i in range(5):
            await store.create(f"t{i}", 0, 1, "s1")
        rows = await store.list_all(limit=2, offset=1)
        assert len(rows) == 2
        assert rows[0].id == 2  # offset=1 skips id=1
