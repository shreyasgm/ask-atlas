"""Agentic SQL sub-agent: multi-turn ReAct loop for SQL generation and execution.

Replaces the linear generate_sql -> validate_sql -> execute_sql -> retry pipeline
with a frontier LLM sub-agent that writes SQL directly, executes it via tools,
and self-corrects on errors with full DDL context.

The sub-agent has 3 tools:
  - execute_sql: Validate + run SQL against the database
  - explore_schema: Inspect database schemas, tables, columns, sample data
  - lookup_products: Re-extract product codes when initial extraction was wrong
"""

from __future__ import annotations

import asyncio
import logging
import operator
import time
from typing import Annotated

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
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from typing_extensions import TypedDict

from src.error_handling import (
    QueryExecutionError,
    async_execute_with_retry,
    execute_with_retry,
)
from src.product_and_schema_lookup import (
    ProductAndSchemaLookup,
    SchemasAndProductsFound,
    format_product_codes_for_prompt,
)
from src.prompts import SQL_SUBAGENT_PROMPT
from src.prompts._blocks import SQL_DATA_MAX_YEAR
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.sql_pipeline import get_table_info_for_schemas
from src.sql_validation import validate_sql
from src.state import AtlasAgentState
from src.token_usage import make_usage_record_from_msg, node_timer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------


def _serialize_subagent_messages(messages: list[BaseMessage]) -> list[dict]:
    """Serialize sub-agent LangChain messages into JSON-safe dicts.

    Captures the full reasoning trace: AI thinking, tool calls, and tool
    responses. The initial HumanMessage (context dump) is excluded to avoid
    bloating the trace with DDL/schema info already visible elsewhere.

    Returns:
        List of dicts with keys: role, content, tool_calls (if AI),
        tool_call_id/tool_name (if Tool).
    """
    trace: list[dict] = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            # Skip the initial context dump — it's large and redundant
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
            if tool_name == "execute_sql":
                # Summarize — full data is in pipeline_result_rows already
                content = _summarize_execute_sql_result(content)
            elif len(content) > 2000:
                content = content[:2000] + f"\n[truncated from {len(content)} chars]"
            trace.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": content,
                }
            )
    return trace


def _summarize_execute_sql_result(content: str) -> str:
    """Summarize execute_sql tool output for the reasoning trace.

    Keeps only the status line (success/error + row count), strips raw data
    rows and redundant SQL dumps since both are available elsewhere.
    """
    if content.startswith("Success."):
        # Extract just the first line: "Success. N rows returned"
        first_line = content.split("\n", 1)[0]
        return first_line
    if content.startswith("0 rows returned"):
        # Strip column names and hints — they clutter the UI trace.
        # The full message (with hint) still reaches the LLM via ToolMessage.
        return "0 rows returned"
    if content.startswith("Validation error:") or content.startswith(
        "Execution error:"
    ):
        # Strip "SQL attempted:" section — the SQL is in the preceding tool_call
        marker = "\n\nSQL attempted:"
        idx = content.find(marker)
        if idx != -1:
            return content[:idx]
        return content
    # Fallback: return as-is but capped
    if len(content) > 500:
        return content[:500] + "\n[truncated]"
    return content


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 12
"""Maximum reasoning iterations (initial attempt + up to 11 corrections)."""

RESULT_TRUNCATION_THRESHOLD = 50
"""Row count above which results are truncated in the tool response."""

RESULT_DISPLAY_ROWS = 20
"""Number of rows shown when results are truncated."""

MAX_TOOL_OUTPUT_TOKENS = 3000
"""Approximate character limit for explore_schema output."""


# ---------------------------------------------------------------------------
# Sub-agent state
# ---------------------------------------------------------------------------


class SQLSubAgentState(TypedDict):
    """Internal state for the SQL sub-agent's reasoning loop."""

    # Context (populated before loop starts, from deterministic phase)
    question: str
    context: str
    products: SchemasAndProductsFound | None
    codes: str
    table_info: str
    override_direction: str | None
    override_mode: str | None

    # ReAct conversation (sub-agent's internal reasoning trace)
    messages: Annotated[list[BaseMessage], add_messages]

    # Working state (updated by tool nodes)
    sql: str
    result: str
    result_columns: list[str]
    result_rows: list[list]
    execution_time_ms: int
    last_error: str
    iteration_count: int

    # Accumulator
    attempt_history: Annotated[list[dict], operator.add]


# ---------------------------------------------------------------------------
# Tool schemas (for bind_tools)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Validate and execute a SQL query against the Atlas trade database. "
                "Returns query results if successful, or a detailed error message. "
                "Validation (syntax, write-blocking) is automatic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Brief explanation of your approach: what you're querying, "
                            "why you chose this table/join/filter, and what you changed "
                            "if this is a retry after an error."
                        ),
                    },
                    "sql": {
                        "type": "string",
                        "description": "The complete SQL query to validate and execute.",
                    },
                },
                "required": ["reasoning", "sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore_schema",
            "description": (
                "Explore the Atlas database schema. Returns table listings, DDL, "
                "column names, descriptions, or sample data. "
                "Examples: 'List tables in the hs92 schema', "
                "'Show columns in hs92.country_product_year_4', "
                "'What schemas are available?', "
                "'Show 5 sample rows from hs92.country_product_year_4'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language query about the schema.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_products",
            "description": (
                "Re-extract product codes and classification schemas from the question. "
                "Use when results are empty and you suspect wrong product codes, "
                "the wrong classification schema was identified, or you need services "
                "tables but only have goods tables. This tool is expensive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": (
                            "What to do differently. E.g. 'Try SITC classification', "
                            "'Look for electronic chips, not food', "
                            "'Include services schemas'."
                        ),
                    }
                },
                "required": ["instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_results",
            "description": (
                "Finish the SQL task and report your results. You MUST call this "
                "tool when you are done — it is the only way to complete the task. "
                "Before calling, review your results for correctness. Use "
                "surface_to_agent to flag caveats that the parent agent needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "assessment": {
                        "type": "string",
                        "description": (
                            "Your assessment of the results: what was queried, whether "
                            "the results look correct, any caveats or data limitations "
                            "(e.g. stale year, missing services data)."
                        ),
                    },
                    "needs_verification": {
                        "type": "boolean",
                        "description": (
                            "Set to true if you haven't verified the results yet and "
                            "they warrant a check (e.g. aggregate values, year "
                            "freshness, goods-vs-services completeness). Set to false "
                            "for simple lookups with unambiguous results."
                        ),
                    },
                    "surface_to_agent": {
                        "type": "boolean",
                        "description": (
                            "Set to true if the parent agent needs to see this "
                            "assessment to make a good decision — e.g., caveats about "
                            "missing data categories, wrong product codes that were "
                            "corrected, stale year data, or partial results. Set to "
                            "false for clean, straightforward results."
                        ),
                        "default": False,
                    },
                },
                "required": ["assessment", "needs_verification", "surface_to_agent"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool node implementations
# ---------------------------------------------------------------------------


async def execute_sql_tool_node(
    state: SQLSubAgentState,
    *,
    async_engine: AsyncEngine | Engine,
) -> dict:
    """Validate and execute the SQL from the last tool_call."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "execute_sql")
    sql = tool_call["args"]["sql"]

    # Validate first
    validation = validate_sql(sql)
    if not validation.is_valid:
        error_msg = "SQL validation failed: " + "; ".join(validation.errors)
        return {
            "messages": [
                ToolMessage(
                    content=f"Validation error:\n{error_msg}\n\nSQL attempted:\n{sql}",
                    tool_call_id=tool_call["id"],
                    name="execute_sql",
                )
            ],
            "last_error": error_msg,
            "attempt_history": [
                {"sql": sql, "stage": "validation_error", "errors": validation.errors}
            ],
        }

    # Execute
    use_async = isinstance(async_engine, AsyncEngine)
    try:
        if use_async:

            async def _run_query() -> tuple[str, list[str], list[list]]:
                async with async_engine.connect() as conn:
                    result = await conn.execute(text(sql))
                    if not result.returns_rows:
                        return "", [], []
                    columns = list(result.keys())
                    rows = result.fetchall()
                    rows_as_lists = [list(row) for row in rows]
                    if not rows:
                        return "", columns, []
                    result_str = _format_result_rows(columns, rows_as_lists)
                    return result_str, columns, rows_as_lists

            t0 = time.monotonic()
            result_str, columns, rows = await async_execute_with_retry(_run_query)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        else:
            engine = async_engine

            def _run_query_sync() -> tuple[str, list[str], list[list]]:
                with engine.connect() as conn:
                    result = conn.execute(text(sql))
                    if not result.returns_rows:
                        return "", [], []
                    columns = list(result.keys())
                    rows = result.fetchall()
                    rows_as_lists = [list(row) for row in rows]
                    if not rows:
                        return "", columns, []
                    result_str = _format_result_rows(columns, rows_as_lists)
                    return result_str, columns, rows_as_lists

            t0 = time.monotonic()
            result_str, columns, rows = await asyncio.to_thread(
                execute_with_retry, _run_query_sync
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Record query metrics for observability
        from src.db_pool_health import metrics as pool_metrics

        engine_type = "async" if use_async else "sync"
        pool_metrics.record_query(elapsed_ms, sql[:200], engine_type=engine_type)
        logger.debug(
            "%s query  elapsed=%dms  rows=%d  sql=%s",
            engine_type,
            elapsed_ms,
            len(rows),
            sql[:200],
        )

    except (QueryExecutionError, Exception) as e:
        logger.error("SQL execution failed in sub-agent: %s", e)
        error_str = str(e)
        return {
            "messages": [
                ToolMessage(
                    content=f"Execution error:\n{error_str}\n\nSQL attempted:\n{sql}",
                    tool_call_id=tool_call["id"],
                    name="execute_sql",
                )
            ],
            "last_error": error_str,
            "attempt_history": [
                {"sql": sql, "stage": "execution_error", "errors": [error_str]}
            ],
        }

    # Format result for the agent
    row_count = len(rows)
    if row_count == 0:
        display = (
            f"0 rows returned. Columns: {', '.join(columns) if columns else 'none'}\n\n"
            "Hint: Check product codes, table suffix (_1/_2/_4/_6), time period, "
            "or classification schema."
        )
    elif row_count > RESULT_TRUNCATION_THRESHOLD:
        truncated_str = _format_result_rows(columns, rows[:RESULT_DISPLAY_ROWS])
        display = (
            f"Success. {row_count} rows returned (showing first {RESULT_DISPLAY_ROWS}):\n\n"
            f"{truncated_str}\n\n"
            f"... ({row_count - RESULT_DISPLAY_ROWS} more rows, {row_count} total)"
        )
    else:
        display = f"Success. {row_count} rows returned:\n\n{result_str}"

    return {
        "messages": [
            ToolMessage(
                content=display,
                tool_call_id=tool_call["id"],
                name="execute_sql",
            )
        ],
        "sql": sql,
        "result": result_str,
        "result_columns": columns,
        "result_rows": rows,
        "execution_time_ms": elapsed_ms,
        "last_error": "",
        "attempt_history": [{"sql": sql, "stage": "executed", "errors": None}],
    }


async def explore_schema_node(
    state: SQLSubAgentState,
    *,
    db,
    engine: Engine,
    async_engine: AsyncEngine | None = None,
) -> dict:
    """Handle explore_schema tool calls by querying the database schema.

    Uses async helpers when an async_engine is provided; falls back to
    asyncio.to_thread with sync engine otherwise.
    """
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "explore_schema")
    query = tool_call["args"]["query"].lower()

    try:
        if async_engine is not None:
            result = await _explore_schema_async(query, db, async_engine)
        else:
            result = await asyncio.to_thread(_explore_schema_sync, query, db, engine)
    except Exception as e:
        result = f"Error exploring schema: {e}"

    # Cap output size
    if len(result) > MAX_TOOL_OUTPUT_TOKENS * 4:  # rough char estimate
        result = result[: MAX_TOOL_OUTPUT_TOKENS * 4] + "\n\n[output truncated]"

    return {
        "messages": [
            ToolMessage(
                content=result,
                tool_call_id=tool_call["id"],
                name="explore_schema",
            )
        ],
    }


def _explore_schema_sync(query: str, db: SQLDatabaseWithSchemas, engine: Engine) -> str:
    """Synchronous schema exploration — runs in a thread."""
    # Detect intent from the query
    if "sample" in query or "example rows" in query or "sample rows" in query:
        # Extract table name
        table_name = _extract_table_name(query)
        if table_name:
            return _get_sample_rows(table_name, engine, limit=5)
        return "Could not determine which table to sample. Please specify a table name."

    if (
        "what schemas" in query
        or "available schemas" in query
        or "list schemas" in query
    ):
        return _list_schemas(engine)

    if "list tables" in query or "tables in" in query:
        schema_name = _extract_schema_name(query)
        if schema_name:
            return _list_tables_in_schema(schema_name, engine)
        return "Could not determine which schema. Available: hs92, hs12, hs22, sitc, services_unilateral, services_bilateral, classification."

    if "columns" in query or "show columns" in query or "ddl" in query:
        table_name = _extract_table_name(query)
        if table_name:
            try:
                return db.get_table_info(table_names=[table_name])
            except Exception as e:
                return f"Error getting DDL for {table_name}: {e}"
        return "Could not determine which table. Please specify a schema-qualified table name (e.g. hs92.country_year)."

    # Default: try to get DDL for any table name found in the query
    table_name = _extract_table_name(query)
    if table_name:
        try:
            return db.get_table_info(table_names=[table_name])
        except Exception as e:
            return f"Error: {e}"

    return (
        "Available schemas: hs92, hs12, hs22, sitc, services_unilateral, services_bilateral, classification.\n"
        "Try: 'List tables in hs92', 'Show columns in hs92.country_year', "
        "'Show 5 sample rows from hs92.country_product_year_4'"
    )


async def _explore_schema_async(query: str, db, async_engine: AsyncEngine) -> str:
    """Async schema exploration — uses async engine directly, no threads."""
    if "sample" in query or "example rows" in query or "sample rows" in query:
        table_name = _extract_table_name(query)
        if table_name:
            return await _aget_sample_rows(table_name, async_engine, limit=5)
        return "Could not determine which table to sample. Please specify a table name."

    if (
        "what schemas" in query
        or "available schemas" in query
        or "list schemas" in query
    ):
        return _list_schemas(None)

    if "list tables" in query or "tables in" in query:
        schema_name = _extract_schema_name(query)
        if schema_name:
            return await _alist_tables_in_schema(schema_name, async_engine)
        return "Could not determine which schema. Available: hs92, hs12, hs22, sitc, services_unilateral, services_bilateral, classification."

    if "columns" in query or "show columns" in query or "ddl" in query:
        table_name = _extract_table_name(query)
        if table_name:
            try:
                return await db.aget_table_info(table_names=[table_name])
            except Exception as e:
                return f"Error getting DDL for {table_name}: {e}"
        return "Could not determine which table. Please specify a schema-qualified table name (e.g. hs92.country_year)."

    # Default: try to get DDL for any table name found in the query
    table_name = _extract_table_name(query)
    if table_name:
        try:
            return await db.aget_table_info(table_names=[table_name])
        except Exception as e:
            return f"Error: {e}"

    return (
        "Available schemas: hs92, hs12, hs22, sitc, services_unilateral, services_bilateral, classification.\n"
        "Try: 'List tables in hs92', 'Show columns in hs92.country_year', "
        "'Show 5 sample rows from hs92.country_product_year_4'"
    )


def _extract_table_name(query: str) -> str | None:
    """Extract a schema-qualified table name from a natural language query."""
    import re

    # Match patterns like "schema.table_name"
    match = re.search(
        r"\b(hs92|hs12|hs22|sitc|services_unilateral|services_bilateral|classification)\."
        r"([a-z_0-9]+)\b",
        query,
    )
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return None


def _extract_schema_name(query: str) -> str | None:
    """Extract a schema name from a natural language query."""
    schemas = [
        "hs92",
        "hs12",
        "hs22",
        "sitc",
        "services_unilateral",
        "services_bilateral",
        "classification",
    ]
    for schema in schemas:
        if schema in query:
            return schema
    return None


def _list_schemas(engine: Engine) -> str:
    """List available schemas with year coverage info."""
    return (
        "Available schemas:\n"
        "- hs92: Goods (HS 1992 classification), data from 1995\n"
        "- hs12: Goods (HS 2012 classification), data from 2012\n"
        "- hs22: Goods (HS 2022 classification), data from 2022\n"
        "- sitc: Goods (SITC classification), data from 1962\n"
        "- services_unilateral: Services (single country), data from 1980\n"
        "- services_bilateral: Services (bilateral) — CURRENTLY EMPTY (no data)\n"
        "- classification: Lookup tables (countries, products, groups)"
    )


def _list_tables_in_schema(schema_name: str, engine: Engine) -> str:
    """List tables in a specific schema."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema ORDER BY table_name"
            ),
            {"schema": schema_name},
        )
        tables = [row[0] for row in result]
    if not tables:
        return f"No tables found in schema '{schema_name}'."
    return f"Tables in {schema_name}:\n" + "\n".join(
        f"- {schema_name}.{t}" for t in tables
    )


async def _alist_tables_in_schema(schema_name: str, async_engine: AsyncEngine) -> str:
    """Async version: list tables in a specific schema."""
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :schema ORDER BY table_name"
            ),
            {"schema": schema_name},
        )
        tables = [row[0] for row in result]
    if not tables:
        return f"No tables found in schema '{schema_name}'."
    return f"Tables in {schema_name}:\n" + "\n".join(
        f"- {schema_name}.{t}" for t in tables
    )


def _get_sample_rows(table_name: str, engine: Engine, limit: int = 5) -> str:
    """Get sample rows from a table."""
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT * FROM {table_name} LIMIT :limit"), {"limit": limit}
        )
        columns = list(result.keys())
        rows = result.fetchall()
    if not rows:
        return f"No rows in {table_name}."
    header = " | ".join(columns)
    separator = "-|-".join("-" * len(c) for c in columns)
    data_rows = "\n".join(" | ".join(str(v) for v in row) for row in rows)
    return f"Sample rows from {table_name} ({len(rows)} rows):\n{header}\n{separator}\n{data_rows}"


async def _aget_sample_rows(
    table_name: str, async_engine: AsyncEngine, limit: int = 5
) -> str:
    """Async version: get sample rows from a table."""
    async with async_engine.connect() as conn:
        result = await conn.execute(
            text(f"SELECT * FROM {table_name} LIMIT :limit"), {"limit": limit}
        )
        columns = list(result.keys())
        rows = result.fetchall()
    if not rows:
        return f"No rows in {table_name}."
    header = " | ".join(columns)
    separator = "-|-".join("-" * len(c) for c in columns)
    data_rows = "\n".join(" | ".join(str(v) for v in row) for row in rows)
    return f"Sample rows from {table_name} ({len(rows)} rows):\n{header}\n{separator}\n{data_rows}"


async def lookup_products_node(
    state: SQLSubAgentState,
    *,
    lightweight_llm: BaseLanguageModel,
    engine: Engine,
    db,
    table_descriptions: dict,
    async_engine: AsyncEngine | None = None,
    async_db=None,
) -> dict:
    """Re-extract product codes with a guidance instruction."""
    last_msg = state["messages"][-1]
    tool_call = _find_tool_call(last_msg, "lookup_products")
    instruction = tool_call["args"]["instruction"]

    try:
        # Prepend instruction to the question for the extraction LLM
        augmented_question = (
            f"[Instruction: {instruction}] Original question: {state['question']}"
        )

        lookup = ProductAndSchemaLookup(
            llm=lightweight_llm, connection=engine, async_engine=async_engine
        )

        # Re-extract
        products = await lookup.aextract_schemas_and_product_mentions_direct(
            augmented_question
        )

        # Get candidate codes and select final
        if products.products:
            if async_engine is not None:
                candidates = await lookup.aget_candidate_codes(products)
            else:
                candidates = await asyncio.to_thread(
                    lookup.get_candidate_codes, products
                )
            codes_mapping = await lookup.aselect_final_codes_direct(
                state["question"], candidates
            )
            new_codes = format_product_codes_for_prompt(codes_mapping)
        else:
            new_codes = ""

        # Get updated DDL for any newly identified schemas
        if async_db is not None:
            from src.sql_pipeline import aget_table_info_for_schemas

            new_table_info = await aget_table_info_for_schemas(
                db=async_db,
                table_descriptions=table_descriptions,
                classification_schemas=products.classification_schemas,
                requires_group_tables=getattr(products, "requires_group_tables", False),
            )
        else:
            new_table_info = await asyncio.to_thread(
                get_table_info_for_schemas,
                db=db,
                table_descriptions=table_descriptions,
                classification_schemas=products.classification_schemas,
                requires_group_tables=getattr(products, "requires_group_tables", False),
            )

        response_parts = [
            f"Re-extracted products with instruction: {instruction}",
            f"Schemas identified: {', '.join(products.classification_schemas)}",
        ]
        if new_codes:
            response_parts.append(f"Product codes:\n{new_codes}")
        else:
            response_parts.append("No specific product codes identified.")
        response_parts.append(f"\nUpdated table DDL:\n{new_table_info}")

        return {
            "messages": [
                ToolMessage(
                    content="\n".join(response_parts),
                    tool_call_id=tool_call["id"],
                    name="lookup_products",
                )
            ],
            "products": products,
            "codes": new_codes,
            "table_info": new_table_info,
        }
    except Exception as e:
        logger.error("lookup_products failed: %s", e)
        return {
            "messages": [
                ToolMessage(
                    content=f"Error re-extracting products: {e}",
                    tool_call_id=tool_call["id"],
                    name="lookup_products",
                )
            ],
        }


# ---------------------------------------------------------------------------
# Report results node
# ---------------------------------------------------------------------------


async def report_results_node(state: SQLSubAgentState) -> dict:
    """Handle the report_results tool call.

    If needs_verification is True, bounce back to reasoning by returning a
    ToolMessage that prompts the LLM to run verification queries. Otherwise
    this is a terminal node — the graph routes to END after it.
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
                        "You indicated verification is needed. Run lightweight "
                        "verification queries now (e.g. SELECT MAX(year), "
                        "magnitude checks, product code verification). "
                        "Call report_results again when satisfied."
                    ),
                    tool_call_id=tool_call["id"],
                    name="report_results",
                )
            ],
        }

    # Terminal: store the assessment as a ToolMessage so the trace is complete
    return {
        "messages": [
            ToolMessage(
                content=f"Results reported. Assessment: {assessment}",
                tool_call_id=tool_call["id"],
                name="report_results",
            )
        ],
    }


def route_after_report(state: SQLSubAgentState) -> str:
    """After report_results: continue if verification needed, else END."""
    # Find the report_results tool call in the AI message before the ToolMessage
    for msg in reversed(state["messages"]):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["name"] == "report_results":
                    if tc["args"].get("needs_verification", False):
                        return "reasoning"
                    return END
    return END


# ---------------------------------------------------------------------------
# Reasoning node
# ---------------------------------------------------------------------------


async def reasoning_node(
    state: SQLSubAgentState,
    *,
    llm: BaseLanguageModel,
) -> dict:
    """Sub-agent LLM: generates SQL and decides on tools."""
    iteration = state.get("iteration_count", 0)
    if iteration >= MAX_ITERATIONS:
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"Reached maximum attempts ({MAX_ITERATIONS}). "
                        "Unable to produce a successful query."
                    )
                )
            ],
        }

    system_prompt = SQL_SUBAGENT_PROMPT.format(
        top_k=state.get("_top_k", 15),
        sql_max_year=SQL_DATA_MAX_YEAR,
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


def route_after_reasoning(state: SQLSubAgentState) -> str:
    """Dispatch to tool or end based on the last AI message."""
    last_msg = state["messages"][-1]
    if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
        # With tool_choice="any" this shouldn't happen, but handle gracefully
        return END

    tool_name = last_msg.tool_calls[0]["name"]
    if tool_name == "execute_sql":
        return "execute_sql"
    elif tool_name == "explore_schema":
        return "explore_schema"
    elif tool_name == "lookup_products":
        return "lookup_products"
    elif tool_name == "report_results":
        return "report_results"
    return END


# ---------------------------------------------------------------------------
# Sub-agent graph builder
# ---------------------------------------------------------------------------


def build_sql_subagent(
    *,
    llm: BaseLanguageModel,
    lightweight_llm: BaseLanguageModel,
    db,
    engine: Engine,
    table_descriptions: dict,
    async_engine: AsyncEngine | Engine | None = None,
    async_db=None,
    top_k: int = 15,
):
    """Build the SQL sub-agent subgraph.

    Args:
        llm: Frontier model for SQL reasoning.
        lightweight_llm: Lightweight model for product re-extraction.
        db: Multi-schema database instance (for DDL).
        engine: Sync SQLAlchemy engine.
        table_descriptions: Table descriptions dict.
        async_engine: Async or sync engine for SQL execution.
        async_db: Optional AsyncSQLDatabaseWithSchemas for async DDL.
        top_k: Default row limit.

    Returns:
        A compiled LangGraph StateGraph.
    """
    from functools import partial

    _exec_engine = async_engine if async_engine is not None else engine
    _real_async_engine = async_engine if isinstance(async_engine, AsyncEngine) else None

    builder = StateGraph(SQLSubAgentState)

    builder.add_node(
        "reasoning",
        partial(reasoning_node, llm=llm),
    )
    builder.add_node(
        "execute_sql",
        partial(execute_sql_tool_node, async_engine=_exec_engine),
    )
    # Use async_db for DDL if available; use async_engine for sample rows/table listing
    _explore_db = async_db if async_db is not None else db
    builder.add_node(
        "explore_schema",
        partial(
            explore_schema_node,
            db=_explore_db,
            engine=engine,
            async_engine=_real_async_engine,
        ),
    )
    builder.add_node(
        "lookup_products",
        partial(
            lookup_products_node,
            lightweight_llm=lightweight_llm,
            engine=engine,
            db=db,
            table_descriptions=table_descriptions,
            async_engine=_real_async_engine,
            async_db=async_db,
        ),
    )

    builder.add_node("report_results", report_results_node)

    builder.add_edge(START, "reasoning")
    builder.add_conditional_edges(
        "reasoning",
        route_after_reasoning,
        {
            "execute_sql": "execute_sql",
            "explore_schema": "explore_schema",
            "lookup_products": "lookup_products",
            "report_results": "report_results",
            END: END,
        },
    )
    builder.add_edge("execute_sql", "reasoning")
    builder.add_edge("explore_schema", "reasoning")
    builder.add_edge("lookup_products", "reasoning")
    builder.add_conditional_edges(
        "report_results",
        route_after_report,
        {"reasoning": "reasoning", END: END},
    )

    return builder.compile(), top_k


# ---------------------------------------------------------------------------
# Initial context message builder
# ---------------------------------------------------------------------------


def _build_initial_message(
    *,
    question: str,
    context: str,
    codes: str,
    table_info: str,
    override_direction: str | None,
    override_mode: str | None,
    example_queries: list[dict[str, str]],
) -> HumanMessage:
    """Build the initial HumanMessage with all per-query context."""
    parts = [f"Answer this question by writing a SQL query:\n\n{question}"]

    if context:
        parts.append(f"\nTechnical context:\n{context}")

    if codes:
        parts.append(f"\nProduct codes identified:\n{codes}")
    else:
        parts.append("\nNo specific product codes identified.")

    overrides = []
    if override_direction:
        overrides.append(
            f"Trade direction override: **{override_direction}** only. "
            f"Use {override_direction} data columns."
        )
    if override_mode:
        overrides.append(
            f"Trade mode override: **{override_mode}** only. "
            f"Use only {override_mode} tables."
        )
    if overrides:
        parts.append("\nActive overrides:\n" + "\n".join(overrides))

    parts.append(
        f"\nTable schemas (DDL) — these are the tables available to query:\n{table_info}"
    )

    if example_queries:
        examples_text = "\n\n".join(
            f"User question: {ex['question']}\nSQL query: {ex['query']}"
            for ex in example_queries
        )
        parts.append(f"\nReference examples (question -> SQL):\n{examples_text}")

    parts.append(
        "\nWrite a SQL query to answer the question, then call execute_sql to run it."
    )

    return HumanMessage(content="\n".join(parts))


# ---------------------------------------------------------------------------
# Parent graph wrapper node
# ---------------------------------------------------------------------------


async def sql_query_agent_node(
    state: AtlasAgentState,
    *,
    subagent,
    top_k: int,
    example_queries: list[dict[str, str]],
) -> dict:
    """Invoke the SQL sub-agent and map results back to parent state."""
    async with node_timer("sql_query_agent", "query_tool") as t:
        initial_msg = _build_initial_message(
            question=state["pipeline_question"],
            context=state.get("pipeline_context", ""),
            codes=state.get("pipeline_codes", ""),
            table_info=state.get("pipeline_table_info", ""),
            override_direction=state.get("override_direction"),
            override_mode=state.get("override_mode"),
            example_queries=example_queries,
        )

        sub_input = {
            "question": state["pipeline_question"],
            "context": state.get("pipeline_context", ""),
            "products": state.get("pipeline_products"),
            "codes": state.get("pipeline_codes", ""),
            "table_info": state.get("pipeline_table_info", ""),
            "override_direction": state.get("override_direction"),
            "override_mode": state.get("override_mode"),
            "messages": [initial_msg],
            "sql": "",
            "result": "",
            "result_columns": [],
            "result_rows": [],
            "execution_time_ms": 0,
            "last_error": "",
            "iteration_count": 0,
            "attempt_history": [],
            "_top_k": top_k,
        }

        llm_start = time.monotonic()
        result = await subagent.ainvoke(
            sub_input,
            config={"recursion_limit": 50},
        )
        t.mark_llm(llm_start, time.monotonic())

    # Collect token usage from all AI messages in the sub-agent trace
    token_records = []
    sub_messages = result.get("messages", [])
    for msg in sub_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "usage_metadata", None):
            token_records.append(
                make_usage_record_from_msg("sql_query_agent", "query_tool", msg)
            )

    # Serialize the sub-agent's full reasoning trace (AI + Tool messages)
    reasoning_trace = _serialize_subagent_messages(sub_messages)

    # Extract assessment from the final report_results tool call
    assessment = ""
    surface_to_agent = False
    for msg in reversed(sub_messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc["name"] == "report_results":
                    assessment = tc["args"].get("assessment", "")
                    surface_to_agent = tc["args"].get("surface_to_agent", False)
                    break
            if assessment:
                break

    return {
        "pipeline_sql": result.get("sql", ""),
        "pipeline_result": result.get("result", ""),
        "pipeline_result_columns": result.get("result_columns", []),
        "pipeline_result_rows": result.get("result_rows", []),
        "pipeline_execution_time_ms": result.get("execution_time_ms", 0),
        "last_error": result.get("last_error", ""),
        "retry_count": 0,
        "pipeline_sql_history": result.get("attempt_history", []),
        "pipeline_reasoning_trace": [reasoning_trace],
        "pipeline_assessment": assessment,
        "pipeline_surface_to_agent": surface_to_agent,
        "token_usage": token_records,
        "step_timing": [t.record],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_tool_call(msg: AIMessage, tool_name: str) -> dict:
    """Find the first tool_call with the given name."""
    for tc in msg.tool_calls:
        if tc["name"] == tool_name:
            return tc
    # Fallback: return first tool_call
    return msg.tool_calls[0]


def _format_result_rows(columns: list[str], rows: list[list]) -> str:
    """Format result rows as a readable string with column headers."""
    if not rows:
        return ""
    header = " | ".join(str(c) for c in columns)
    data = "\n".join(" | ".join(str(v) for v in row) for row in rows)
    return f"{header}\n{data}"
