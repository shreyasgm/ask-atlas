"""Unit tests for the docs pipeline.

Tests cover manifest loading, all three pipeline nodes, and error fallbacks.
All tests are unit tests — no database or external LLM required.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.docs_pipeline import (
    DocEntry,
    DocsSelection,
    _DOCS_STATE_DEFAULTS,
    _format_manifest_for_prompt,
    _parse_doc_header,
    extract_docs_question,
    format_docs_results,
    load_docs_manifest,
    select_and_synthesize,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_DOC = """\
# Test Metric Guide

**Purpose:** Explains test metrics and their formulas.

**When to load this document:** Load when a user asks about test metrics
or needs formula details for testing.

---

## Section 1

Some content here about test metrics.
"""


@pytest.fixture
def sample_docs_dir(tmp_path: Path) -> Path:
    """Create a temporary directory with sample documentation files."""
    doc1 = tmp_path / "metrics.md"
    doc1.write_text(SAMPLE_DOC)

    doc2 = tmp_path / "trade_data.md"
    doc2.write_text(
        "# Trade Data Guide\n\n"
        "**Purpose:** Reference for trade data tables.\n\n"
        "**When to load this document:** Load when asking about trade tables.\n\n"
        "---\n\n"
        "Trade data content here.\n"
    )
    return tmp_path


@pytest.fixture
def sample_manifest(sample_docs_dir: Path) -> list[DocEntry]:
    """Build a manifest from the sample docs directory."""
    return load_docs_manifest(sample_docs_dir)


def _docs_tool_call(question: str, context: str = "", call_id: str = "docs-call-1"):
    """Build an AIMessage with a docs_tool tool_call."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "docs_tool",
                "args": {"question": question, "context": context},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


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
# Tests: Header parsing
# ---------------------------------------------------------------------------


class TestParseDocHeader:
    def test_parses_all_fields(self):
        title, purpose, when = _parse_doc_header(SAMPLE_DOC)
        assert title == "Test Metric Guide"
        assert "test metrics" in purpose.lower()
        assert "formula" in when.lower()

    def test_empty_document(self):
        title, purpose, when = _parse_doc_header("")
        assert title == ""
        assert purpose == ""
        assert when == ""

    def test_no_purpose_or_when(self):
        title, purpose, when = _parse_doc_header("# Just a Title\n\nSome content.")
        assert title == "Just a Title"
        assert purpose == ""
        assert when == ""


# ---------------------------------------------------------------------------
# Tests: Manifest loading
# ---------------------------------------------------------------------------


class TestLoadDocsManifest:
    def test_loads_real_technical_docs(self):
        """Ensure we can parse the actual src/docs/ directory."""
        docs_dir = Path(__file__).resolve().parents[1] / "docs"
        if not docs_dir.is_dir():
            pytest.skip("src/docs/ directory not found")

        manifest = load_docs_manifest(docs_dir)
        assert len(manifest) >= 10  # We know there are 14 files

        # Every entry should have a title and filename
        for entry in manifest:
            assert entry.filename.endswith(".md")
            assert entry.title
            assert entry.full_path.exists()

    def test_loads_sample_directory(self, sample_docs_dir: Path):
        manifest = load_docs_manifest(sample_docs_dir)
        assert len(manifest) == 2

        # Should be sorted by filename
        assert manifest[0].filename == "metrics.md"
        assert manifest[1].filename == "trade_data.md"

        assert manifest[0].title == "Test Metric Guide"
        assert "test metrics" in manifest[0].purpose.lower()

    def test_nonexistent_directory(self, tmp_path: Path):
        manifest = load_docs_manifest(tmp_path / "nonexistent")
        assert manifest == []

    def test_empty_directory(self, tmp_path: Path):
        manifest = load_docs_manifest(tmp_path)
        assert manifest == []


# ---------------------------------------------------------------------------
# Tests: Manifest formatting
# ---------------------------------------------------------------------------


class TestFormatManifest:
    def test_format_includes_all_entries(self, sample_manifest: list[DocEntry]):
        text = _format_manifest_for_prompt(sample_manifest)
        assert "[0]" in text
        assert "[1]" in text
        assert "Test Metric Guide" in text
        assert "Trade Data Guide" in text


# ---------------------------------------------------------------------------
# Tests: extract_docs_question
# ---------------------------------------------------------------------------


class TestExtractDocsQuestion:
    async def test_extracts_question_and_context(self):
        msg = _docs_tool_call(
            question="What is ECI?",
            context="User wants to understand complexity rankings.",
        )
        state = _base_docs_state(messages=[msg])

        result = await extract_docs_question(state)

        assert result["docs_question"] == "What is ECI?"
        assert result["docs_context"] == "User wants to understand complexity rankings."

    async def test_extracts_question_without_context(self):
        msg = _docs_tool_call(question="How does RCA work?")
        state = _base_docs_state(messages=[msg])

        result = await extract_docs_question(state)

        assert result["docs_question"] == "How does RCA work?"
        assert result["docs_context"] == ""

    async def test_resets_state_fields(self):
        msg = _docs_tool_call(question="test")
        state = _base_docs_state(
            messages=[msg],
            docs_synthesis="old synthesis",
            docs_selected_files=["old.md"],
        )

        result = await extract_docs_question(state)

        # Should reset all docs_* fields (except question/context which are set)
        assert result["docs_selected_files"] == []
        assert result["docs_synthesis"] == ""

    async def test_handles_parallel_tool_calls(self):
        """Only the first tool_call is processed."""
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "docs_tool",
                    "args": {"question": "first question"},
                    "id": "call-1",
                    "type": "tool_call",
                },
                {
                    "name": "docs_tool",
                    "args": {"question": "second question"},
                    "id": "call-2",
                    "type": "tool_call",
                },
            ],
        )
        state = _base_docs_state(messages=[msg])

        result = await extract_docs_question(state)

        assert result["docs_question"] == "first question"


# ---------------------------------------------------------------------------
# Tests: select_and_synthesize
# ---------------------------------------------------------------------------


class TestSelectAndSynthesize:
    async def test_happy_path(self, sample_manifest: list[DocEntry]):
        """LLM selects correct docs, synthesis produces a response."""
        selection = DocsSelection(
            reasoning="Metrics doc is relevant",
            selected_indices=[0],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )

        synthesis_response = MagicMock()
        synthesis_response.content = "ECI measures economic complexity."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(
            docs_question="What is ECI?",
            docs_context="",
        )

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_selected_files"] == ["metrics.md"]
        assert result["docs_synthesis"] == "ECI measures economic complexity."
        mock_llm.with_structured_output.assert_called_once()

        # Verify selection LLM was called with a prompt containing the question
        # and the manifest entries
        selection_call_args = (
            mock_llm.with_structured_output.return_value.ainvoke.call_args[0][0]
        )
        assert "What is ECI?" in selection_call_args
        assert "Test Metric Guide" in selection_call_args

        # Verify synthesis LLM was called with loaded doc content (not just any string)
        synthesis_call_args = mock_llm.ainvoke.call_args[0][0]
        assert "Some content here about test metrics" in synthesis_call_args

    async def test_selects_multiple_docs(self, sample_manifest: list[DocEntry]):
        """LLM can select multiple documents."""
        selection = DocsSelection(
            reasoning="Both docs relevant",
            selected_indices=[0, 1],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Combined answer."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(docs_question="Overview?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_selected_files"] == ["metrics.md", "trade_data.md"]
        assert result["docs_synthesis"] == "Combined answer."

    async def test_selection_fallback_on_llm_error(
        self, sample_manifest: list[DocEntry]
    ):
        """Selection LLM fails → loads all docs as fallback."""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=Exception("LLM timeout")
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Fallback synthesis."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(docs_question="Anything?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        # All docs loaded as fallback
        assert len(result["docs_selected_files"]) == len(sample_manifest)
        assert result["docs_synthesis"] == "Fallback synthesis."

    async def test_synthesis_fallback_on_llm_error(
        self, sample_manifest: list[DocEntry]
    ):
        """Synthesis LLM fails → returns raw concatenated docs."""
        selection = DocsSelection(
            reasoning="Select first",
            selected_indices=[0],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("Synthesis failed"))

        state = _base_docs_state(docs_question="Metrics?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_selected_files"] == ["metrics.md"]
        # Synthesis should contain the raw doc content
        assert "Test Metric Guide" in result["docs_synthesis"]
        assert "Some content here" in result["docs_synthesis"]

    async def test_handles_empty_selection(self, sample_manifest: list[DocEntry]):
        """LLM selects no docs → loads all as fallback."""
        selection = DocsSelection(
            reasoning="None seem relevant",
            selected_indices=[],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Answer from all docs."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(docs_question="Something?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        # Should fall back to all docs
        assert len(result["docs_selected_files"]) == len(sample_manifest)

    async def test_handles_invalid_indices(self, sample_manifest: list[DocEntry]):
        """LLM returns out-of-range indices → loads all as fallback."""
        selection = DocsSelection(
            reasoning="Invalid indices",
            selected_indices=[99, -5],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Fallback answer."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(docs_question="Something?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        # All invalid → loads all
        assert len(result["docs_selected_files"]) == len(sample_manifest)

    async def test_passes_context_to_prompts(self, sample_manifest: list[DocEntry]):
        """Context string is included in LLM prompts when provided."""
        selection = DocsSelection(reasoning="Relevant", selected_indices=[0])
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Answer with context."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(
            docs_question="What is PCI?",
            docs_context="User is building a dashboard showing PCI trends.",
        )

        await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        # Verify context was passed to synthesis prompt
        synthesis_call = mock_llm.ainvoke.call_args[0][0]
        assert "dashboard" in synthesis_call

    async def test_empty_manifest(self):
        """With no docs available, returns appropriate message."""
        mock_llm = MagicMock()
        # Selection LLM will fail because no manifest → catch
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=Exception("No docs")
        )

        state = _base_docs_state(docs_question="Anything?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=[]
        )

        assert result["docs_synthesis"] == "No documentation files could be loaded."

    async def test_handles_deleted_doc_file(self, sample_docs_dir: Path):
        """A doc file deleted after manifest load is handled gracefully."""
        manifest = load_docs_manifest(sample_docs_dir)
        assert len(manifest) == 2  # metrics.md + trade_data.md

        # Delete one of the files after the manifest was built
        (sample_docs_dir / "metrics.md").unlink()

        # LLM selects the deleted file (index 0)
        selection = DocsSelection(
            reasoning="Metrics doc is relevant",
            selected_indices=[0],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )

        state = _base_docs_state(docs_question="What are the metrics?")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=manifest
        )

        # The deleted file is still listed as selected (it was in the manifest)
        assert "metrics.md" in result["docs_selected_files"]
        # With no loadable content, falls back to "no docs loaded" message
        assert result["docs_synthesis"] == "No documentation files could be loaded."

    async def test_handles_deleted_doc_file_with_remaining_docs(
        self, sample_docs_dir: Path
    ):
        """When one doc is deleted but another is still readable, synthesis proceeds."""
        manifest = load_docs_manifest(sample_docs_dir)
        assert len(manifest) == 2

        # Delete metrics.md but keep trade_data.md
        (sample_docs_dir / "metrics.md").unlink()

        # LLM selects both files
        selection = DocsSelection(
            reasoning="Both docs relevant",
            selected_indices=[0, 1],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )
        synthesis_response = MagicMock()
        synthesis_response.content = "Answer from remaining doc."
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(docs_question="Tell me about trade data")

        result = await select_and_synthesize(
            state, lightweight_model=mock_llm, manifest=manifest
        )

        # Both listed as selected, but synthesis proceeds with the remaining doc
        assert len(result["docs_selected_files"]) == 2
        assert result["docs_synthesis"] == "Answer from remaining doc."
        # Synthesis LLM was called with the trade_data content
        synthesis_call_args = mock_llm.ainvoke.call_args[0][0]
        assert "Trade data content here" in synthesis_call_args


# ---------------------------------------------------------------------------
# Tests: format_docs_results
# ---------------------------------------------------------------------------


class TestFormatDocsResults:
    async def test_creates_tool_message(self):
        msg = _docs_tool_call(question="What is ECI?", call_id="docs-fmt-1")
        state = _base_docs_state(
            messages=[msg],
            docs_synthesis="ECI measures economic complexity.",
        )

        result = await format_docs_results(state)

        assert "messages" in result
        assert len(result["messages"]) == 1
        tool_msg = result["messages"][0]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.content == "ECI measures economic complexity."
        assert tool_msg.tool_call_id == "docs-fmt-1"

    async def test_does_not_increment_queries_executed(self):
        msg = _docs_tool_call(question="test", call_id="docs-fmt-2")
        state = _base_docs_state(
            messages=[msg],
            docs_synthesis="Some docs.",
            queries_executed=1,
        )

        result = await format_docs_results(state)

        # Should NOT contain queries_executed
        assert "queries_executed" not in result

    async def test_handles_empty_synthesis(self):
        msg = _docs_tool_call(question="test", call_id="docs-fmt-3")
        state = _base_docs_state(messages=[msg], docs_synthesis="")

        result = await format_docs_results(state)

        assert result["messages"][0].content == "No relevant documentation found."

    async def test_handles_parallel_tool_calls(self):
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "docs_tool",
                    "args": {"question": "q1"},
                    "id": "call-1",
                    "type": "tool_call",
                },
                {
                    "name": "docs_tool",
                    "args": {"question": "q2"},
                    "id": "call-2",
                    "type": "tool_call",
                },
            ],
        )
        state = _base_docs_state(messages=[msg], docs_synthesis="Answer here.")

        result = await format_docs_results(state)

        assert len(result["messages"]) == 2
        assert result["messages"][0].content == "Answer here."
        assert "one tool" in result["messages"][1].content.lower()
