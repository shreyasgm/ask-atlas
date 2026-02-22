"""Tests for state reducers in src/state.py."""

from src.state import add_turn_summaries


class TestAddTurnSummaries:
    """Tests for the add_turn_summaries reducer."""

    def test_appends_new_to_existing(self) -> None:
        existing = [{"entities": None, "queries": []}]
        new = [{"entities": {"schemas": ["hs92"]}, "queries": [{"sql": "SELECT 1"}]}]
        result = add_turn_summaries(existing, new)
        assert len(result) == 2
        assert result[0] == existing[0]
        assert result[1] == new[0]

    def test_handles_none_existing(self) -> None:
        new = [{"entities": None, "queries": []}]
        result = add_turn_summaries(None, new)
        assert result == new

    def test_handles_none_new(self) -> None:
        existing = [{"entities": None, "queries": []}]
        result = add_turn_summaries(existing, None)
        assert result == existing

    def test_both_none_returns_empty(self) -> None:
        result = add_turn_summaries(None, None)
        assert result == []

    def test_multiple_appends(self) -> None:
        s1 = [{"queries": [{"sql": "Q1"}]}]
        s2 = [{"queries": [{"sql": "Q2"}]}]
        result = add_turn_summaries(add_turn_summaries(None, s1), s2)
        assert len(result) == 2
