"""Integration tests for the docs pipeline with a real LLM.

Verifies that the docs pipeline correctly selects documentation,
synthesizes responses, and handles edge cases using a real LLM
(no mocks). Uses the actual src/docs/ directory.

Requires: LLM API keys configured in .env.
Does NOT require a database.

Run::

    PYTHONPATH=$(pwd) uv run pytest src/tests/test_docs_integration.py -m integration -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import create_llm, get_settings
from src.docs_pipeline import (
    DocEntry,
    _DOCS_STATE_DEFAULTS,
    load_docs_manifest,
    select_and_synthesize,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TECHNICAL_DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"


@pytest.fixture(scope="module")
def lightweight_llm():
    """Create a real lightweight LLM for integration tests."""
    settings = get_settings()
    if (
        not settings.openai_api_key
        and not settings.anthropic_api_key
        and not settings.google_api_key
    ):
        pytest.skip("No LLM API keys configured — skipping integration tests")
    return create_llm(
        settings.lightweight_model,
        settings.lightweight_model_provider,
        temperature=0,
    )


@pytest.fixture(scope="module")
def docs_manifest() -> list[DocEntry]:
    """Load the real technical documentation manifest."""
    if not TECHNICAL_DOCS_DIR.is_dir():
        pytest.skip("src/docs/ directory not found — skipping docs integration tests")
    manifest = load_docs_manifest(TECHNICAL_DOCS_DIR)
    if not manifest:
        pytest.skip("No documentation files found in src/docs/")
    return manifest


def _base_docs_state(**overrides) -> dict:
    """Build a minimal state dict for docs pipeline tests."""
    state: dict = {
        "messages": [],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        **_DOCS_STATE_DEFAULTS,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests: ECI methodology question
# ---------------------------------------------------------------------------


class TestDocsEciMethodology:
    async def test_eci_definition_produces_relevant_synthesis(
        self, lightweight_llm, docs_manifest
    ):
        """The docs pipeline should produce a relevant response about ECI methodology."""
        state = _base_docs_state(
            docs_question="What is the Economic Complexity Index (ECI)? How is it calculated?",
            docs_context="User wants to understand complexity metrics.",
        )

        result = await select_and_synthesize(
            state, lightweight_model=lightweight_llm, manifest=docs_manifest
        )

        # Should have selected at least one doc
        assert result["docs_selected_files"], "No documentation files were selected"

        # Synthesis should mention ECI-related concepts
        synthesis = result["docs_synthesis"].lower()
        assert (
            "eci" in synthesis or "economic complexity" in synthesis
        ), f"Synthesis does not mention ECI: {result['docs_synthesis'][:200]}"

    async def test_rca_question_selects_relevant_docs(
        self, lightweight_llm, docs_manifest
    ):
        """A question about RCA should select metrics documentation."""
        state = _base_docs_state(
            docs_question="What is Revealed Comparative Advantage (RCA)? How is it calculated?",
        )

        result = await select_and_synthesize(
            state, lightweight_model=lightweight_llm, manifest=docs_manifest
        )

        assert result["docs_selected_files"], "No documentation files were selected"

        synthesis = result["docs_synthesis"].lower()
        assert (
            "rca" in synthesis or "comparative advantage" in synthesis
        ), f"Synthesis does not mention RCA: {result['docs_synthesis'][:200]}"


# ---------------------------------------------------------------------------
# Tests: Classification system questions
# ---------------------------------------------------------------------------


class TestDocsClassificationSystems:
    async def test_hs92_vs_hs12_question(self, lightweight_llm, docs_manifest):
        """A question about classification systems should be answered from docs."""
        state = _base_docs_state(
            docs_question="What is the difference between HS92 and HS12 classification systems? What years does each cover?",
        )

        result = await select_and_synthesize(
            state, lightweight_model=lightweight_llm, manifest=docs_manifest
        )

        assert result["docs_selected_files"], "No documentation files were selected"

        synthesis = result["docs_synthesis"].lower()
        # Should mention at least one of the classification systems
        assert (
            "hs92" in synthesis
            or "hs12" in synthesis
            or "harmonized system" in synthesis
        ), f"Synthesis does not mention classification systems: {result['docs_synthesis'][:200]}"


# ---------------------------------------------------------------------------
# Tests: Out-of-scope handling
# ---------------------------------------------------------------------------


class TestDocsOutOfScope:
    async def test_unrelated_question_still_produces_output(
        self, lightweight_llm, docs_manifest
    ):
        """An unrelated question should still produce a response (even if noting irrelevance)."""
        state = _base_docs_state(
            docs_question="What is the weather in Boston today?",
        )

        result = await select_and_synthesize(
            state, lightweight_model=lightweight_llm, manifest=docs_manifest
        )

        # The pipeline should not crash — it should produce some output
        assert result[
            "docs_synthesis"
        ], "Pipeline returned empty synthesis for out-of-scope question"
        # It may note that it can't answer, or may try to answer from whatever it has
        # Either way, it should not crash
