"""Agent node factory for the Atlas graph.

Provides:
- ``_atlas_graphql_schema``: Schema-only tool for the atlas_graphql tool
- ``resolve_effective_mode()``: Pure function for mode resolution
- ``make_agent_node()``: Factory returning the async agent_node callable
"""

from __future__ import annotations

from typing import Awaitable, Callable

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.config import AgentMode
from src.graphql_client import GraphQLBudgetTracker
from src.sql_pipeline import _query_tool_schema, build_sql_only_system_prompt
from src.state import AtlasAgentState

# ---------------------------------------------------------------------------
# atlas_graphql tool schema (schema-only; execution routes through graph nodes)
# ---------------------------------------------------------------------------


class AtlasGraphQLInput(BaseModel):
    question: str = Field(
        description="A question about trade data or economic complexity"
    )
    context: str = Field(
        default="",
        description="Additional context from prior turns that may help answer the question. Optional.",
    )


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
# Dual-tool system prompt extension
# ---------------------------------------------------------------------------


_DUAL_TOOL_EXTENSION = """

**Additional Tool: Atlas GraphQL API (atlas_graphql)**

You also have access to the `atlas_graphql` tool, which queries the Atlas platform's
pre-calculated metrics and visualizations. This is complementary to `query_tool`:

| Use `atlas_graphql` for | Use `query_tool` for |
|-------------------------|----------------------|
| ECI/PCI rankings and grades | Custom SQL aggregations |
| Country profiles (GDP, population, diversification grade) | Complex multi-table JOINs |
| Country lookback (how exports changed over N years) | Time-series queries across many years |
| Pre-calculated bilateral trade data | Questions requiring WHERE clauses on raw rows |
| New products a country gained RCA in | Any question atlas_graphql rejects |
| Growth opportunities and product feasibility | |

**Multi-tool strategy:**
- Decompose complex questions into sub-questions and route each to the best tool.
- If `atlas_graphql` returns a rejection message, the query doesn't fit its data model —
  fall back to `query_tool` for that sub-question.
- If a result looks surprising, you may verify it with the other tool.
- Both tools count against your query budget of {max_uses} total uses.

**Atlas visualization links:**
- When `atlas_graphql` returns data, it may include Atlas visualization links.
- Include these links in your final response so users can explore interactively.

**GraphQL API budget:** {budget_status}
"""


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def resolve_effective_mode(
    config_mode: AgentMode,
    budget_tracker: GraphQLBudgetTracker | None,
) -> AgentMode:
    """Resolve the effective agent mode given config and budget state.

    Args:
        config_mode: The configured agent mode (AUTO, GRAPHQL_SQL, SQL_ONLY).
        budget_tracker: The budget tracker instance, or None.

    Returns:
        The effective AgentMode to use for this invocation.
    """
    if config_mode == AgentMode.SQL_ONLY:
        return AgentMode.SQL_ONLY
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
        # Per-request override takes precedence over construction-time config
        state_mode = state.get("override_agent_mode")
        effective_config_mode = AgentMode(state_mode) if state_mode else agent_mode
        effective_mode = resolve_effective_mode(effective_config_mode, budget_tracker)
        tools = [_query_tool_schema]
        if effective_mode != AgentMode.SQL_ONLY:
            tools = [_query_tool_schema, _atlas_graphql_schema]

        # Select system prompt based on effective mode
        prompt_text = build_sql_only_system_prompt(max_uses, top_k_per_query)
        if effective_mode != AgentMode.SQL_ONLY:
            remaining = budget_tracker.remaining() if budget_tracker else "unknown"
            budget_status = f"Available ({remaining} calls remaining this window)"
            prompt_text += _DUAL_TOOL_EXTENSION.format(
                max_uses=max_uses, budget_status=budget_status
            )

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
        response = await model_with_tools.ainvoke(
            [SystemMessage(content=prompt_text)] + state["messages"]
        )
        return {"messages": [response]}

    return agent_node
