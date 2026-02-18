"""Integration tests for checkpoint persistence.

Tests real PostgresSaver connectivity and MemorySaver fallback behavior.

NOTE: This file was generated with LLM assistance and needs human review.
Fragile areas: test_checkpointer_round_trip_with_agent asserts specific words
in LLM output (non-deterministic).
"""

import pytest
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from src.config import get_settings
from src.persistence import CheckpointerManager
from src.text_to_sql import AtlasTextToSQL

BASE_DIR = Path(__file__).resolve().parents[2]


@pytest.mark.db
class TestPersistenceIntegration:
    """Validate real checkpointer behavior against Postgres and fallback."""

    def test_checkpointer_with_real_checkpoint_db(self):
        """PostgresSaver is used when CHECKPOINT_DB_URL is configured."""
        settings = get_settings()
        if not settings.checkpoint_db_url:
            pytest.skip("CHECKPOINT_DB_URL not configured")

        manager = CheckpointerManager(db_url=settings.checkpoint_db_url)
        try:
            cp = manager.checkpointer
            assert not isinstance(cp, MemorySaver)
        finally:
            manager.close()

    def test_checkpointer_fallback_with_bad_url(self):
        """Bad URL gracefully falls back to MemorySaver (no crash)."""
        manager = CheckpointerManager(db_url="postgresql://invalid:5432/nope")
        try:
            cp = manager.checkpointer
            assert isinstance(cp, MemorySaver)
        finally:
            manager.close()

    @pytest.mark.integration
    def test_checkpointer_round_trip_with_agent(self):
        """Ask a question, follow up, verify context is retained."""
        settings = get_settings()
        if not settings.checkpoint_db_url:
            pytest.skip("CHECKPOINT_DB_URL not configured")
        if not settings.openai_api_key:
            pytest.skip("OPENAI_API_KEY not configured")

        atlas_sql = AtlasTextToSQL(
            db_uri=settings.atlas_db_url,
            table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
            table_structure_json=BASE_DIR / "db_table_structure.json",
            queries_json=BASE_DIR / "src/example_queries/queries.json",
            example_queries_dir=BASE_DIR / "src/example_queries",
        )
        try:
            thread = "persistence_integration_test"
            # First question
            a1 = atlas_sql.answer_question(
                "What were the top 3 exports of Kenya in 2019?",
                stream_response=False,
                thread_id=thread,
            )
            assert len(a1) > 0

            # Follow-up referencing prior context
            a2 = atlas_sql.answer_question(
                "How did those change in 2020?",
                stream_response=False,
                thread_id=thread,
            )
            assert len(a2) > 0
            # The follow-up should reference Kenya or the products
            assert any(
                word in a2.lower() for word in ["kenya", "export", "tea", "coffee"]
            )
        finally:
            atlas_sql.close()
