"""Documentation pipeline node functions for the Atlas agent graph.

Provides async node functions that form a linear pipeline:

    extract_docs_question → retrieve_docs → format_docs_results

When the agent calls ``docs_tool(question="...")``, this pipeline retrieves
relevant documentation chunks via hybrid search (BM25 + vector) and returns
them as a ToolMessage.  The agent can then pass this context to subsequent
data queries via the structured ``context`` field.

Also provides ``retrieve_docs_context`` — a pre-agent node that auto-injects
relevant doc chunks into the agent's system prompt before each turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.state import AtlasAgentState
from src.token_usage import node_timer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DOCS_PIPELINE_NODES = frozenset(
    {
        "extract_docs_question",
        "retrieve_docs",
        "format_docs_results",
        "retrieve_docs_context",
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
    logger.info(
        "load_docs_manifest: docs_dir=%s  exists=%s  is_dir=%s",
        docs_dir,
        docs_dir.exists(),
        docs_dir.is_dir(),
    )
    if not docs_dir.is_dir():
        # List parent contents to diagnose missing directory
        parent = docs_dir.parent
        try:
            siblings = [p.name for p in parent.iterdir()] if parent.is_dir() else []
        except OSError:
            siblings = ["<error listing parent>"]
        logger.warning(
            "Docs directory does not exist: %s  (parent %s contains: %s)",
            docs_dir,
            parent,
            siblings,
        )
        return entries

    all_files = sorted(docs_dir.iterdir())
    md_files = [f for f in all_files if f.suffix == ".md"]
    logger.info(
        "load_docs_manifest: docs_dir=%s  total_entries=%d  md_files=%d  names=%s",
        docs_dir,
        len(all_files),
        len(md_files),
        [f.name for f in all_files[:20]],
    )
    for md_file in md_files:
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

    logger.info("load_docs_manifest: built %d entries from %s", len(entries), docs_dir)
    return entries


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


async def retrieve_docs(
    state: AtlasAgentState,
    *,
    docs_index: Any = None,
    top_k: int = 6,
) -> dict:
    """Retrieve relevant documentation chunks via hybrid search.

    Replaces the old select_docs + synthesize_docs two-LLM-call pipeline
    with a single sub-200ms retrieval call (no LLM at query time).

    Excludes chunk IDs that were already auto-injected into the system prompt.

    Args:
        state: Current agent state with docs_question and docs_context.
        docs_index: DocsIndex instance for hybrid search. If None, returns
            a fallback message.
        top_k: Number of chunks to retrieve.

    Returns:
        Dict with docs_synthesis (formatted chunk text).
    """
    async with node_timer("retrieve_docs", "docs_tool") as _t:
        question = state.get("docs_question", "")
        context = state.get("docs_context", "")
        search_query = f"{question} {context}".strip() if context else question

        if docs_index is None:
            return {
                "docs_synthesis": "Documentation index not available.",
                "step_timing": [_t.record],
            }

        # Exclude already auto-injected chunks
        auto_chunks = state.get("docs_auto_chunks", [])
        exclude_ids = frozenset(c.get("chunk_id", "") for c in auto_chunks)

        try:
            from src.docs_retrieval import format_chunks_for_prompt

            chunks = await docs_index.search(
                search_query, top_k=top_k, exclude_chunk_ids=exclude_ids
            )
            if not chunks:
                synthesis = "No relevant documentation found."
                doc_titles: list[str] = []
            else:
                synthesis = format_chunks_for_prompt(chunks)
                doc_titles = sorted({c.doc_title for c in chunks})
        except Exception:
            logger.exception("Documentation retrieval failed")
            synthesis = "Documentation retrieval encountered an error."
            doc_titles = []

    return {
        "docs_synthesis": synthesis,
        "docs_retrieved_titles": doc_titles,
        "step_timing": [_t.record],
    }


async def retrieve_docs_context(
    state: AtlasAgentState,
    *,
    docs_index: Any = None,
    top_k: int = 6,
) -> dict:
    """Pre-agent node: auto-inject relevant doc chunks into state.

    Runs before the agent node on each turn. Embeds the user's latest
    message and retrieves the top-k chunks, storing them in
    ``docs_auto_chunks`` for the agent node to include in its system prompt.

    Args:
        state: Current agent state (reads latest human message).
        docs_index: DocsIndex instance. If None, returns empty chunks.
        top_k: Number of chunks to auto-inject.

    Returns:
        Dict with docs_auto_chunks (list of chunk dicts).
    """
    async with node_timer("retrieve_docs_context", "agent") as _t:
        if docs_index is None:
            return {"docs_auto_chunks": [], "step_timing": [_t.record]}

        # Extract the latest user message for search
        from langchain_core.messages import HumanMessage

        query = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage) and msg.content:
                query = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                break

        if not query:
            return {"docs_auto_chunks": [], "step_timing": [_t.record]}

        try:
            chunks = await docs_index.search(query, top_k=top_k)
            auto_chunks = [
                {
                    "chunk_id": c.chunk_id,
                    "doc_filename": c.doc_filename,
                    "doc_title": c.doc_title,
                    "section_title": c.section_title,
                    "body": c.body,
                }
                for c in chunks
            ]
        except Exception:
            logger.exception("Auto-injection retrieval failed")
            auto_chunks = []

    return {"docs_auto_chunks": auto_chunks, "step_timing": [_t.record]}


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
                    name=tc["name"],
                )
            )

    return {"messages": messages, "step_timing": [t.record]}
