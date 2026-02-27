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

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.state import AtlasAgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DOCS_PIPELINE_NODES = frozenset(
    {
        "extract_docs_question",
        "select_and_synthesize",
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


def _parse_doc_header(text: str) -> tuple[str, str, str]:
    """Parse title, purpose, and when-to-load from a doc's header.

    Args:
        text: Full text content of the markdown file.

    Returns:
        Tuple of (title, purpose, when_to_load).
    """
    title = ""
    purpose = ""
    when_to_load = ""

    lines = text.split("\n")

    # Find first H1 line for title
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    # Find **Purpose:** paragraph
    in_purpose = False
    purpose_lines: list[str] = []
    for line in lines:
        if line.startswith("**Purpose:**"):
            in_purpose = True
            # Grab the rest of this line after the marker
            rest = line[len("**Purpose:**") :].strip()
            if rest:
                purpose_lines.append(rest)
            continue
        if in_purpose:
            if line.startswith("**When to load") or line.startswith("---"):
                in_purpose = False
                continue
            if line.strip():
                purpose_lines.append(line.strip())
            else:
                # Empty line ends the purpose paragraph
                in_purpose = False
    purpose = " ".join(purpose_lines)

    # Find **When to load this document:** paragraph
    in_when = False
    when_lines: list[str] = []
    for line in lines:
        if line.startswith("**When to load this document:**"):
            in_when = True
            rest = line[len("**When to load this document:**") :].strip()
            if rest:
                when_lines.append(rest)
            continue
        if in_when:
            if line.startswith("---"):
                in_when = False
                continue
            if line.strip():
                when_lines.append(line.strip())
            else:
                in_when = False
    when_to_load = " ".join(when_lines)

    return title, purpose, when_to_load


def load_docs_manifest(docs_dir: Path) -> list[DocEntry]:
    """Scan a directory of markdown docs and build the manifest.

    Parses each ``.md`` file's header to extract title, purpose,
    and when-to-load guidance.  Returns a list of ``DocEntry`` instances.

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

        title, purpose, when_to_load = _parse_doc_header(text)
        if not title:
            title = md_file.stem.replace("_", " ").title()

        entries.append(
            DocEntry(
                filename=md_file.name,
                title=title,
                purpose=purpose,
                when_to_load=when_to_load,
                full_path=md_file,
            )
        )

    return entries


def _format_manifest_for_prompt(manifest: list[DocEntry]) -> str:
    """Format the manifest as a numbered list for the selection LLM prompt.

    Args:
        manifest: List of DocEntry instances.

    Returns:
        Formatted string with one entry per document.
    """
    parts: list[str] = []
    for i, entry in enumerate(manifest):
        parts.append(
            f"[{i}] {entry.title}\n"
            f"    Purpose: {entry.purpose}\n"
            f"    When to load: {entry.when_to_load}"
        )
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


class DocsSelection(BaseModel):
    """LLM output: which documents to load from the manifest."""

    reasoning: str = Field(
        description="Brief explanation of why these documents are relevant."
    )
    selected_indices: list[int] = Field(
        description=(
            "Zero-based indices of the 1-2 most relevant documents from the manifest. "
            "Select at most 2 documents."
        ),
        max_length=2,
    )


# ---------------------------------------------------------------------------
# Pipeline node functions
# ---------------------------------------------------------------------------

# -- Prompts — USER REVIEW PENDING (per CLAUDE.md: never modify LLM prompts without approval) --

_SELECTION_PROMPT = """\
You are a documentation librarian for the Atlas of Economic Complexity.
Given a user's question and optional context, select the 1 or 2 MOST relevant
documents from the manifest below. Pick only the single best document if one
clearly covers the topic; add a second only if the question genuinely spans
two distinct subjects. Never select more than 2.

**Question:** {question}
{context_block}

**Document manifest:**

{manifest}

Return the indices of the 1-2 most relevant documents."""

_SYNTHESIS_PROMPT = """\
You are a technical documentation assistant for the Atlas of Economic Complexity.
Using ONLY the documentation provided below, answer the question directly and
concisely. Include specific formulas, column names, year ranges, and caveats
where they are directly relevant.

**Question:** {question}
{context_block}

**Documentation:**

{docs_content}

Provide a focused, well-organized response. If the documentation doesn't fully
answer the question, say what it does cover and note what's missing."""


async def extract_docs_question(state: AtlasAgentState) -> dict:
    """Extract question and context from the agent's docs_tool call args.

    Resets all ``docs_*`` state fields to defaults before populating
    the new question and context.  Pure state manipulation — no LLM call.
    """
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
    return update


async def select_and_synthesize(
    state: AtlasAgentState,
    *,
    lightweight_model: BaseLanguageModel,
    manifest: list[DocEntry],
) -> dict:
    """Select relevant docs and synthesize a response.

    Two-step LLM process:
    1. Selection: present manifest to lightweight LLM with structured output
       to choose which documents to load.
    2. Synthesis: load selected docs from disk, present to lightweight LLM
       to produce a focused response.

    Error handling:
    - Selection fails → load ALL docs (fallback).
    - Synthesis fails → return raw concatenated docs.
    - Node must never raise.

    Args:
        state: Current agent state with docs_question and docs_context.
        lightweight_model: Lightweight LLM for selection and synthesis.
        manifest: Pre-loaded documentation manifest.

    Returns:
        Dict with docs_selected_files and docs_synthesis.
    """
    question = state.get("docs_question", "")
    context = state.get("docs_context", "")
    context_block = f"**Context:** {context}" if context else ""

    # --- Step A: Selection ---
    selected_entries: list[DocEntry] = []
    selected_filenames: list[str] = []

    try:
        manifest_text = _format_manifest_for_prompt(manifest)
        selection_prompt = _SELECTION_PROMPT.format(
            question=question,
            context_block=context_block,
            manifest=manifest_text,
        )

        selection_llm = lightweight_model.with_structured_output(DocsSelection)
        selection: DocsSelection = await selection_llm.ainvoke(selection_prompt)

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
        logger.exception("Doc selection LLM failed; loading all documents as fallback.")
        selected_entries = list(manifest)

    selected_filenames = [e.filename for e in selected_entries]

    # --- Step B: Load selected files ---
    docs_content_parts: list[str] = []
    for entry in selected_entries:
        try:
            content = entry.full_path.read_text(encoding="utf-8")
            docs_content_parts.append(
                f"--- {entry.title} ({entry.filename}) ---\n\n{content}"
            )
        except OSError:
            logger.warning("Could not read doc file: %s", entry.full_path)

    docs_content = "\n\n".join(docs_content_parts)
    if not docs_content:
        return {
            "docs_selected_files": selected_filenames,
            "docs_synthesis": "No documentation files could be loaded.",
        }

    # --- Step C: Synthesis ---
    try:
        synthesis_prompt = _SYNTHESIS_PROMPT.format(
            question=question,
            context_block=context_block,
            docs_content=docs_content,
        )
        response = await lightweight_model.ainvoke(synthesis_prompt)
        synthesis = response.content if hasattr(response, "content") else str(response)
    except Exception:
        logger.exception(
            "Doc synthesis LLM failed; returning raw concatenated docs as fallback."
        )
        synthesis = docs_content

    return {
        "docs_selected_files": selected_filenames,
        "docs_synthesis": synthesis,
    }


async def format_docs_results(state: AtlasAgentState) -> dict:
    """Create a ToolMessage with the synthesized documentation response.

    Does NOT increment ``queries_executed`` — this is a knowledge lookup,
    not a data query.
    """
    last_msg = state["messages"][-1]
    tool_calls = last_msg.tool_calls

    synthesis = state.get("docs_synthesis", "")
    if not synthesis:
        synthesis = "No relevant documentation found."

    messages: list[ToolMessage] = [
        ToolMessage(content=synthesis, tool_call_id=tool_calls[0]["id"])
    ]
    # Handle parallel tool_calls (only first is executed)
    for tc in tool_calls[1:]:
        messages.append(
            ToolMessage(
                content="Only one tool can be executed at a time. Please make additional requests sequentially.",
                tool_call_id=tc["id"],
            )
        )

    return {"messages": messages}
