"""Integration tests for the docs pipeline with a real docs index.

Verifies that the docs retrieval pipeline correctly retrieves relevant
documentation chunks and handles edge cases using the hybrid search index.

Requires: A built docs index at src/docs_index.db (run scripts/build_docs_index.py first).
Does NOT require a database or LLM API keys — retrieval is purely local.

Run::

    PYTHONPATH=$(pwd) uv run pytest src/tests/test_docs_integration.py -m integration -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.docs_pipeline import (
    _DOCS_STATE_DEFAULTS,
    retrieve_docs,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DOCS_INDEX_PATH = Path(__file__).resolve().parents[1] / "docs_index.db"


@pytest.fixture(scope="module")
def docs_index():
    """Load the real docs index for integration tests."""
    if not DOCS_INDEX_PATH.exists():
        pytest.skip("docs_index.db not found — run scripts/build_docs_index.py first")

    from src.docs_retrieval import DocsIndex

    index = DocsIndex(DOCS_INDEX_PATH)
    yield index
    index.close()


def _base_docs_state(**overrides) -> dict:
    """Build a minimal state dict for docs pipeline tests."""
    state: dict = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "docs_auto_chunks": [],
        **_DOCS_STATE_DEFAULTS,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests: ECI methodology question
# ---------------------------------------------------------------------------


class TestDocsEciMethodology:
    async def test_eci_definition_retrieves_relevant_chunks(self, docs_index):
        """The docs pipeline should retrieve relevant chunks about ECI methodology."""
        state = _base_docs_state(
            docs_question="What is the Economic Complexity Index (ECI)? How is it calculated?",
            docs_context="User wants to understand complexity metrics.",
        )

        result = await retrieve_docs(state, docs_index=docs_index, top_k=6)

        synthesis = result["docs_synthesis"]
        assert synthesis, "No synthesis produced"
        assert synthesis != "Documentation index not available."

        # Should contain ECI-related content
        synthesis_lower = synthesis.lower()
        assert "eci" in synthesis_lower or "economic complexity" in synthesis_lower, (
            f"Synthesis does not mention ECI: {synthesis[:200]}"
        )


# ---------------------------------------------------------------------------
# Tests: Classification system questions
# ---------------------------------------------------------------------------


class TestDocsClassificationSystems:
    async def test_hs_classification_retrieves_relevant_chunks(self, docs_index):
        """A question about classification systems should retrieve relevant docs."""
        state = _base_docs_state(
            docs_question="What is the difference between HS92 and HS12 classification systems?",
        )

        result = await retrieve_docs(state, docs_index=docs_index, top_k=6)

        synthesis = result["docs_synthesis"]
        assert synthesis, "No synthesis produced"
        synthesis_lower = synthesis.lower()
        assert (
            "hs92" in synthesis_lower
            or "hs12" in synthesis_lower
            or "harmonized system" in synthesis_lower
        ), f"Synthesis does not mention classification systems: {synthesis[:200]}"


# ---------------------------------------------------------------------------
# Tests: No index fallback
# ---------------------------------------------------------------------------


class TestDocsNoIndex:
    async def test_no_index_returns_fallback_message(self):
        """Without a docs index, retrieve_docs should return a fallback message."""
        state = _base_docs_state(
            docs_question="What is ECI?",
        )

        result = await retrieve_docs(state, docs_index=None, top_k=6)

        assert result["docs_synthesis"] == "Documentation index not available."
