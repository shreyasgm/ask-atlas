"""build_atlas_graph: assembles the full Atlas LangGraph with SQL + GraphQL + Docs pipelines.

Replaces create_sql_agent() from generate_query.py with a multi-tool graph
that supports SQL-only, GraphQL+SQL, and AUTO modes, plus a documentation
lookup pipeline (docs_tool) available in all modes.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Dict, List, Literal

from langchain_core.language_models import BaseLanguageModel
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
    load_docs_manifest,
    select_and_synthesize,
)
from src.graphql_client import GraphQLBudgetTracker
from src.graphql_pipeline import (
    build_and_execute_graphql,
    classify_query,
    extract_entities,
    extract_graphql_question,
    format_graphql_results,
    resolve_ids,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.sql_pipeline import (
    extract_products_node,
    extract_tool_question,
    format_results_node,
    generate_sql_node,
    get_table_info_node,
    lookup_codes_node,
    max_queries_exceeded_node,
    validate_sql_node,
    execute_sql_node,
)
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
    table_descriptions: Dict,
    example_queries: List[Dict] = [],
    top_k_per_query: int = 15,
    max_uses: int = 3,
    checkpointer: BaseCheckpointSaver | None = None,
    async_engine: AsyncEngine | None = None,
    graphql_client=None,
    country_pages_client=None,
    country_cache=None,
    product_cache=None,
    services_cache=None,
    agent_mode: AgentMode = AgentMode.AUTO,
    budget_tracker: GraphQLBudgetTracker | None = None,
    docs_dir: Path | None = None,
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
        product_cache: Optional CatalogCache for product lookups.
        services_cache: Optional CatalogCache for services lookups.
        agent_mode: Operating mode (AUTO, GRAPHQL_SQL, SQL_ONLY).
        budget_tracker: Optional GraphQLBudgetTracker for AUTO mode.
        docs_dir: Path to documentation directory. Defaults to src/docs/.

    Returns:
        A compiled LangGraph StateGraph.
    """

    # --- Routing functions ---

    def route_after_agent(
        state: AtlasAgentState,
    ) -> Literal[
        "extract_tool_question",
        "extract_graphql_question",
        "extract_docs_question",
        "max_queries_exceeded",
        "__end__",
    ]:
        last_msg = state["messages"][-1]
        if not (hasattr(last_msg, "tool_calls") and last_msg.tool_calls):
            return END
        tool_name = last_msg.tool_calls[0]["name"]
        # docs_tool bypasses the query budget — check BEFORE budget gate
        if tool_name == "docs_tool":
            return "extract_docs_question"
        if state.get("queries_executed", 0) >= max_uses:
            return "max_queries_exceeded"
        if tool_name == "query_tool":
            return "extract_tool_question"
        elif tool_name == "atlas_graphql":
            return "extract_graphql_question"
        return END

    def route_after_classify(
        state: AtlasAgentState,
    ) -> Literal["format_graphql_results", "extract_entities"]:
        classification = state.get("graphql_classification") or {}
        if classification.get("query_type") == "reject":
            return "format_graphql_results"
        return "extract_entities"

    def route_after_validation(
        state: AtlasAgentState,
    ) -> Literal["format_results", "execute_sql"]:
        return "format_results" if state.get("last_error") else "execute_sql"

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
        partial(get_table_info_node, db=db, table_descriptions=table_descriptions),
    )
    builder.add_node(
        "generate_sql",
        partial(
            generate_sql_node,
            llm=llm,
            example_queries=example_queries,
            max_results=top_k_per_query,
        ),
    )
    builder.add_node(
        "validate_sql",
        partial(validate_sql_node, table_descriptions=table_descriptions),
    )
    builder.add_node(
        "execute_sql",
        partial(
            execute_sql_node,
            async_engine=async_engine if async_engine is not None else engine,
        ),
    )
    builder.add_node("format_results", format_results_node)
    builder.add_node("max_queries_exceeded", max_queries_exceeded_node)

    # GraphQL pipeline nodes
    #
    # RetryPolicy for nodes that make LLM calls: on transient errors (rate
    # limits, timeouts, connection failures) LangGraph retries the node
    # automatically.  classify_query and extract_entities let errors propagate
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
        "classify_query",
        partial(classify_query, lightweight_model=lightweight_llm),
        retry_policy=_llm_retry,
    )
    builder.add_node(
        "extract_entities",
        partial(extract_entities, lightweight_model=lightweight_llm),
        retry_policy=_llm_retry,
    )
    # resolve_ids needs three separate catalog caches
    _resolve_kwargs: dict = {
        "lightweight_model": lightweight_llm,
        "country_cache": country_cache,
        "product_cache": product_cache,
        "services_cache": services_cache,
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
            product_cache=product_cache,
            country_cache=country_cache,
        ),
    )

    # Docs pipeline nodes
    _docs_dir = docs_dir or Path(__file__).resolve().parent / "docs"
    _docs_manifest = load_docs_manifest(_docs_dir)
    builder.add_node("extract_docs_question", extract_docs_question)
    builder.add_node(
        "select_and_synthesize",
        partial(
            select_and_synthesize,
            lightweight_model=lightweight_llm,
            manifest=_docs_manifest,
        ),
        retry_policy=_llm_retry,
    )
    builder.add_node("format_docs_results", format_docs_results)

    # --- Edges ---
    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "extract_tool_question": "extract_tool_question",
            "extract_graphql_question": "extract_graphql_question",
            "extract_docs_question": "extract_docs_question",
            "max_queries_exceeded": "max_queries_exceeded",
            END: END,
        },
    )

    # SQL pipeline
    builder.add_edge("extract_tool_question", "extract_products")
    builder.add_edge("extract_products", "lookup_codes")
    builder.add_edge("lookup_codes", "get_table_info")
    builder.add_edge("get_table_info", "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges(
        "validate_sql",
        route_after_validation,
        {
            "execute_sql": "execute_sql",
            "format_results": "format_results",
        },
    )
    builder.add_edge("execute_sql", "format_results")
    builder.add_edge("format_results", "agent")
    builder.add_edge("max_queries_exceeded", "agent")

    # GraphQL pipeline
    builder.add_edge("extract_graphql_question", "classify_query")
    builder.add_conditional_edges(
        "classify_query",
        route_after_classify,
        {
            "format_graphql_results": "format_graphql_results",
            "extract_entities": "extract_entities",
        },
    )
    builder.add_edge("extract_entities", "resolve_ids")
    builder.add_edge("resolve_ids", "build_and_execute_graphql")
    builder.add_edge("build_and_execute_graphql", "format_graphql_results")
    builder.add_edge("format_graphql_results", "agent")

    # Docs pipeline
    builder.add_edge("extract_docs_question", "select_and_synthesize")
    builder.add_edge("select_and_synthesize", "format_docs_results")
    builder.add_edge("format_docs_results", "agent")

    memory = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=memory)
