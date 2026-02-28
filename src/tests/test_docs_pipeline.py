"""Unit tests for the docs pipeline.

Tests cover manifest loading, all three pipeline nodes, and error fallbacks.
All tests are unit tests — no database or external LLM required.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.docs_pipeline import (
    DEFAULT_MAX_DOCS,
    DocEntry,
    DocsSelection,
    _DOCS_STATE_DEFAULTS,
    _extract_body,
    _format_manifest_for_prompt,
    _make_docs_selection_model,
    _parse_yaml_frontmatter,
    extract_docs_question,
    format_docs_results,
    load_docs_manifest,
    select_docs,
    synthesize_docs,
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
# Tests: Manifest formatting
# ---------------------------------------------------------------------------


class TestFormatManifest:
    def test_format_includes_all_entries(self, sample_manifest: list[DocEntry]):
        text = _format_manifest_for_prompt(sample_manifest)
        assert "[0]" in text
        assert "[1]" in text
        assert "Test Metric Guide" in text
        assert "Trade Data Guide" in text

    def test_format_includes_keywords(self, sample_manifest: list[DocEntry]):
        text = _format_manifest_for_prompt(sample_manifest)
        assert "Keywords:" in text
        assert "ECI" in text
        assert "trade" in text

    def test_format_includes_negative_signals(self, sample_manifest: list[DocEntry]):
        text = _format_manifest_for_prompt(sample_manifest)
        assert "When NOT to load:" in text
        assert "trade data" in text.lower()

    def test_format_omits_empty_optional_fields(self):
        """Entries without keywords or when_not_to_load don't show those lines."""
        entry = DocEntry(
            filename="bare.md",
            title="Bare Doc",
            purpose="A bare doc.",
            when_to_load="Load always.",
            full_path=Path("/tmp/bare.md"),
        )
        text = _format_manifest_for_prompt([entry])
        assert "Keywords:" not in text
        assert "When NOT to load:" not in text


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
# Tests: select_docs
# ---------------------------------------------------------------------------


class TestSelectDocs:
    async def test_happy_path(self, sample_manifest: list[DocEntry]):
        """LLM selects correct docs; no synthesis is performed."""
        selection = DocsSelection(
            reasoning="Metrics doc is relevant",
            selected_indices=[0],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )

        state = _base_docs_state(docs_question="What is ECI?", docs_context="")

        result = await select_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_selected_files"] == ["metrics.md"]
        # select_docs must NOT produce docs_synthesis
        assert "docs_synthesis" not in result
        mock_llm.with_structured_output.assert_called_once()

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

        state = _base_docs_state(docs_question="Overview?")

        result = await select_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_selected_files"] == ["metrics.md", "trade_data.md"]

    async def test_fallback_on_llm_error(self, sample_manifest: list[DocEntry]):
        """Selection LLM fails → falls back to all docs."""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            side_effect=Exception("LLM timeout")
        )

        state = _base_docs_state(docs_question="Anything?")

        result = await select_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert len(result["docs_selected_files"]) == len(sample_manifest)

    async def test_handles_invalid_indices(self, sample_manifest: list[DocEntry]):
        """LLM returns out-of-range indices → falls back to all docs."""
        selection = DocsSelection(
            reasoning="Invalid indices",
            selected_indices=[99, -5],
        )
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )

        state = _base_docs_state(docs_question="Something?")

        result = await select_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert len(result["docs_selected_files"]) == len(sample_manifest)

    async def test_max_docs_passed_to_prompt(self, sample_manifest: list[DocEntry]):
        """max_docs value is included in the selection prompt."""
        selection = DocsSelection(reasoning="Relevant", selected_indices=[0])
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=selection
        )

        state = _base_docs_state(docs_question="What is ECI?")

        await select_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest, max_docs=5
        )

        selection_call_args = (
            mock_llm.with_structured_output.return_value.ainvoke.call_args[0][0]
        )
        assert "1 to 5" in selection_call_args
        assert "more than 5" in selection_call_args


# ---------------------------------------------------------------------------
# Tests: synthesize_docs
# ---------------------------------------------------------------------------


class TestSynthesizeDocs:
    async def test_happy_path(self, sample_manifest: list[DocEntry]):
        """Given selected files in state, produces synthesis."""
        synthesis_response = MagicMock()
        synthesis_response.content = "ECI measures economic complexity."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(
            docs_question="What is ECI?",
            docs_selected_files=["metrics.md"],
        )

        result = await synthesize_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_synthesis"] == "ECI measures economic complexity."
        # Verify synthesis LLM was called with pre-loaded doc content
        synthesis_call_args = mock_llm.ainvoke.call_args[0][0]
        assert "Some content here about test metrics" in synthesis_call_args

    async def test_fallback_on_llm_error(self, sample_manifest: list[DocEntry]):
        """Synthesis LLM fails → returns raw concatenated docs."""
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("Synthesis failed"))

        state = _base_docs_state(
            docs_question="Metrics?",
            docs_selected_files=["metrics.md"],
        )

        result = await synthesize_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert "Test Metric Guide" in result["docs_synthesis"]
        assert "Some content here" in result["docs_synthesis"]

    async def test_no_matching_files(self, sample_manifest: list[DocEntry]):
        """If selected files don't match manifest entries, returns error message."""
        mock_llm = MagicMock()

        state = _base_docs_state(
            docs_question="Test?",
            docs_selected_files=["nonexistent.md"],
        )

        result = await synthesize_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        assert result["docs_synthesis"] == "No documentation files could be loaded."

    async def test_passes_context_to_prompt(self, sample_manifest: list[DocEntry]):
        """Context string is included in the synthesis LLM prompt."""
        synthesis_response = MagicMock()
        synthesis_response.content = "Answer with context."
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=synthesis_response)

        state = _base_docs_state(
            docs_question="What is PCI?",
            docs_context="User is building a dashboard showing PCI trends.",
            docs_selected_files=["metrics.md"],
        )

        await synthesize_docs(
            state, lightweight_model=mock_llm, manifest=sample_manifest
        )

        synthesis_call = mock_llm.ainvoke.call_args[0][0]
        assert "dashboard" in synthesis_call


# ---------------------------------------------------------------------------
# Tests: _make_docs_selection_model
# ---------------------------------------------------------------------------


class TestMakeDocsSelectionModel:
    def test_default_max_docs_is_2(self):
        assert DEFAULT_MAX_DOCS == 2

    def test_creates_model_with_max_length(self):
        model_cls = _make_docs_selection_model(3)
        # Should accept up to 3 indices
        instance = model_cls(
            reasoning="test",
            selected_indices=[0, 1, 2],
        )
        assert len(instance.selected_indices) == 3

    def test_rejects_too_many_indices(self):
        model_cls = _make_docs_selection_model(2)
        with pytest.raises(Exception):  # Pydantic validation error
            model_cls(
                reasoning="test",
                selected_indices=[0, 1, 2],
            )

    def test_description_includes_max_docs(self):
        model_cls = _make_docs_selection_model(4)
        field_info = model_cls.model_fields["selected_indices"]
        assert "4" in field_info.description


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
