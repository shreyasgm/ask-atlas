"""Agent node factory for the Atlas graph.

Provides:
- ``_atlas_graphql_schema``: Schema-only tool for the atlas_graphql tool
- ``resolve_effective_mode()``: Pure function for mode resolution
- ``make_agent_node()``: Factory returning the async agent_node callable
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.config import AgentMode
from src.docs_pipeline import _docs_tool_schema
from src.graphql_client import GraphQLBudgetTracker
from src.prompts import (
    GRAPHQL_ONLY_OVERRIDE,
    build_dual_tool_system_prompt,
    build_sql_only_system_prompt,
)
from src.sql_pipeline import _query_tool_schema
from src.state import AtlasAgentState
from src.token_usage import make_usage_record_from_msg, node_timer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# atlas_graphql tool schema (schema-only; execution routes through graph nodes)
# ---------------------------------------------------------------------------


class AtlasGraphQLInput(BaseModel):
    question: str = Field(
        description="A question about trade data or economic complexity"
    )
    context: str = Field(
        default="",
        description="Optional corrective feedback or technical context. Use when retrying after an "
        "unsatisfactory result (e.g., 'use HS92 classification', 'the country is Turkey "
        "not the poultry product') or to pass methodology notes from docs_tool.",
    )


class LookupCatalogInput(BaseModel):
    entity_type: str = Field(description="'product' or 'country'")
    ids: list[int] = Field(description="List of Atlas internal IDs to look up")
    product_class: str = Field(
        default="HS12",
        description="Classification system (e.g. HS92, HS12, SITC). Only relevant for product lookups.",
    )


@tool("lookup_catalog", args_schema=LookupCatalogInput)
def _lookup_catalog_schema(
    entity_type: str, ids: list[int], product_class: str = "HS12"
) -> str:
    """Look up human-readable names for Atlas internal product or country IDs.

    Use when you receive data with numeric IDs but no corresponding names.
    Returns a mapping of ID -> name for each resolved ID."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")


@tool("atlas_graphql", args_schema=AtlasGraphQLInput)
def _atlas_graphql_schema(question: str, context: str = "") -> str:
    """Queries the Atlas platform's GraphQL API for pre-calculated economic complexity
    metrics, country profiles, and trade visualizations.

    Use this tool when you need:
    - Country profiles: GDP, population, ECI rank, diversification grade, peer comparisons
    - Complexity trends over time: how a country's ECI and export basket changed
    - Products a country recently started exporting (gained comparative advantage)
    - Bilateral trade breakdowns: what products country A exports to country B
    - Product market share and global rankings
    - Growth opportunities and feasibility metrics for country-product pairs
    - Pre-calculated trade data visualizations ("treemaps", "over time" charts)

    Do NOT use this tool for:
    - Custom aggregations (use query_tool for GROUP BY, SUM, AVG, etc.)
    - Questions requiring multi-table SQL joins
    - Any question this tool REJECTS — it will return a rejection message; fall back to query_tool

    Returns: JSON data from the Atlas API, plus Atlas visualization links when available.
    Input: a natural language question about trade data or economic complexity."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def resolve_effective_mode(
    config_mode: AgentMode,
    budget_tracker: GraphQLBudgetTracker | None,
) -> AgentMode:
    """Resolve the effective agent mode given config and budget state.

    Args:
        config_mode: The configured agent mode (AUTO, GRAPHQL_SQL, SQL_ONLY, GRAPHQL_ONLY).
        budget_tracker: The budget tracker instance, or None.

    Returns:
        The effective AgentMode to use for this invocation.
    """
    if config_mode == AgentMode.SQL_ONLY:
        return AgentMode.SQL_ONLY
    if config_mode == AgentMode.GRAPHQL_ONLY:
        return AgentMode.GRAPHQL_ONLY
    if config_mode == AgentMode.GRAPHQL_SQL:
        return AgentMode.GRAPHQL_SQL
    # AUTO: check budget
    if budget_tracker is not None and budget_tracker.is_available():
        return AgentMode.GRAPHQL_SQL
    return AgentMode.SQL_ONLY


# ---------------------------------------------------------------------------
# Agent node factory
# ---------------------------------------------------------------------------


def make_agent_node(
    llm: BaseLanguageModel,
    agent_mode: AgentMode,
    max_uses: int,
    top_k_per_query: int,
    budget_tracker: GraphQLBudgetTracker | None = None,
) -> Callable[[AtlasAgentState], Awaitable[dict]]:
    """Create the agent_node async callable for use in the Atlas graph.

    Args:
        llm: The language model to use for the agent.
        agent_mode: The configured agent mode.
        max_uses: Maximum number of tool uses per question.
        top_k_per_query: Maximum rows returned per SQL query.
        budget_tracker: Optional budget tracker for AUTO mode.

    Returns:
        An async callable that takes AtlasAgentState and returns a dict update.
    """

    async def agent_node(state: AtlasAgentState) -> dict:
        async with node_timer("agent", "agent") as t:
            # Repair orphan tool calls left by a prior cancelled/crashed request.
            # OpenAI requires every tool_call to have a matching ToolMessage.
            # If a previous stream was killed mid-pipeline, the AIMessage with
            # tool_calls may be checkpointed without its ToolMessage responses.
            messages = list(state["messages"])
            orphan_stubs: list[ToolMessage] = []
            pending_tool_ids: set[str] = set()
            for msg in messages:
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    for tc in msg.tool_calls:
                        pending_tool_ids.add(tc["id"])
                elif isinstance(msg, ToolMessage):
                    pending_tool_ids.discard(msg.tool_call_id)
            if pending_tool_ids:
                logger.warning(
                    "Repairing %d orphan tool_call(s) from prior cancelled request",
                    len(pending_tool_ids),
                )
                for tc_id in pending_tool_ids:
                    stub = ToolMessage(
                        content="[Request was cancelled before this tool finished]",
                        tool_call_id=tc_id,
                    )
                    orphan_stubs.append(stub)
                    messages.append(stub)

            # Per-request override takes precedence over construction-time config
            state_mode = state.get("override_agent_mode")
            effective_config_mode = AgentMode(state_mode) if state_mode else agent_mode
            effective_mode = resolve_effective_mode(
                effective_config_mode, budget_tracker
            )
            if effective_mode == AgentMode.GRAPHQL_ONLY:
                tools = [
                    _atlas_graphql_schema,
                    _lookup_catalog_schema,
                    _docs_tool_schema,
                ]
            elif effective_mode == AgentMode.SQL_ONLY:
                tools = [_query_tool_schema, _docs_tool_schema]
            else:
                # GRAPHQL_SQL (and AUTO resolved to GRAPHQL_SQL)
                tools = [
                    _query_tool_schema,
                    _atlas_graphql_schema,
                    _lookup_catalog_schema,
                    _docs_tool_schema,
                ]

            # Select system prompt based on effective mode
            if effective_mode == AgentMode.SQL_ONLY:
                prompt_text = build_sql_only_system_prompt(max_uses, top_k_per_query)
            else:
                remaining = budget_tracker.remaining() if budget_tracker else "unknown"
                budget_status = f"Available ({remaining} calls remaining this window)"
                prompt_text = build_dual_tool_system_prompt(
                    max_uses, top_k_per_query, budget_status
                )
                if effective_mode == AgentMode.GRAPHQL_ONLY:
                    prompt_text = GRAPHQL_ONLY_OVERRIDE + "\n\n" + prompt_text

            # Apply override lines (same logic as legacy create_sql_agent)
            overrides_parts: list[str] = []
            if state.get("override_schema"):
                overrides_parts.append(
                    f"- Classification schema: **{state['override_schema']}**"
                )
            if state.get("override_direction"):
                overrides_parts.append(
                    f"- Trade direction: **{state['override_direction']}**"
                )
            if state.get("override_mode"):
                overrides_parts.append(f"- Trade mode: **{state['override_mode']}**")
            if overrides_parts:
                prompt_text += "\n\n**Active User Overrides:**\n" + "\n".join(
                    overrides_parts
                )
                prompt_text += "\n\nThese overrides take precedence over what the question implies. If the question contradicts an override, briefly note the conflict but follow the override."

            model_with_tools = llm.bind_tools(tools)
            llm_start = time.monotonic()
            response = await model_with_tools.ainvoke(
                [SystemMessage(content=prompt_text)] + messages
            )
            t.mark_llm(llm_start, time.monotonic())

        usage_record = make_usage_record_from_msg("agent", "agent", response)
        return {
            "messages": orphan_stubs + [response],
            "token_usage": [usage_record],
            "step_timing": [t.record],
        }

    return agent_node
