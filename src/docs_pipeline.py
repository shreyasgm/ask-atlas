"""Documentation pipeline node functions for the Atlas agent graph.

Provides 3 async node functions that form a linear pipeline:

    extract_docs_question → select_and_synthesize → format_docs_results

When the agent calls ``docs_tool(question="...")``, this pipeline selects
relevant documentation from ``src/docs/``, synthesizes a focused
response, and returns it as a ToolMessage.  The agent can then pass this
context to subsequent data queries via the structured ``context`` field.

Design authority: docs/backend_redesign_analysis.md section 15.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.prompts import DOCUMENT_SELECTION_PROMPT, DOCUMENTATION_SYNTHESIS_PROMPT
from src.state import AtlasAgentState
from src.token_usage import (
    make_usage_record_from_callback,
    make_usage_record_from_msg,
    node_timer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DOCS_PIPELINE_NODES = frozenset(
    {
        "extract_docs_question",
        "select_docs",
        "synthesize_docs",
        "format_docs_results",
    }
)

# Default state values for docs_* fields (used to reset between turns)
_DOCS_STATE_DEFAULTS: dict[str, Any] = {
    "docs_question": "",
    "docs_context": "",
    "docs_selected_files": [],
    "docs_synthesis": "",
}

# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocEntry:
    """One entry in the documentation manifest."""

    filename: str
    title: str
    purpose: str
    when_to_load: str
    full_path: Path
    content: str = ""
    keywords: tuple[str, ...] = ()
    when_not_to_load: str = ""
    related_docs: tuple[str, ...] = ()


def _parse_yaml_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file.

    Expects the file to begin with ``---``, followed by YAML, closed by
    another ``---``.  Returns the parsed dict (empty dict on any failure).

    Args:
        text: Full text content of the markdown file.

    Returns:
        Dict of frontmatter fields.
    """
    if not text.startswith("---"):
        return {}
    # Find the closing ---
    end = text.find("---", 3)
    if end == -1:
        return {}
    yaml_block = text[3:end]
    try:
        parsed = yaml.safe_load(yaml_block)
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def _extract_body(text: str) -> str:
    """Return the markdown body after the YAML frontmatter block.

    Args:
        text: Full text content of the markdown file.

    Returns:
        Body text with leading/trailing whitespace stripped.
    """
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    if end == -1:
        return text
    return text[end + 3 :].strip()


def load_docs_manifest(docs_dir: Path) -> list[DocEntry]:
    """Scan a directory of markdown docs and build the manifest.

    Parses each ``.md`` file's YAML frontmatter to extract title, purpose,
    when-to-load guidance, keywords, negative signals, and related docs.
    Also pre-loads the full body text so no per-invocation file I/O is needed.

    Args:
        docs_dir: Path to the directory containing technical documentation.

    Returns:
        List of DocEntry instances, sorted by filename for deterministic ordering.
    """
    entries: list[DocEntry] = []
    if not docs_dir.is_dir():
        logger.warning("Docs directory does not exist: %s", docs_dir)
        return entries

    for md_file in sorted(docs_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Could not read documentation file: %s", md_file)
            continue

        fm = _parse_yaml_frontmatter(text)
        title = fm.get("title", "")
        if not title:
            title = md_file.stem.replace("_", " ").title()

        raw_keywords = fm.get("keywords", [])
        keywords = tuple(raw_keywords) if isinstance(raw_keywords, list) else ()

        raw_related = fm.get("related_docs", [])
        related_docs = tuple(raw_related) if isinstance(raw_related, list) else ()

        entries.append(
            DocEntry(
                filename=md_file.name,
                title=title,
                purpose=fm.get("purpose", ""),
                when_to_load=fm.get("when_to_load", ""),
                full_path=md_file,
                content=_extract_body(text),
                keywords=keywords,
                when_not_to_load=fm.get("when_not_to_load", ""),
                related_docs=related_docs,
            )
        )

    return entries


def _format_manifest_for_prompt(manifest: list[DocEntry]) -> str:
    """Format the manifest as a numbered list for the selection LLM prompt.

    Includes keywords and negative signals when available to improve
    selection accuracy.

    Args:
        manifest: List of DocEntry instances.

    Returns:
        Formatted string with one entry per document.
    """
    parts: list[str] = []
    for i, entry in enumerate(manifest):
        lines = [
            f"[{i}] {entry.title}",
            f"    Purpose: {entry.purpose}",
        ]
        if entry.keywords:
            lines.append(f"    Keywords: {', '.join(entry.keywords)}")
        lines.append(f"    When to load: {entry.when_to_load}")
        if entry.when_not_to_load:
            lines.append(f"    When NOT to load: {entry.when_not_to_load}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool schema (LLM sees this as a callable tool; execution routes through nodes)
# ---------------------------------------------------------------------------


class DocsToolInput(BaseModel):
    question: str = Field(
        description=(
            "A question about economic complexity methodology, metric definitions, "
            "data sources, or how to reproduce Atlas visualizations."
        )
    )
    context: str = Field(
        default="",
        description=(
            "The broader user query or reasoning for why this documentation is needed. "
            "Helps the documentation tool tailor its response to the actual use case."
        ),
    )


@tool("docs_tool", args_schema=DocsToolInput)
def _docs_tool_schema(question: str, context: str = "") -> str:
    """Retrieves technical documentation about economic complexity metrics,
    data methodology, classification systems, and Atlas visualization reproduction.

    Use this tool when you need deeper understanding of:
    - Metric definitions (ECI, PCI, RCA, COI, COG, distance, proximity, etc.)
    - Trade data methodology (mirror statistics, CIF/FOB, data cleaning)
    - Classification systems (HS92, HS12, SITC, services)
    - Data coverage and year ranges
    - How to reproduce Atlas country pages or explore visualizations

    Do NOT use this tool for actual data queries — use query_tool or atlas_graphql instead.
    This tool does not count against your query limit."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")


# ---------------------------------------------------------------------------
# Pydantic model for structured selection
# ---------------------------------------------------------------------------

# Default kept for backwards compatibility and tests that don't pass max_docs.
DEFAULT_MAX_DOCS = 2


class DocsSelection(BaseModel):
    """LLM output: which documents to load from the manifest."""

    reasoning: str = Field(
        description="Brief explanation of why these documents are relevant."
    )
    selected_indices: list[int] = Field(
        description=(
            "Zero-based indices of the most relevant documents from the manifest."
        ),
    )


def _make_docs_selection_model(max_docs: int) -> type[DocsSelection]:
    """Create a DocsSelection subclass with a dynamic max_length constraint.

    Args:
        max_docs: Maximum number of documents the LLM may select.

    Returns:
        A Pydantic model class with the appropriate max_length on selected_indices.
    """
    return type(
        "DocsSelection",
        (BaseModel,),
        {
            "__annotations__": {
                "reasoning": str,
                "selected_indices": list[int],
            },
            "reasoning": Field(
                description="Brief explanation of why these documents are relevant."
            ),
            "selected_indices": Field(
                description=(
                    f"Zero-based indices of the 1-{max_docs} most relevant documents "
                    f"from the manifest. Select at most {max_docs} documents."
                ),
                max_length=max_docs,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Pipeline node functions
# ---------------------------------------------------------------------------


async def extract_docs_question(state: AtlasAgentState) -> dict:
    """Extract question and context from the agent's docs_tool call args.

    Resets all ``docs_*`` state fields to defaults before populating
    the new question and context.  Pure state manipulation — no LLM call.
    """
    async with node_timer("extract_docs_question", "docs_tool") as t:
        last_msg = state["messages"][-1]
        if len(last_msg.tool_calls) > 1:
            logger.warning(
                "LLM produced %d parallel tool_calls; only the first will be executed.",
                len(last_msg.tool_calls),
            )
        args = last_msg.tool_calls[0]["args"]
        question = args.get("question", "")
        context = args.get("context", "")

    update = dict(_DOCS_STATE_DEFAULTS)
    update["docs_question"] = question
    update["docs_context"] = context
    update["step_timing"] = [t.record]
    return update


async def select_docs(
    state: AtlasAgentState,
    *,
    lightweight_model: BaseLanguageModel,
    manifest: list[DocEntry],
    max_docs: int = DEFAULT_MAX_DOCS,
) -> dict:
    """Select relevant docs from the manifest using an LLM.

    Presents the manifest to a lightweight LLM with structured output to
    choose which documents to load for synthesis in the next node.

    Error handling: Selection fails → select ALL docs (fallback).
    Node must never raise.

    Args:
        state: Current agent state with docs_question and docs_context.
        lightweight_model: Lightweight LLM for selection.
        manifest: Pre-loaded documentation manifest.
        max_docs: Maximum number of documents to select (default 2).

    Returns:
        Dict with docs_selected_files.
    """
    import time as _time

    async with node_timer("select_docs", "docs_tool") as _t:
        question = state.get("docs_question", "")
        context = state.get("docs_context", "")
        context_block = f"**Context:** {context}" if context else ""

        selected_entries: list[DocEntry] = []
        usage_records: list[dict] = []

        try:
            from langchain_core.callbacks import UsageMetadataCallbackHandler

            manifest_text = _format_manifest_for_prompt(manifest)
            selection_prompt = DOCUMENT_SELECTION_PROMPT.format(
                question=question,
                context_block=context_block,
                manifest=manifest_text,
                max_docs=max_docs,
            )

            selection_model = _make_docs_selection_model(max_docs)
            selection_llm = lightweight_model.with_structured_output(selection_model)
            selection_handler = UsageMetadataCallbackHandler()
            llm_start = _time.monotonic()
            selection = await selection_llm.ainvoke(
                selection_prompt, config={"callbacks": [selection_handler]}
            )
            _t.mark_llm(llm_start, _time.monotonic())
            usage_records.append(
                make_usage_record_from_callback(
                    "select_docs", "docs_tool", selection_handler
                )
            )

            valid_indices = [
                i for i in selection.selected_indices if 0 <= i < len(manifest)
            ]

            if not valid_indices:
                logger.warning(
                    "LLM selected no valid doc indices; falling back to all docs."
                )
                selected_entries = list(manifest)
            else:
                selected_entries = [manifest[i] for i in valid_indices]

        except Exception:
            logger.exception(
                "Doc selection LLM failed; loading all documents as fallback."
            )
            selected_entries = list(manifest)

        selected_filenames = [e.filename for e in selected_entries]

    result: dict = {
        "docs_selected_files": selected_filenames,
        "step_timing": [_t.record],
    }
    if usage_records:
        result["token_usage"] = usage_records
    return result


def _assemble_selected_content(
    selected_filenames: list[str],
    manifest: list[DocEntry],
) -> str:
    """Look up selected files in the manifest and assemble their content.

    Args:
        selected_filenames: Filenames chosen by select_docs.
        manifest: Pre-loaded documentation manifest.

    Returns:
        Concatenated document content string (may be empty).
    """
    manifest_by_name = {e.filename: e for e in manifest}
    parts: list[str] = []
    for filename in selected_filenames:
        entry = manifest_by_name.get(filename)
        if entry is None:
            continue
        body = entry.content
        if not body:
            try:
                text = entry.full_path.read_text(encoding="utf-8")
                body = _extract_body(text)
            except OSError:
                logger.warning("Could not read doc file: %s", entry.full_path)
                continue
        parts.append(f"--- {entry.title} ({entry.filename}) ---\n\n{body}")
    return "\n\n".join(parts)


async def synthesize_docs(
    state: AtlasAgentState,
    *,
    lightweight_model: BaseLanguageModel,
    manifest: list[DocEntry],
) -> dict:
    """Synthesize a response from previously selected documentation files.

    Reads docs_selected_files from state, looks up their content in the
    manifest, and runs a synthesis LLM call.

    Error handling: Synthesis fails → return raw concatenated docs.
    Node must never raise.

    Args:
        state: Current agent state with docs_question, docs_context,
            and docs_selected_files.
        lightweight_model: Lightweight LLM for synthesis.
        manifest: Pre-loaded documentation manifest.

    Returns:
        Dict with docs_synthesis.
    """
    import time as _time

    async with node_timer("synthesize_docs", "docs_tool") as _t:
        question = state.get("docs_question", "")
        context = state.get("docs_context", "")
        context_block = f"**Context:** {context}" if context else ""
        selected_filenames = state.get("docs_selected_files", [])
        usage_records: list[dict] = []

        docs_content = _assemble_selected_content(selected_filenames, manifest)

        if not docs_content:
            return {
                "docs_synthesis": "No documentation files could be loaded.",
                "step_timing": [_t.record],
            }

        try:
            synthesis_prompt = DOCUMENTATION_SYNTHESIS_PROMPT.format(
                question=question,
                context_block=context_block,
                docs_content=docs_content,
            )
            llm_start = _time.monotonic()
            response = await lightweight_model.ainvoke(synthesis_prompt)
            _t.mark_llm(llm_start, _time.monotonic())
            synthesis = (
                response.content if hasattr(response, "content") else str(response)
            )
            usage_records.append(
                make_usage_record_from_msg("synthesize_docs", "docs_tool", response)
            )
        except Exception:
            logger.exception(
                "Doc synthesis LLM failed; returning raw concatenated docs as fallback."
            )
            synthesis = docs_content

    result: dict = {
        "docs_synthesis": synthesis,
        "step_timing": [_t.record],
    }
    if usage_records:
        result["token_usage"] = usage_records
    return result


async def format_docs_results(state: AtlasAgentState) -> dict:
    """Create a ToolMessage with the synthesized documentation response.

    Does NOT increment ``queries_executed`` — this is a knowledge lookup,
    not a data query.
    """
    async with node_timer("format_docs_results", "docs_tool") as t:
        last_msg = state["messages"][-1]
        tool_calls = last_msg.tool_calls

        synthesis = state.get("docs_synthesis", "")
        if not synthesis:
            synthesis = "No relevant documentation found."

        messages: list[ToolMessage] = [
            ToolMessage(
                content=synthesis, tool_call_id=tool_calls[0]["id"], name="docs_tool"
            )
        ]
        # Handle parallel tool_calls (only first is executed)
        for tc in tool_calls[1:]:
            messages.append(
                ToolMessage(
                    content="Only one tool can be executed at a time. Please make additional requests sequentially.",
                    tool_call_id=tc["id"],
                    name="docs_tool",
                )
            )

    return {"messages": messages, "step_timing": [t.record]}
