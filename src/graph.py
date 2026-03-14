"""build_atlas_graph: assembles the full Atlas LangGraph with SQL + GraphQL + Docs pipelines.

Replaces create_sql_agent() from generate_query.py with a multi-tool graph
that supports SQL-only, GraphQL+SQL, and AUTO modes, plus a documentation
lookup pipeline (docs_tool) available in all modes.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Literal

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agent_node import make_agent_node
from src.config import AgentMode
from src.docs_pipeline import (
    extract_docs_question,
    format_docs_results,
    retrieve_docs,
    retrieve_docs_context,
)
from src.graphql_client import GraphQLBudgetTracker
from src.graphql_pipeline import (
    assess_graphql_result,
    build_and_execute_graphql,
    execute_catalog_lookup,
    extract_graphql_question,
    format_graphql_results,
    plan_query,
    resolve_ids,
    route_after_assessment,
)
from src.graphql_subagent import build_graphql_subagent, graphql_correction_agent_node
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.sql_pipeline import (
    extract_products_node,
    extract_tool_question,
    format_results_node,
    get_table_info_node,
    lookup_codes_node,
    max_queries_exceeded_node,
)
from src.sql_subagent import build_sql_subagent, sql_query_agent_node
from src.state import AtlasAgentState

try:
    from src.cache import CatalogCache
except ImportError:
    CatalogCache = None

try:
    from src.graphql_client import AtlasGraphQLClient
except ImportError:
    AtlasGraphQLClient = None


def build_atlas_graph(
    llm: BaseLanguageModel,
    lightweight_llm: BaseLanguageModel,
    db: SQLDatabaseWithSchemas,
    engine: Engine,
    table_descriptions: dict,
    example_queries: list[dict] | None = None,
    top_k_per_query: int = 15,
    max_uses: int = 3,
    checkpointer: BaseCheckpointSaver | None = None,
    async_engine: AsyncEngine | None = None,
    async_db=None,
    graphql_client=None,
    country_pages_client=None,
    country_cache=None,
    product_caches: dict | None = None,
    services_cache=None,
    group_cache=None,
    agent_mode: AgentMode = AgentMode.AUTO,
    budget_tracker: GraphQLBudgetTracker | None = None,
    docs_dir: Path | None = None,
    max_docs_per_selection: int = 2,
    docs_index=None,
) -> CompiledStateGraph:
    """Build the full Atlas agent graph with SQL, optional GraphQL, and docs pipelines.

    Args:
        llm: Frontier language model for agent reasoning and SQL generation.
        lightweight_llm: Lightweight model for extraction and classification.
        db: SQLDatabaseWithSchemas instance.
        engine: SQLAlchemy sync engine.
        table_descriptions: Dict of table descriptions keyed by schema.
        example_queries: List of example question/query pairs.
        top_k_per_query: Maximum rows returned per SQL query.
        max_uses: Maximum number of tool uses per question.
        checkpointer: Optional checkpoint saver. Falls back to MemorySaver.
        async_engine: Optional async SQLAlchemy engine for true async DB I/O.
        graphql_client: Optional AtlasGraphQLClient instance for the Explore API.
        country_pages_client: Optional AtlasGraphQLClient for the Country Pages API.
        country_cache: Optional CatalogCache for country lookups.
        product_caches: Dict of CatalogCache instances keyed by classification
            (e.g. ``{"HS92": ..., "HS12": ...}``).
        services_cache: Optional CatalogCache for services lookups.
        agent_mode: Operating mode (AUTO, GRAPHQL_SQL, SQL_ONLY).
        budget_tracker: Optional GraphQLBudgetTracker for AUTO mode.
        docs_dir: Path to documentation directory. Defaults to src/docs/.

    Returns:
        A compiled LangGraph StateGraph.
    """

    if example_queries is None:
        example_queries = []

    # --- Routing functions ---

    def route_after_agent(
        state: AtlasAgentState,
    ) -> Literal[
        "extract_tool_question",
        "extract_graphql_question",
        "extract_docs_question",
        "execute_catalog_lookup",
        "max_queries_exceeded",
        "tool_call_nudge",
        "__end__",
    ]:
        last_msg = state["messages"][-1]
        if not (hasattr(last_msg, "tool_calls") and last_msg.tool_calls):
            # Agent wants to respond without a tool call — check if any tool
            # was ever called in this conversation.  If not, nudge once.
            has_tool_msg = any(isinstance(m, ToolMessage) for m in state["messages"])
            if not has_tool_msg:
                # Check if we already nudged (prevent infinite loop)
                nudge_already = any(
                    isinstance(m, HumanMessage)
                    and hasattr(m, "content")
                    and "You must call a tool before answering" in (m.content or "")
                    for m in state["messages"]
                )
                if not nudge_already:
                    return "tool_call_nudge"
            return END
        tool_name = last_msg.tool_calls[0]["name"]
        # Budget-free tools: bypass query budget gate
        if tool_name == "docs_tool":
            return "extract_docs_question"
        if tool_name == "lookup_catalog":
            return "execute_catalog_lookup"
        if state.get("queries_executed", 0) >= max_uses:
            return "max_queries_exceeded"
        if tool_name == "query_tool":
            return "extract_tool_question"
        elif tool_name == "atlas_graphql":
            return "extract_graphql_question"
        return END

    async def tool_call_nudge(state: AtlasAgentState) -> dict:
        """Inject a nudge message asking the agent to call a tool before answering."""
        return {
            "messages": [
                HumanMessage(
                    content=(
                        "You must call a tool before answering data questions. "
                        "Use query_tool, atlas_graphql, or docs_tool to look up "
                        "the information needed. However, if the question is not "
                        "related to trade data, is harmful, or is otherwise "
                        "inappropriate, you may respond directly without calling "
                        "a tool."
                    )
                )
            ]
        }

    def route_after_plan(
        state: AtlasAgentState,
    ) -> Literal["format_graphql_results", "resolve_ids"]:
        classification = state.get("graphql_classification") or {}
        if classification.get("query_type") == "reject":
            return "format_graphql_results"
        return "resolve_ids"

    # --- Build graph ---
    builder = StateGraph(AtlasAgentState)

    # Agent node
    agent_fn = make_agent_node(
        llm=llm,
        agent_mode=agent_mode,
        max_uses=max_uses,
        top_k_per_query=top_k_per_query,
        budget_tracker=budget_tracker,
    )
    builder.add_node("agent", agent_fn)

    # SQL pipeline nodes
    builder.add_node("extract_tool_question", extract_tool_question)
    builder.add_node(
        "extract_products",
        partial(extract_products_node, llm=lightweight_llm, engine=engine),
    )
    _lookup_kwargs = {"llm": lightweight_llm, "engine": engine}
    if async_engine is not None:
        _lookup_kwargs["async_engine"] = async_engine
    builder.add_node("lookup_codes", partial(lookup_codes_node, **_lookup_kwargs))
    builder.add_node(
        "get_table_info",
        partial(
            get_table_info_node,
            db=db,
            table_descriptions=table_descriptions,
            async_db=async_db,
        ),
    )
    # SQL sub-agent (replaces generate_sql + validate_sql + execute_sql + retry loop)
    _subagent, _top_k = build_sql_subagent(
        llm=llm,
        lightweight_llm=lightweight_llm,
        db=db,
        engine=engine,
        table_descriptions=table_descriptions,
        async_engine=async_engine if async_engine is not None else engine,
        async_db=async_db,
        top_k=top_k_per_query,
    )
    builder.add_node(
        "sql_query_agent",
        partial(
            sql_query_agent_node,
            subagent=_subagent,
            top_k=_top_k,
            example_queries=example_queries,
        ),
    )
    builder.add_node("format_results", format_results_node)
    builder.add_node("max_queries_exceeded", max_queries_exceeded_node)

    # GraphQL pipeline nodes
    #
    # RetryPolicy for nodes that make LLM calls: on transient errors (rate
    # limits, timeouts, connection failures) LangGraph retries the node
    # automatically.  plan_query lets errors propagate
    # (no internal try/except) so RetryPolicy can trigger.  resolve_ids has
    # its own Step-C fallback but we add RetryPolicy as a defensive layer.
    # build_and_execute_graphql handles retries internally via the GraphQL
    # client so it does NOT get a RetryPolicy.
    _llm_retry = RetryPolicy(
        initial_interval=0.5,
        backoff_factor=1.5,
        max_attempts=3,
    )

    builder.add_node(
        "extract_graphql_question",
        partial(extract_graphql_question),
    )
    builder.add_node(
        "plan_query",
        partial(plan_query, lightweight_model=lightweight_llm),
        retry_policy=_llm_retry,
    )
    # resolve_ids needs catalog caches for entity resolution
    _resolve_kwargs: dict = {
        "lightweight_model": lightweight_llm,
        "country_cache": country_cache,
        "product_caches": product_caches or {},
        "services_cache": services_cache,
        "group_cache": group_cache,
    }
    builder.add_node(
        "resolve_ids",
        partial(resolve_ids, **_resolve_kwargs),
        retry_policy=_llm_retry,
    )
    builder.add_node(
        "build_and_execute_graphql",
        partial(
            build_and_execute_graphql,
            graphql_client=graphql_client,
            country_pages_client=country_pages_client,
        ),
    )
    builder.add_node(
        "format_graphql_results",
        partial(
            format_graphql_results,
            product_caches=product_caches or {},
            country_cache=country_cache,
            services_cache=services_cache,
        ),
    )

    # GraphQL assessment + correction agent
    builder.add_node(
        "assess_graphql_result",
        partial(assess_graphql_result, lightweight_model=lightweight_llm),
        retry_policy=_llm_retry,
    )
    _graphql_subagent = build_graphql_subagent(
        llm=llm,
        graphql_client=graphql_client,
        country_pages_client=country_pages_client,
        country_cache=country_cache,
        product_caches=product_caches or {},
        services_cache=services_cache,
        group_cache=group_cache,
    )
    builder.add_node(
        "graphql_correction_agent",
        partial(graphql_correction_agent_node, subagent=_graphql_subagent),
    )

    # Catalog lookup node (budget-free, like docs_tool)
    builder.add_node(
        "execute_catalog_lookup",
        partial(
            execute_catalog_lookup,
            product_caches=product_caches or {},
            country_cache=country_cache,
            services_cache=services_cache,
        ),
    )

    # Anti-hallucination nudge node
    builder.add_node("tool_call_nudge", tool_call_nudge)

    # Docs pipeline nodes (retrieval-based, no LLM at query time)
    builder.add_node("extract_docs_question", extract_docs_question)
    builder.add_node(
        "retrieve_docs",
        partial(retrieve_docs, docs_index=docs_index, top_k=6),
    )
    builder.add_node("format_docs_results", format_docs_results)

    # Auto-injection node: retrieves docs context before each agent turn
    builder.add_node(
        "retrieve_docs_context",
        partial(retrieve_docs_context, docs_index=docs_index, top_k=6),
    )

    # --- Edges ---
    if docs_index is not None:
        builder.add_edge(START, "retrieve_docs_context")
        builder.add_edge("retrieve_docs_context", "agent")
    else:
        builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "extract_tool_question": "extract_tool_question",
            "extract_graphql_question": "extract_graphql_question",
            "extract_docs_question": "extract_docs_question",
            "execute_catalog_lookup": "execute_catalog_lookup",
            "max_queries_exceeded": "max_queries_exceeded",
            "tool_call_nudge": "tool_call_nudge",
            END: END,
        },
    )
    builder.add_edge("tool_call_nudge", "agent")
    builder.add_edge("execute_catalog_lookup", "agent")

    # SQL pipeline
    builder.add_edge("extract_tool_question", "extract_products")
    builder.add_edge("extract_products", "lookup_codes")
    builder.add_edge("lookup_codes", "get_table_info")
    builder.add_edge("get_table_info", "sql_query_agent")
    builder.add_edge("sql_query_agent", "format_results")
    builder.add_edge("format_results", "agent")
    builder.add_edge("max_queries_exceeded", "agent")

    # GraphQL pipeline
    builder.add_edge("extract_graphql_question", "plan_query")
    builder.add_conditional_edges(
        "plan_query",
        route_after_plan,
        {
            "format_graphql_results": "format_graphql_results",
            "resolve_ids": "resolve_ids",
        },
    )
    builder.add_edge("resolve_ids", "build_and_execute_graphql")
    builder.add_edge("build_and_execute_graphql", "assess_graphql_result")
    builder.add_conditional_edges(
        "assess_graphql_result",
        route_after_assessment,
        {
            "format_graphql_results": "format_graphql_results",
            "graphql_correction_agent": "graphql_correction_agent",
        },
    )
    builder.add_edge("graphql_correction_agent", "format_graphql_results")
    builder.add_edge("format_graphql_results", "agent")

    # Docs pipeline (retrieval-based: 3 nodes, no LLM)
    builder.add_edge("extract_docs_question", "retrieve_docs")
    builder.add_edge("retrieve_docs", "format_docs_results")
    builder.add_edge("format_docs_results", "agent")

    memory = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=memory)
