"""Unit tests for the docs pipeline.

Tests cover manifest loading, pipeline nodes (extract, retrieve, format),
auto-injection, and error fallbacks.
All tests are unit tests — no database or external LLM required.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.docs_pipeline import (
    _DOCS_STATE_DEFAULTS,
    DocEntry,
    _extract_body,
    _parse_yaml_frontmatter,
    extract_docs_question,
    format_docs_results,
    load_docs_manifest,
    retrieve_docs,
    retrieve_docs_context,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_DOC = """\
---
title: Test Metric Guide
purpose: >
  Explains test metrics and their formulas.
keywords: [ECI, PCI, test-metrics]
when_to_load: >
  Load when a user asks about test metrics
  or needs formula details for testing.
when_not_to_load: >
  Do not load for trade data questions.
related_docs: [trade_data.md]
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
        "---\n"
        "title: Trade Data Guide\n"
        "purpose: Reference for trade data tables.\n"
        "keywords: [trade, imports, exports]\n"
        "when_to_load: Load when asking about trade tables.\n"
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
        "docs_auto_chunks": [],
        **_DOCS_STATE_DEFAULTS,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests: YAML frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseYamlFrontmatter:
    def test_parses_all_fields(self):
        fm = _parse_yaml_frontmatter(SAMPLE_DOC)
        assert fm["title"] == "Test Metric Guide"
        assert "test metrics" in fm["purpose"].lower()
        assert "formula" in fm["when_to_load"].lower()
        assert fm["keywords"] == ["ECI", "PCI", "test-metrics"]
        assert "trade data" in fm["when_not_to_load"].lower()
        assert fm["related_docs"] == ["trade_data.md"]

    def test_empty_document(self):
        fm = _parse_yaml_frontmatter("")
        assert fm == {}

    def test_no_frontmatter(self):
        fm = _parse_yaml_frontmatter("# Just a Title\n\nSome content.")
        assert fm == {}

    def test_unclosed_frontmatter(self):
        fm = _parse_yaml_frontmatter("---\ntitle: Broken\n")
        assert fm == {}

    def test_invalid_yaml(self):
        fm = _parse_yaml_frontmatter("---\n: [invalid\n---\n")
        assert fm == {}

    def test_non_dict_yaml(self):
        fm = _parse_yaml_frontmatter("---\n- item1\n- item2\n---\n")
        assert fm == {}


class TestExtractBody:
    def test_extracts_body_after_frontmatter(self):
        body = _extract_body(SAMPLE_DOC)
        assert body.startswith("## Section 1")
        assert "Some content here about test metrics." in body

    def test_no_frontmatter_returns_full_text(self):
        text = "# Title\n\nBody content."
        assert _extract_body(text) == text

    def test_unclosed_frontmatter_returns_full_text(self):
        text = "---\ntitle: Broken\nContent after."
        assert _extract_body(text) == text


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

        # Every entry should have a title, filename, and pre-loaded content
        for entry in manifest:
            assert entry.filename.endswith(".md")
            assert entry.title
            assert entry.full_path.exists()
            assert entry.content  # Content should be pre-loaded

    def test_loads_sample_directory(self, sample_docs_dir: Path):
        manifest = load_docs_manifest(sample_docs_dir)
        assert len(manifest) == 2

        # Should be sorted by filename
        assert manifest[0].filename == "metrics.md"
        assert manifest[1].filename == "trade_data.md"

        assert manifest[0].title == "Test Metric Guide"
        assert "test metrics" in manifest[0].purpose.lower()

    def test_populates_new_fields(self, sample_docs_dir: Path):
        """New fields (content, keywords, when_not_to_load, related_docs) are populated."""
        manifest = load_docs_manifest(sample_docs_dir)
        metrics_entry = manifest[0]

        assert metrics_entry.keywords == ("ECI", "PCI", "test-metrics")
        assert "trade data" in metrics_entry.when_not_to_load.lower()
        assert metrics_entry.related_docs == ("trade_data.md",)
        assert "Some content here about test metrics" in metrics_entry.content

    def test_preloads_content(self, sample_docs_dir: Path):
        """Content is pre-loaded (body text without frontmatter)."""
        manifest = load_docs_manifest(sample_docs_dir)
        # Content should NOT contain YAML frontmatter
        for entry in manifest:
            assert not entry.content.startswith("---")
            assert entry.content  # Non-empty

    def test_nonexistent_directory(self, tmp_path: Path):
        manifest = load_docs_manifest(tmp_path / "nonexistent")
        assert manifest == []

    def test_empty_directory(self, tmp_path: Path):
        manifest = load_docs_manifest(tmp_path)
        assert manifest == []

    def test_fallback_title_from_filename(self, tmp_path: Path):
        """Files without a title in frontmatter get a title from the filename."""
        doc = tmp_path / "my_guide.md"
        doc.write_text("---\npurpose: A guide.\n---\n\nBody text.\n")
        manifest = load_docs_manifest(tmp_path)
        assert manifest[0].title == "My Guide"


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
# Tests: retrieve_docs
# ---------------------------------------------------------------------------


class TestRetrieveDocs:
    async def test_no_index_returns_fallback(self):
        """When no docs_index is provided, returns a fallback message."""
        state = _base_docs_state(docs_question="What is ECI?")
        result = await retrieve_docs(state, docs_index=None)
        assert "not available" in result["docs_synthesis"].lower()

    async def test_calls_index_search(self):
        """Calls docs_index.search() with the question."""
        mock_index = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.chunk_id = "c1"
        mock_chunk.doc_filename = "metrics.md"
        mock_chunk.doc_title = "Metrics"
        mock_chunk.section_title = "ECI"
        mock_chunk.body = "ECI measures complexity."
        mock_index.search = AsyncMock(return_value=[mock_chunk])

        state = _base_docs_state(docs_question="What is ECI?")
        result = await retrieve_docs(state, docs_index=mock_index)

        mock_index.search.assert_called_once()
        assert "ECI" in result["docs_synthesis"]

    async def test_excludes_auto_injected_chunks(self):
        """Already auto-injected chunks are excluded from retrieval."""
        mock_index = MagicMock()
        mock_index.search = AsyncMock(return_value=[])

        state = _base_docs_state(
            docs_question="What is ECI?",
            docs_auto_chunks=[{"chunk_id": "already-injected"}],
        )
        await retrieve_docs(state, docs_index=mock_index)

        call_kwargs = mock_index.search.call_args
        exclude_ids = call_kwargs.kwargs.get("exclude_chunk_ids", set())
        assert "already-injected" in exclude_ids

    async def test_empty_results_returns_no_docs(self):
        """When search returns no results, returns appropriate message."""
        mock_index = MagicMock()
        mock_index.search = AsyncMock(return_value=[])

        state = _base_docs_state(docs_question="obscure topic")
        result = await retrieve_docs(state, docs_index=mock_index)

        assert "no relevant" in result["docs_synthesis"].lower()

    async def test_search_error_returns_error_message(self):
        """When search raises an exception, returns error message."""
        mock_index = MagicMock()
        mock_index.search = AsyncMock(side_effect=Exception("DB error"))

        state = _base_docs_state(docs_question="What is ECI?")
        result = await retrieve_docs(state, docs_index=mock_index)

        assert "error" in result["docs_synthesis"].lower()

    async def test_context_appended_to_query(self):
        """Context is appended to the question for search."""
        mock_index = MagicMock()
        mock_index.search = AsyncMock(return_value=[])

        state = _base_docs_state(
            docs_question="What is ECI?",
            docs_context="User wants to build a dashboard.",
        )
        await retrieve_docs(state, docs_index=mock_index)

        call_args = mock_index.search.call_args[0]
        assert "dashboard" in call_args[0]


# ---------------------------------------------------------------------------
# Tests: retrieve_docs_context
# ---------------------------------------------------------------------------


class TestRetrieveDocsContext:
    async def test_no_index_returns_empty(self):
        """When no docs_index is provided, returns empty chunks."""
        state = _base_docs_state(messages=[HumanMessage(content="What is ECI?")])
        result = await retrieve_docs_context(state, docs_index=None)
        assert result["docs_auto_chunks"] == []

    async def test_extracts_user_message_for_search(self):
        """Uses the latest human message as search query."""
        mock_index = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.chunk_id = "c1"
        mock_chunk.doc_filename = "metrics.md"
        mock_chunk.doc_title = "Metrics"
        mock_chunk.section_title = "ECI"
        mock_chunk.body = "ECI content."
        mock_index.search = AsyncMock(return_value=[mock_chunk])

        state = _base_docs_state(messages=[HumanMessage(content="Tell me about ECI")])
        result = await retrieve_docs_context(state, docs_index=mock_index)

        mock_index.search.assert_called_once()
        assert len(result["docs_auto_chunks"]) == 1
        assert result["docs_auto_chunks"][0]["chunk_id"] == "c1"

    async def test_no_messages_returns_empty(self):
        """When there are no messages, returns empty chunks."""
        mock_index = MagicMock()
        state = _base_docs_state(messages=[])
        result = await retrieve_docs_context(state, docs_index=mock_index)
        assert result["docs_auto_chunks"] == []

    async def test_search_error_returns_empty(self):
        """When search fails, returns empty chunks gracefully."""
        mock_index = MagicMock()
        mock_index.search = AsyncMock(side_effect=Exception("search failed"))

        state = _base_docs_state(messages=[HumanMessage(content="What is ECI?")])
        result = await retrieve_docs_context(state, docs_index=mock_index)
        assert result["docs_auto_chunks"] == []


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
