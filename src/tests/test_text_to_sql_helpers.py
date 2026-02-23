"""Unit tests for pure helper functions in text_to_sql.py.

These are static/module-level functions with real transformation logic
that never had direct test coverage.
"""

import pytest

from src.text_to_sql import (
    AtlasTextToSQL,
    _extract_tables_from_sql,
)

# ---------------------------------------------------------------------------
# _extract_tables_from_sql — SQL parsing for table names
# ---------------------------------------------------------------------------


class TestExtractTablesFromSql:
    """Used by pipeline_state events to report which tables a query touched."""

    def test_simple_select(self):
        sql = "SELECT * FROM hs92.country_year WHERE year = 2020"
        assert _extract_tables_from_sql(sql) == ["hs92.country_year"]

    def test_join_extracts_both_tables(self):
        sql = """
        SELECT c.name, t.export_value
        FROM hs92.country_product_year_4 t
        JOIN classification.location_country c ON c.country_id = t.country_id
        """
        result = _extract_tables_from_sql(sql)
        assert "classification.location_country" in result
        assert "hs92.country_product_year_4" in result

    def test_multiple_schemas(self):
        sql = """
        SELECT * FROM hs92.country_year
        UNION ALL
        SELECT * FROM sitc.country_year
        """
        result = _extract_tables_from_sql(sql)
        assert "hs92.country_year" in result
        assert "sitc.country_year" in result

    def test_empty_string_returns_empty(self):
        assert _extract_tables_from_sql("") == []

    def test_whitespace_only_returns_empty(self):
        assert _extract_tables_from_sql("   ") == []

    def test_invalid_sql_returns_empty(self):
        assert _extract_tables_from_sql("NOT VALID SQL !!!") == []


# ---------------------------------------------------------------------------
# AtlasTextToSQL._turn_input — builds the input dict for each conversational turn
# ---------------------------------------------------------------------------


class TestTurnInput:
    """_turn_input resets per-turn counters so Turn N doesn't inherit
    Turn N-1's state from the checkpoint. Getting this wrong causes
    the agent to think it already used all its queries."""

    def test_resets_queries_executed_to_zero(self):
        result = AtlasTextToSQL._turn_input("What did Brazil export?")
        assert result["queries_executed"] == 0

    def test_resets_last_error(self):
        result = AtlasTextToSQL._turn_input("test")
        assert result["last_error"] == ""

    def test_resets_retry_count(self):
        result = AtlasTextToSQL._turn_input("test")
        assert result["retry_count"] == 0

    def test_wraps_question_in_human_message(self):
        result = AtlasTextToSQL._turn_input("Coffee exports from Colombia")
        messages = result["messages"]
        assert len(messages) == 1
        assert messages[0].content == "Coffee exports from Colombia"

    def test_overrides_passed_through(self):
        result = AtlasTextToSQL._turn_input(
            "test",
            override_schema="hs92",
            override_direction="exports",
            override_mode="goods",
        )
        assert result["override_schema"] == "hs92"
        assert result["override_direction"] == "exports"
        assert result["override_mode"] == "goods"

    def test_overrides_default_to_none(self):
        result = AtlasTextToSQL._turn_input("test")
        assert result["override_schema"] is None
        assert result["override_direction"] is None
        assert result["override_mode"] is None


# ---------------------------------------------------------------------------
# AtlasTextToSQL._extract_text — integration tests against real LLM providers
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExtractTextWithRealProviders:
    """_extract_text normalizes LLM response content to a plain string.

    Different providers return content in different formats (plain string
    vs list-of-blocks). These tests call real provider APIs and verify
    _extract_text handles whatever they actually return.
    """

    PROMPT = "Reply with exactly: hello"

    @staticmethod
    def _get_raw_content(provider: str, model: str) -> object:
        """Call a real LLM and return the raw .content from the response."""
        from src.config import create_llm

        # Gemini 2.5 uses "thinking" tokens that consume budget before
        # producing visible output, so we need a generous max_tokens.
        llm = create_llm(model, provider, temperature=0, max_tokens=256)
        response = llm.invoke("What is 2+2? Answer with just the number.")
        return response.content

    def test_openai_content_handled(self):
        raw = self._get_raw_content("openai", "gpt-4.1-nano")
        result = AtlasTextToSQL._extract_text(raw)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_anthropic_content_handled(self):
        raw = self._get_raw_content("anthropic", "claude-haiku-4-5")
        result = AtlasTextToSQL._extract_text(raw)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_google_content_handled(self):
        raw = self._get_raw_content("google-genai", "gemini-2.5-flash")
        result = AtlasTextToSQL._extract_text(raw)
        assert isinstance(result, str)
        assert len(result) > 0
