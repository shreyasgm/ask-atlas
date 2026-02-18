"""End-to-end integration tests for the query tool pipeline.

Requires a live Atlas database and OpenAI API key.
Markers: @pytest.mark.db, @pytest.mark.integration

NOTE: This file was generated with LLM assistance and needs human review.
Fragile areas: assertions are very loose (only check non-empty + no "error" substring);
these may pass even when the answer is incorrect.
"""

import pytest
from pathlib import Path
from langchain_openai import ChatOpenAI
from sqlalchemy import create_engine

from src.config import get_settings
from src.text_to_sql import AtlasTextToSQL

BASE_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def real_atlas_sql():
    """Shared AtlasTextToSQL instance backed by real DB and LLM."""
    settings = get_settings()
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured")
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY not configured")

    instance = AtlasTextToSQL(
        db_uri=settings.atlas_db_url,
        table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
        table_structure_json=BASE_DIR / "db_table_structure.json",
        queries_json=BASE_DIR / "src/example_queries/queries.json",
        example_queries_dir=BASE_DIR / "src/example_queries",
        max_results=settings.max_results_per_query,
    )
    yield instance
    instance.close()


@pytest.mark.db
@pytest.mark.integration
class TestQueryToolE2E:
    """Full pipeline tests: question → product lookup → SQL → answer."""

    def test_simple_country_query(self, real_atlas_sql):
        """Basic country export question returns a non-empty, error-free answer."""
        answer = real_atlas_sql.answer_question(
            "Top 3 products exported by Bolivia in 2020",
            stream_response=False,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0
        assert "error" not in answer.lower()

    def test_product_mention_query(self, real_atlas_sql):
        """Question mentioning a specific product triggers product lookup."""
        answer = real_atlas_sql.answer_question(
            "How much cotton did India export in 2019?",
            stream_response=False,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0
        assert "error" not in answer.lower()

    def test_bilateral_trade_query(self, real_atlas_sql):
        """Bilateral trade question returns a meaningful answer."""
        answer = real_atlas_sql.answer_question(
            "What did Germany export to France in 2020?",
            stream_response=False,
        )
        assert isinstance(answer, str)
        assert len(answer) > 0
        assert "error" not in answer.lower()
