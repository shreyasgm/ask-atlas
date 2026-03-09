"""Agentic GraphQL correction sub-agent: multi-turn ReAct loop for GraphQL self-correction.

When the assessment gate flags a GraphQL pipeline result as failed or suspicious,
this sub-agent diagnoses the problem and attempts to fix it using:

  - execute_graphql_template: Re-run a query with corrected type/params
  - execute_graphql_freeform: Run arbitrary GraphQL for exploration
  - explore_catalog: Re-resolve entity IDs via catalog caches
  - introspect_schema: Discover schema types and fields
  - report_results: Finish and report findings back to the parent graph

Mirrors the architecture of ``src/sql_subagent.py``.
"""

from __future__ import annotations

import json
import logging
import operator
import time
from typing import Annotated, Any

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from src.cache import CatalogCache
from src.graphql_client import AtlasGraphQLClient, BudgetExhaustedError
from src.graphql_pipeline import _QUERY_TYPE_TO_API, build_graphql_query
from src.prompts import GRAPHQL_SUBAGENT_PROMPT
from src.prompts._blocks import GRAPHQL_DATA_MAX_YEAR
from src.state import AtlasAgentState
from src.token_usage import make_usage_record_from_msg, node_timer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_GRAPHQL_CORRECTION_ITERATIONS = 10
"""Max correction iterations. Default based on: 2-3 retry cycles ×
3-4 tool calls each. Search space is ~28 query types, 2 endpoints, few classification
options — smaller than SQL's open-ended query space (which uses 12)."""

FREEFORM_RESPONSE_TRUNCATION_THRESHOLD = 50_000
"""Freeform responses over this size (chars) are truncated."""

FREEFORM_TRUNCATED_RETURN_SIZE = 8_000
"""Chars returned when freeform response exceeds threshold."""

INITIAL_CONTEXT_RESPONSE_THRESHOLD = 12_000
"""Responses under this size are included in full in initial context."""

INITIAL_CONTEXT_SAMPLE_SIZE = 4_000
"""Sample size for large responses in initial context."""

INITIAL_CONTEXT_MAX_ROWS = 20
"""Max rows sampled from large responses for initial context."""


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------


def _serialize_graphql_subagent_messages(messages: list[BaseMessage]) -> list[dict]:
    """Serialize sub-agent LangChain messages into JSON-safe dicts.

    Captures the full reasoning trace: AI thinking, tool calls, and tool
    responses. The initial HumanMessage (context dump) is excluded to avoid
    bloating the trace.

    Returns:
        List of dicts with keys: role, content, tool_calls (if AI),
        tool_name (if Tool).
    """
    trace: list[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            continue
        if isinstance(msg, AIMessage):
            entry: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {"name": tc["name"], "args": tc["args"]} for tc in msg.tool_calls
                ]
            trace.append(entry)
        elif isinstance(msg, ToolMessage):
            tool_name = getattr(msg, "name", "")
            content = str(msg.content or "")
            if len(content) > 2000:
                content = content[:2000] + f"\n[truncated from {len(content)} chars]"
            trace.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": content,
                }
            )
    return trace


# ---------------------------------------------------------------------------
# Sub-agent state
# ---------------------------------------------------------------------------


class GraphQLSubAgentState(TypedDict):
    """Internal state for the GraphQL correction sub-agent's reasoning loop."""

    # Context (populated before loop starts)
    question: str
    context: str
    original_query_type: str
    original_classification: dict | None
    original_resolved_params: dict | None
    original_query_string: str
    original_response_sample: str
    assessment_verdict: str
    assessment_reasoning: str

    # ReAct conversation
    messages: Annotated[list[BaseMessage], add_messages]

    # Working state (updated by tool nodes)
    current_query_type: str
    current_resolved_params: dict | None
    current_raw_response: dict | None
    current_query_string: str
    execution_time_ms: int
    iteration_count: int
    last_error: str
    used_freeform_final: bool

    # Accumulator
    attempt_history: Annotated[list[dict], operator.add]


# ---------------------------------------------------------------------------
# Tool schemas (for bind_tools)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "execute_graphql_template",
            "description": (
                "Execute a templated GraphQL query with corrected query type and parameters. "
                "This uses the same query builders as the main pipeline. Prefer this over "
                "freeform queries when possible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Brief explanation: what you're trying, why this query type "
                            "and these parameters should produce the correct result."
                        ),
                    },
                    "query_type": {
                        "type": "string",
                        "description": (
                            "The query type to execute (e.g. 'country_year', 'treemap_products')."
                        ),
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Resolved parameters dict. Keys depend on query type: "
                            "country_id, product_id, product_class, product_level, "
                            "year, year_min, year_max, partner_id, group_id, group_type, "
                            "services_class, location (for Country Pages), etc."
                        ),
                    },
                },
                "required": ["reasoning", "query_type", "params"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_graphql_freeform",
            "description": (
                "Execute a raw GraphQL query string for exploration or probing. "
                "Use this when the template system doesn't support what you need, "
                "or for targeted introspection of specific fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Why you're running this freeform query.",
                    },
                    "query": {
                        "type": "string",
                        "description": "The raw GraphQL query string.",
                    },
                    "variables": {
                        "type": ["object", "null"],
                        "description": "Optional query variables.",
                    },
                    "api_target": {
                        "type": "string",
                        "enum": ["explore", "country_pages"],
                        "description": "Which API endpoint to query.",
                    },
                },
                "required": ["reasoning", "query", "api_target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_catalog",
            "description": (
                "Look up entities in the catalog caches. Use to re-resolve "
                "country IDs, product IDs, group IDs, or services categories."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "catalog": {
                        "type": "string",
                        "enum": ["country", "product", "group", "services"],
                        "description": "Which catalog to search.",
                    },
                    "search_term": {
                        "type": "string",
                        "description": "Name or code to search for.",
                    },
                    "product_class": {
                        "type": ["string", "null"],
                        "description": "Product classification (HS92, HS12, etc.) for product lookups.",
                    },
                },
                "required": ["catalog", "search_term"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "introspect_schema",
            "description": (
                "Introspect the GraphQL schema to discover types, fields, and arguments. "
                "Use targeted __type queries (provide type_name) rather than full introspection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type_name": {
                        "type": ["string", "null"],
                        "description": (
                            "Name of the GraphQL type to inspect (e.g. 'CountryYear', 'Query'). "
                            "If null, inspects the root Query type."
                        ),
                    },
                    "api_target": {
                        "type": "string",
                        "enum": ["explore", "country_pages"],
                        "description": "Which API endpoint to introspect.",
                    },
                },
                "required": ["api_target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_results",
            "description": (
                "Finish the correction task and report your results. You MUST call this "
                "tool when you are done — it is the only way to complete the task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "assessment": {
                        "type": "string",
                        "description": (
                            "Your assessment of the correction: what was wrong, what you "
                            "fixed, and any remaining caveats."
                        ),
                    },
                    "final_query_type": {
                        "type": ["string", "null"],
                        "description": (
                            "The corrected query type, or null if no template query worked."
                        ),
                    },
                    "final_resolved_params": {
                        "type": ["object", "null"],
                        "description": "The corrected resolved parameters, or null.",
                    },
                    "surface_to_agent": {
                        "type": "boolean",
                        "description": (
                            "Set to true if the parent agent needs to see this assessment "
                            "for caveats — e.g., coverage gaps, partial results."
                        ),
                    },
                    "used_freeform_final": {
                        "type": "boolean",
                        "description": (
                            "True if the final result came from a freeform query "
                            "rather than a template query."
                        ),
                    },
                    "needs_verification": {
                        "type": "boolean",
                        "description": (
                            "Set to true if you haven't verified the results yet."
                        ),
                    },
                },
                "required": [
                    "assessment",
                    "surface_to_agent",
                    "used_freeform_final",
                    "needs_verification",
                ],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool node implementations
# ---------------------------------------------------------------------------


def _find_tool_call(msg: AIMessage, tool_name: str) -> dict:
    """Find the first tool_call with the given name."""
    for tc in msg.tool_calls:
        if tc["name"] == tool_name:
            return tc
    return msg.tool_calls[0]


async def execute_template_tool_node(
    state: GraphQLSubAgentState,
    *,
    graphql_client: AtlasGraphQLClient,
    country_pages_client: AtlasGraphQLClient | None,
) -> dict:
    """Execute a templated GraphQL query with corrected params."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "execute_graphql_template")
    query_type = tool_call["args"].get("query_type", "")
    params = tool_call["args"].get("params", {})

    try:
        query_string, variables = build_graphql_query(query_type, params)
    except ValueError as e:
        return {
            "messages": [
                ToolMessage(
                    content=f"Invalid query type '{query_type}': {e}",
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_template",
                )
            ],
            "last_error": str(e),
            "attempt_history": [
                {"query_type": query_type, "stage": "build_error", "error": str(e)}
            ],
        }

    # Route to correct client
    api_target = _QUERY_TYPE_TO_API.get(query_type, "explore")
    client = (
        country_pages_client
        if (api_target == "country_pages" and country_pages_client is not None)
        else graphql_client
    )

    start = time.monotonic()
    try:
        data = await client.execute(query_string, variables)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        response_str = json.dumps(data, default=str)
        if len(response_str) > FREEFORM_RESPONSE_TRUNCATION_THRESHOLD:
            display = (
                response_str[:FREEFORM_TRUNCATED_RETURN_SIZE]
                + f"\n[Truncated: {len(response_str)} chars total. "
                f"Add filters or request fewer fields.]"
            )
        else:
            display = response_str

        content = f"Query type: {query_type}\nAPI: {api_target}\n\n{display}"

        return {
            "messages": [
                ToolMessage(
                    content=content,
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_template",
                )
            ],
            "current_query_type": query_type,
            "current_resolved_params": params,
            "current_raw_response": data,
            "current_query_string": query_string,
            "execution_time_ms": elapsed_ms,
            "last_error": "",
            "used_freeform_final": False,
            "attempt_history": [
                {"query_type": query_type, "stage": "executed", "error": None}
            ],
        }

    except BudgetExhaustedError as e:
        return {
            "messages": [
                ToolMessage(
                    content=f"GraphQL API budget exhausted: {e}",
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_template",
                )
            ],
            "last_error": str(e),
            "attempt_history": [
                {"query_type": query_type, "stage": "budget_exhausted", "error": str(e)}
            ],
        }
    except Exception as e:
        logger.error("Template execution failed: %s", e)
        return {
            "messages": [
                ToolMessage(
                    content=f"Error executing query: {e}",
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_template",
                )
            ],
            "last_error": str(e),
            "attempt_history": [
                {"query_type": query_type, "stage": "execution_error", "error": str(e)}
            ],
        }


async def execute_freeform_tool_node(
    state: GraphQLSubAgentState,
    *,
    graphql_client: AtlasGraphQLClient,
    country_pages_client: AtlasGraphQLClient | None,
) -> dict:
    """Execute a raw GraphQL query for exploration."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "execute_graphql_freeform")
    query = tool_call["args"].get("query", "")
    variables = tool_call["args"].get("variables")
    api_target = tool_call["args"].get("api_target", "explore")

    client = (
        country_pages_client
        if (api_target == "country_pages" and country_pages_client is not None)
        else graphql_client
    )

    start = time.monotonic()
    try:
        data = await client.execute(query, variables)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        response_str = json.dumps(data, default=str)
        if len(response_str) > FREEFORM_RESPONSE_TRUNCATION_THRESHOLD:
            display = (
                response_str[:FREEFORM_TRUNCATED_RETURN_SIZE]
                + f"\n[Truncated: {len(response_str)} chars total. "
                f"Add filters or request fewer fields.]"
            )
        else:
            display = response_str

        return {
            "messages": [
                ToolMessage(
                    content=display,
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_freeform",
                )
            ],
            "current_query_string": query,
            "current_raw_response": data,
            "execution_time_ms": elapsed_ms,
            "last_error": "",
            "used_freeform_final": True,
        }

    except BudgetExhaustedError as e:
        return {
            "messages": [
                ToolMessage(
                    content=f"GraphQL API budget exhausted: {e}",
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_freeform",
                )
            ],
            "last_error": str(e),
        }
    except Exception as e:
        logger.error("Freeform execution failed: %s", e)
        return {
            "messages": [
                ToolMessage(
                    content=f"Error executing freeform query: {e}",
                    tool_call_id=tool_call["id"],
                    name="execute_graphql_freeform",
                )
            ],
            "last_error": str(e),
        }


async def explore_catalog_node(
    state: GraphQLSubAgentState,
    *,
    country_cache: CatalogCache | None,
    product_caches: dict[str, CatalogCache],
    services_cache: CatalogCache | None,
    group_cache: CatalogCache | None,
) -> dict:
    """Look up entities in catalog caches."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "explore_catalog")
    catalog = tool_call["args"].get("catalog", "country")
    search_term = tool_call["args"].get("search_term", "")
    product_class = tool_call["args"].get("product_class")

    results: list[dict[str, Any]] = []
    cache: CatalogCache | None = None

    if catalog == "country":
        cache = country_cache
    elif catalog == "product":
        cache = product_caches.get(product_class or "HS92")
        if cache is None and product_caches:
            cache = next(iter(product_caches.values()))
    elif catalog == "services":
        cache = services_cache
    elif catalog == "group":
        cache = group_cache

    if cache is None:
        return {
            "messages": [
                ToolMessage(
                    content=f"No cache available for catalog '{catalog}'.",
                    tool_call_id=tool_call["id"],
                    name="explore_catalog",
                )
            ],
        }

    try:
        # Try exact code lookup first
        entry = await cache.lookup("code", search_term)
        if entry:
            results = [entry]
        else:
            # Fall back to name search
            results = await cache.search("nameShortEn", search_term, limit=20)
    except (KeyError, RuntimeError):
        # Index might not exist — try name search only
        try:
            results = await cache.search("nameShortEn", search_term, limit=20)
        except Exception as e:
            return {
                "messages": [
                    ToolMessage(
                        content=f"Error searching catalog: {e}",
                        tool_call_id=tool_call["id"],
                        name="explore_catalog",
                    )
                ],
            }

    if not results:
        content = f"No matches found for '{search_term}' in {catalog} catalog."
    else:
        lines = []
        for r in results:
            name = r.get("nameShortEn", r.get("nameEn", "unknown"))
            code = r.get("code", r.get("iso3Code", "N/A"))
            entry_id = r.get("id", "N/A")
            lines.append(f"- {name} (code: {code}, id: {entry_id})")
        content = f"Found {len(results)} match(es) in {catalog} catalog:\n" + "\n".join(
            lines
        )

    return {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call["id"],
                name="explore_catalog",
            )
        ],
    }


async def introspect_schema_node(
    state: GraphQLSubAgentState,
    *,
    graphql_client: AtlasGraphQLClient,
    country_pages_client: AtlasGraphQLClient | None,
) -> dict:
    """Introspect a GraphQL schema type."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "introspect_schema")
    type_name = tool_call["args"].get("type_name") or "Query"
    api_target = tool_call["args"].get("api_target", "explore")

    client = (
        country_pages_client
        if (api_target == "country_pages" and country_pages_client is not None)
        else graphql_client
    )

    if type_name == "Query":
        query = """
        {
          __type(name: "Query") {
            fields {
              name
              args { name type { name kind ofType { name } } }
            }
          }
        }
        """
    else:
        query = f"""
        {{
          __type(name: "{type_name}") {{
            fields {{
              name
              type {{ name kind ofType {{ name }} }}
            }}
          }}
        }}
        """

    try:
        data = await client.execute(query)
        content = json.dumps(data, indent=2, default=str)
        if len(content) > 4000:
            content = content[:4000] + "\n[truncated]"
    except Exception as e:
        content = f"Error introspecting schema: {e}"

    return {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call["id"],
                name="introspect_schema",
            )
        ],
    }


async def report_results_node(state: GraphQLSubAgentState) -> dict:
    """Handle the report_results tool call.

    If needs_verification is True, bounce back to reasoning.
    Otherwise this is a terminal node.
    """
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "report_results")
    assessment = tool_call["args"].get("assessment", "")
    needs_verification = tool_call["args"].get("needs_verification", False)

    if needs_verification:
        return {
            "messages": [
                ToolMessage(
                    content=(
                        "You indicated verification is needed. Run a quick probe "
                        "to verify your result, then call report_results again "
                        "with needs_verification=false."
                    ),
                    tool_call_id=tool_call["id"],
                    name="report_results",
                )
            ],
        }

    return {
        "messages": [
            ToolMessage(
                content=f"Results reported. Assessment: {assessment}",
                tool_call_id=tool_call["id"],
                name="report_results",
            )
        ],
    }


# ---------------------------------------------------------------------------
# Reasoning node
# ---------------------------------------------------------------------------


async def reasoning_node(
    state: GraphQLSubAgentState,
    *,
    llm: BaseLanguageModel,
) -> dict:
    """Sub-agent LLM: diagnoses problems and decides on tools."""
    iteration = state.get("iteration_count", 0)
    if iteration >= MAX_GRAPHQL_CORRECTION_ITERATIONS:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"Reached maximum correction attempts ({MAX_GRAPHQL_CORRECTION_ITERATIONS}). "
                        "Reporting best available result."
                    )
                )
            ],
        }

    system_prompt = GRAPHQL_SUBAGENT_PROMPT.format(
        max_year=GRAPHQL_DATA_MAX_YEAR,
    )

    model = llm.bind_tools(TOOL_SCHEMAS, parallel_tool_calls=False, tool_choice="any")
    response = await model.ainvoke(
        [SystemMessage(content=system_prompt)] + state["messages"]
    )

    return {
        "messages": [response],
        "iteration_count": iteration + 1,
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_reasoning(state: GraphQLSubAgentState) -> str:
    """Dispatch to tool or end based on the last AI message."""
    last_msg = state["messages"][-1]
    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        return END

    tool_name = last_msg.tool_calls[0]["name"]
    dispatch = {
        "execute_graphql_template": "execute_graphql_template",
        "execute_graphql_freeform": "execute_graphql_freeform",
        "explore_catalog": "explore_catalog",
        "introspect_schema": "introspect_schema",
        "report_results": "report_results",
    }
    return dispatch.get(tool_name, END)


def route_after_report(state: GraphQLSubAgentState) -> str:
    """After report_results: continue if verification needed, else END."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["name"] == "report_results":
                    if tc["args"].get("needs_verification", False):
                        return "reasoning"
                    return END
    return END


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graphql_subagent(
    *,
    llm: BaseLanguageModel,
    graphql_client: AtlasGraphQLClient,
    country_pages_client: AtlasGraphQLClient | None,
    country_cache: CatalogCache | None,
    product_caches: dict[str, CatalogCache],
    services_cache: CatalogCache | None,
    group_cache: CatalogCache | None,
) -> CompiledStateGraph:
    """Build the GraphQL correction sub-agent subgraph.

    Args:
        llm: Frontier model for correction reasoning.
        graphql_client: HTTP client for the Atlas Explore API.
        country_pages_client: Optional HTTP client for the Country Pages API.
        country_cache: Country catalog cache.
        product_caches: Product catalog caches keyed by classification.
        services_cache: Services catalog cache.
        group_cache: Group catalog cache.

    Returns:
        A compiled LangGraph StateGraph.
    """
    from functools import partial

    builder = StateGraph(GraphQLSubAgentState)

    builder.add_node(
        "reasoning",
        partial(reasoning_node, llm=llm),
    )
    builder.add_node(
        "execute_graphql_template",
        partial(
            execute_template_tool_node,
            graphql_client=graphql_client,
            country_pages_client=country_pages_client,
        ),
    )
    builder.add_node(
        "execute_graphql_freeform",
        partial(
            execute_freeform_tool_node,
            graphql_client=graphql_client,
            country_pages_client=country_pages_client,
        ),
    )
    builder.add_node(
        "explore_catalog",
        partial(
            explore_catalog_node,
            country_cache=country_cache,
            product_caches=product_caches,
            services_cache=services_cache,
            group_cache=group_cache,
        ),
    )
    builder.add_node(
        "introspect_schema",
        partial(
            introspect_schema_node,
            graphql_client=graphql_client,
            country_pages_client=country_pages_client,
        ),
    )
    builder.add_node("report_results", report_results_node)

    builder.add_edge(START, "reasoning")
    builder.add_conditional_edges(
        "reasoning",
        route_after_reasoning,
        {
            "execute_graphql_template": "execute_graphql_template",
            "execute_graphql_freeform": "execute_graphql_freeform",
            "explore_catalog": "explore_catalog",
            "introspect_schema": "introspect_schema",
            "report_results": "report_results",
            END: END,
        },
    )
    builder.add_edge("execute_graphql_template", "reasoning")
    builder.add_edge("execute_graphql_freeform", "reasoning")
    builder.add_edge("explore_catalog", "reasoning")
    builder.add_edge("introspect_schema", "reasoning")
    builder.add_conditional_edges(
        "report_results",
        route_after_report,
        {"reasoning": "reasoning", END: END},
    )

    return builder.compile()


# ---------------------------------------------------------------------------
# Initial message builder
# ---------------------------------------------------------------------------


def _build_initial_message(
    *,
    question: str,
    context: str,
    original_query_type: str,
    original_classification: dict | None,
    original_resolved_params: dict | None,
    original_query_string: str,
    original_raw_response: dict | None,
    assessment_reasoning: str,
) -> HumanMessage:
    """Build the initial HumanMessage with all context about the failed attempt."""
    parts = [
        f"The GraphQL pipeline result was flagged as incorrect.\n\n"
        f"**User's question:** {question}"
    ]

    if context:
        parts.append(f"\n**Conversational context:** {context}")

    parts.append(f"\n**Assessment:** {assessment_reasoning}")
    parts.append(f"\n**Original query type:** {original_query_type}")

    if original_classification:
        parts.append(
            f"\n**Full classification:** {json.dumps(original_classification, default=str)}"
        )

    if original_resolved_params:
        parts.append(
            f"\n**Resolved params:** {json.dumps(original_resolved_params, default=str)}"
        )

    if original_query_string:
        parts.append(f"\n**Query string:**\n```graphql\n{original_query_string}\n```")

    if original_raw_response is not None:
        response_str = json.dumps(original_raw_response, default=str)
        if len(response_str) <= INITIAL_CONTEXT_RESPONSE_THRESHOLD:
            parts.append(f"\n**Raw response:**\n{response_str}")
        else:
            sample = response_str[:INITIAL_CONTEXT_SAMPLE_SIZE]
            parts.append(
                f"\n**Raw response (sample — {len(response_str):,} chars total):**\n{sample}"
            )
    else:
        parts.append("\n**Raw response:** None (no response received)")

    parts.append(
        "\n\nDiagnose the problem and use tools to produce the correct result. "
        "Call report_results when done."
    )

    return HumanMessage(content="\n".join(parts))


# ---------------------------------------------------------------------------
# Parent graph wrapper node
# ---------------------------------------------------------------------------


async def graphql_correction_agent_node(
    state: AtlasAgentState,
    *,
    subagent: CompiledStateGraph,
) -> dict:
    """Invoke the GraphQL correction sub-agent and map results back to parent state.

    CRITICAL: Does NOT add messages to parent state — format_graphql_results
    needs the original AIMessage for tool_call_id.
    """
    async with node_timer("graphql_correction_agent", "atlas_graphql") as t:
        assessment = state.get("graphql_assessment", "")
        parts = assessment.split("|", 2)
        verdict = parts[0] if parts else ""
        failure_type = parts[1] if len(parts) > 1 else ""
        reasoning = parts[2] if len(parts) > 2 else ""

        classification = state.get("graphql_classification") or {}

        initial_msg = _build_initial_message(
            question=state.get("graphql_question", ""),
            context=state.get("graphql_context", ""),
            original_query_type=classification.get("query_type", ""),
            original_classification=classification,
            original_resolved_params=state.get("graphql_resolved_params"),
            original_query_string=state.get("graphql_query") or "",
            original_raw_response=state.get("graphql_raw_response"),
            assessment_reasoning=f"{verdict}: {failure_type} — {reasoning}",
        )

        sub_input: dict[str, Any] = {
            "question": state.get("graphql_question", ""),
            "context": state.get("graphql_context", ""),
            "original_query_type": classification.get("query_type", ""),
            "original_classification": classification,
            "original_resolved_params": state.get("graphql_resolved_params"),
            "original_query_string": state.get("graphql_query") or "",
            "original_response_sample": "",
            "assessment_verdict": verdict,
            "assessment_reasoning": reasoning,
            "messages": [initial_msg],
            "current_query_type": classification.get("query_type", ""),
            "current_resolved_params": state.get("graphql_resolved_params"),
            "current_raw_response": state.get("graphql_raw_response"),
            "current_query_string": state.get("graphql_query") or "",
            "execution_time_ms": 0,
            "iteration_count": 0,
            "last_error": "",
            "used_freeform_final": False,
            "attempt_history": [],
        }

        llm_start = time.monotonic()
        result = await subagent.ainvoke(
            sub_input,
            config={"recursion_limit": 50},
        )
        t.mark_llm(llm_start, time.monotonic())

    # Collect token usage from all AI messages in the sub-agent trace
    token_records: list[dict] = []
    sub_messages = result.get("messages", [])
    for msg in sub_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "usage_metadata", None):
            token_records.append(
                make_usage_record_from_msg(
                    "graphql_correction_agent", "atlas_graphql", msg
                )
            )

    # Serialize reasoning trace
    reasoning_trace = _serialize_graphql_subagent_messages(sub_messages)

    # Extract report_results args from the final tool call
    final_assessment = ""
    surface_to_agent = False
    final_query_type: str | None = None
    final_resolved_params: dict | None = None
    used_freeform_final = False

    for msg in reversed(sub_messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["name"] == "report_results":
                    final_assessment = tc["args"].get("assessment", "")
                    surface_to_agent = tc["args"].get("surface_to_agent", False)
                    final_query_type = tc["args"].get("final_query_type")
                    final_resolved_params = tc["args"].get("final_resolved_params")
                    used_freeform_final = tc["args"].get("used_freeform_final", False)
                    break
            if final_assessment:
                break

    # Build return dict — map corrections back to parent state
    result_dict: dict[str, Any] = {
        "graphql_assessment": final_assessment or assessment,
        "graphql_surface_to_agent": surface_to_agent,
        "graphql_reasoning_trace": [reasoning_trace],
        "token_usage": token_records,
        "step_timing": [t.record],
    }

    if not used_freeform_final and final_query_type:
        # Template-based correction: update classification and params
        classification_update = dict(classification)
        classification_update["query_type"] = final_query_type
        result_dict["graphql_classification"] = classification_update
        if final_resolved_params is not None:
            result_dict["graphql_resolved_params"] = final_resolved_params
        # Use the sub-agent's latest raw response
        if result.get("current_raw_response") is not None:
            result_dict["graphql_raw_response"] = result["current_raw_response"]
        if result.get("current_query_string"):
            result_dict["graphql_query"] = result["current_query_string"]
    elif used_freeform_final:
        # Freeform correction: store raw response directly
        if result.get("current_raw_response") is not None:
            result_dict["graphql_raw_response"] = result["current_raw_response"]
        if result.get("current_query_string"):
            result_dict["graphql_query"] = result["current_query_string"]

    return result_dict
