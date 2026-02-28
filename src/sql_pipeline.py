from typing import Dict, List, Tuple, Union
import asyncio
import json
import logging
import time
from pathlib import Path

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import ToolMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import FewShotPromptTemplate, PromptTemplate
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from src.error_handling import (
    QueryExecutionError,
    async_execute_with_retry,
    execute_with_retry,
)
from src.product_and_schema_lookup import (
    SCHEMA_TO_PRODUCTS_TABLE_MAP,
    ProductAndSchemaLookup,
    ProductDetails,
    SchemasAndProductsFound,
    format_product_codes_for_prompt,
)
from src.prompts import build_sql_generation_prefix
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.sql_validation import extract_table_names_from_ddl, validate_sql
from src.state import AtlasAgentState
from src.token_usage import make_usage_record_from_callback

logger = logging.getLogger(__name__)

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]

QUERIES_JSON_PATH = BASE_DIR / "src/example_queries/queries.json"
EXAMPLE_QUERIES_DIR = BASE_DIR / "src/example_queries"


# ---------------------------------------------------------------------------
# File / data loading helpers
# ---------------------------------------------------------------------------


def load_example_queries(
    queries_json: Union[str, Path], directory: Union[str, Path]
) -> List[Dict[str, str]]:
    """
    Loads example SQL queries from files in the specified directory and maps them to their questions.
    Returns a list of dictionaries, each containing a 'question' and its corresponding 'query'.

    Args:
        queries_json: Path to the queries.json file
        directory: Path to the directory containing the example SQL queries
    """
    # Load the queries.json file
    with open(Path(queries_json), "r") as f:
        query_metadata = json.load(f)

    # Create list of question-query pairs
    example_queries = []
    for entry in query_metadata:
        query_path = Path(directory) / entry["file"]
        with open(query_path, "r") as f:
            example_queries.append({"question": entry["question"], "query": f.read()})

    return example_queries


def load_table_descriptions(table_descriptions_json: Union[str, Path]) -> Dict:
    """
    Loads table descriptions from a JSON file.

    Args:
        table_descriptions_json: Path to the table descriptions JSON file

    Returns:
        Dictionary containing table descriptions
    """
    with open(table_descriptions_json, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# SQL generation chain (LCEL kept — prompt | llm | parser)
# ---------------------------------------------------------------------------


def _strip(text: str) -> str:
    return text.strip().replace("```sql", "").replace("```", "")


def create_query_generation_chain(
    llm: BaseLanguageModel,
    codes: str = None,
    top_k: int = 15,
    table_info: str = "",
    example_queries: List[Dict[str, str]] = [],
    direction_constraint: str | None = None,
    mode_constraint: str | None = None,
    context: str = "",
) -> Runnable:
    """
    Creates a chain that generates SQL queries based on the user's question.

    Args:
        llm: The language model to use for query generation
        codes: Reference string of product codes
        top_k: Maximum rows to return per query
        table_info: Information about database tables
        example_queries: List of example SQL queries for reference
        direction_constraint: Optional trade direction constraint (exports/imports)
        mode_constraint: Optional trade mode constraint (goods/services)
        context: Optional technical context (e.g., from docs_tool) to guide SQL generation.

    Returns:
        A chain that generates SQL queries
    """
    prefix = build_sql_generation_prefix(
        codes=codes,
        top_k=top_k,
        table_info=table_info,
        direction_constraint=direction_constraint,
        mode_constraint=mode_constraint,
        context=context,
    )

    example_prompt = PromptTemplate.from_template(
        "User question: {question}\nSQL query: {query}"
    )
    prompt = FewShotPromptTemplate(
        examples=example_queries,
        example_prompt=example_prompt,
        prefix=prefix,
        suffix="User question: {question}\nSQL query: ",
        input_variables=["question", "top_k", "table_info", "codes"],
    )
    if codes:
        prompt = prompt.partial(top_k=top_k, table_info=table_info, codes=codes)
    else:
        prompt = prompt.partial(top_k=top_k, table_info=table_info)

    return prompt | llm | StrOutputParser() | _strip


# ---------------------------------------------------------------------------
# Tool schema (LLM sees this as a callable tool; execution goes through nodes)
# ---------------------------------------------------------------------------


class QueryToolInput(BaseModel):
    question: str = Field(description="A question about international trade data")
    context: str = Field(
        default="",
        description="Additional technical context (e.g., metric definitions, data caveats) "
        "that may help answer the query accurately. Optional.",
    )


@tool("query_tool", args_schema=QueryToolInput)
def _query_tool_schema(question: str, context: str = "") -> str:
    """A tool that generates and executes SQL queries on the trade database.
    Input should be a natural language question about trade data."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")


# ---------------------------------------------------------------------------
# Table / schema helpers
# ---------------------------------------------------------------------------


def _classification_tables_for_schemas(
    classification_schemas: List[str],
    table_descriptions: Dict,
) -> List[Dict]:
    """Return the specific classification lookup tables needed for the given data schemas.

    For each data schema (e.g. hs92), includes:
    - classification.location_country (always — needed for country lookups)
    - The matching product table (e.g. classification.product_hs92)
    """
    tables: List[Dict] = []
    seen: set[str] = set()

    # Always include location_country
    tables.append(
        {
            "table_name": "classification.location_country",
            "context_str": "Country-level data with names, ISO codes, and hierarchical information.",
        }
    )
    seen.add("classification.location_country")

    # Include the matching product classification table for each data schema
    classification_entries = table_descriptions.get("classification", [])
    classification_by_name = {t["table_name"]: t for t in classification_entries}

    for schema in classification_schemas:
        product_table_full = SCHEMA_TO_PRODUCTS_TABLE_MAP.get(
            schema
        )  # e.g. "classification.product_hs92"
        if product_table_full and product_table_full not in seen:
            table_name = product_table_full.split(".", 1)[1]  # "product_hs92"
            entry = classification_by_name.get(table_name)
            if entry:
                tables.append(
                    {
                        "table_name": product_table_full,
                        "context_str": entry["context_str"],
                    }
                )
                seen.add(product_table_full)

    return tables


def get_tables_in_schemas(
    table_descriptions: Dict, classification_schemas: List[str]
) -> List[Dict]:
    """
    Gets all tables and their descriptions for the selected schemas.

    Args:
        classification_schemas: List of classification schema names

    Returns:
        List of dictionaries containing table information with schema-qualified table_name and context_str
    """
    tables = []
    for schema in classification_schemas:
        if schema in table_descriptions:
            for table in table_descriptions[schema]:
                # Create a new dict with schema-qualified table name
                tables.append(
                    {
                        "table_name": f"{schema}.{table['table_name']}",
                        "context_str": table["context_str"],
                    }
                )
    return tables


def get_table_info_for_schemas(
    db: SQLDatabaseWithSchemas,
    table_descriptions: Dict,
    classification_schemas: List[str],
) -> str:
    """Get table information for a list of schemas."""
    from src.cache import registry, table_info_cache, table_info_key

    key = table_info_key(classification_schemas)
    cached = table_info_cache.get(key)
    if cached is not None:
        registry.record_hit("table_info")
        logger.debug("Cache HIT for table_info key=%s", key)
        return cached

    registry.record_miss("table_info")
    logger.debug("Cache MISS for table_info key=%s", key)

    # Get data schema tables (e.g. hs92.country_year, hs92.country_product_year_4, ...)
    tables = get_tables_in_schemas(
        table_descriptions=table_descriptions,
        classification_schemas=classification_schemas,
    )

    # Add the specific classification lookup tables needed for JOINs
    tables.extend(
        _classification_tables_for_schemas(classification_schemas, table_descriptions)
    )

    # Exclude large group data tables but keep classification lookup tables
    # Table names are schema-qualified (e.g. "hs92.group_group_product_year_4"),
    # so we check the part after the schema prefix.
    tables = [table for table in tables if "group_group_" not in table["table_name"]]
    table_info = ""
    for table in tables:
        table_info += (
            f"Table: {table['table_name']}\nDescription: {table['context_str']}\n"
        )
        table_info += db.get_table_info(table_names=[table["table_name"]])
        table_info += "\n\n"

    table_info_cache[key] = table_info
    return table_info


# ---------------------------------------------------------------------------
# Pipeline node functions (each takes AtlasAgentState, returns partial update)
# ---------------------------------------------------------------------------


async def extract_tool_question(state: AtlasAgentState) -> dict:
    """Extract the question from the agent's tool_call args."""
    last_msg = state["messages"][-1]
    if len(last_msg.tool_calls) > 1:
        logger.warning(
            "LLM produced %d parallel tool_calls; only the first will be executed.",
            len(last_msg.tool_calls),
        )
    question = last_msg.tool_calls[0]["args"]["question"]
    context = last_msg.tool_calls[0]["args"].get("context", "")
    return {"pipeline_question": question, "pipeline_context": context}


async def extract_products_node(
    state: AtlasAgentState, *, llm: BaseLanguageModel, engine: Engine
) -> dict:
    """Run product/schema extraction LLM chain, then apply overrides."""
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    usage_handler = UsageMetadataCallbackHandler()
    lookup = ProductAndSchemaLookup(llm=llm, connection=engine)
    products = await lookup.aextract_schemas_and_product_mentions_direct(
        state["pipeline_question"],
        callbacks=[usage_handler],
    )

    override_schema = state.get("override_schema")
    override_mode = state.get("override_mode")

    if override_schema:
        # Schema override: force classification_schemas and rebind products
        products = SchemasAndProductsFound(
            classification_schemas=[override_schema],
            products=[
                ProductDetails(
                    name=p.name,
                    classification_schema=override_schema,
                    codes=p.codes,
                )
                for p in (products.products or [])
            ],
            requires_product_lookup=products.requires_product_lookup,
        )
    elif override_mode:
        # Mode override (only when no schema override): filter schemas
        schemas = products.classification_schemas
        if override_mode == "goods":
            schemas = [s for s in schemas if not s.startswith("services_")]
            if not schemas:
                schemas = ["hs92"]
        elif override_mode == "services":
            schemas = [s for s in schemas if s.startswith("services_")]
            if not schemas:
                schemas = ["services_unilateral"]
        products = SchemasAndProductsFound(
            classification_schemas=schemas,
            products=products.products,
            requires_product_lookup=products.requires_product_lookup,
        )

    usage_record = make_usage_record_from_callback(
        "extract_products", "query_tool", usage_handler
    )
    return {"pipeline_products": products, "token_usage": [usage_record]}


async def lookup_codes_node(
    state: AtlasAgentState,
    *,
    llm: BaseLanguageModel,
    engine: Engine,
    async_engine: AsyncEngine | None = None,
) -> dict:
    """Get candidate codes from DB and select final codes via LLM."""
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    products = state.get("pipeline_products")
    if not products or not products.products:
        return {"pipeline_codes": ""}

    lookup = ProductAndSchemaLookup(
        llm=llm, connection=engine, async_engine=async_engine
    )
    if async_engine is not None:
        candidates = await lookup.aget_candidate_codes(products)
    else:
        candidates = await asyncio.to_thread(lookup.get_candidate_codes, products)
    usage_handler = UsageMetadataCallbackHandler()
    codes = await lookup.aselect_final_codes_direct(
        state["pipeline_question"], candidates, callbacks=[usage_handler]
    )
    usage_record = make_usage_record_from_callback(
        "lookup_codes", "query_tool", usage_handler
    )
    return {
        "pipeline_codes": format_product_codes_for_prompt(codes),
        "token_usage": [usage_record],
    }


async def get_table_info_node(
    state: AtlasAgentState,
    *,
    db: SQLDatabaseWithSchemas,
    table_descriptions: Dict,
) -> dict:
    """Get table info for the identified schemas."""
    products = state.get("pipeline_products")
    schemas = products.classification_schemas if products else []
    info = await asyncio.to_thread(
        get_table_info_for_schemas,
        db=db,
        table_descriptions=table_descriptions,
        classification_schemas=schemas,
    )
    return {"pipeline_table_info": info}


async def generate_sql_node(
    state: AtlasAgentState,
    *,
    llm: BaseLanguageModel,
    example_queries: List[Dict[str, str]],
    max_results: int,
) -> dict:
    """Generate SQL query using LLM."""
    from langchain_core.callbacks import UsageMetadataCallbackHandler

    codes = state.get("pipeline_codes") or None
    chain = create_query_generation_chain(
        llm=llm,
        codes=codes,
        top_k=max_results,
        table_info=state.get("pipeline_table_info", ""),
        example_queries=example_queries,
        direction_constraint=state.get("override_direction"),
        mode_constraint=state.get("override_mode"),
        context=state.get("pipeline_context", ""),
    )
    usage_handler = UsageMetadataCallbackHandler()
    sql = await chain.ainvoke(
        {"question": state["pipeline_question"]},
        config={"callbacks": [usage_handler]},
    )
    usage_record = make_usage_record_from_callback(
        "generate_sql", "query_tool", usage_handler
    )
    return {"pipeline_sql": sql, "token_usage": [usage_record]}


async def validate_sql_node(
    state: AtlasAgentState,
    *,
    table_descriptions: Dict,
) -> dict:
    """Validate generated SQL before execution.

    Extracts valid table names from the DDL in ``pipeline_table_info`` and
    from ``table_descriptions`` + ``pipeline_products.classification_schemas``,
    then runs structural validation checks (syntax, table existence, etc.).

    On validation failure, short-circuits by setting ``last_error`` so that
    the graph routes to ``format_results`` instead of ``execute_sql``.
    """
    sql = state.get("pipeline_sql", "")
    table_info = state.get("pipeline_table_info", "")

    # Build the set of valid table names from two sources:
    # 1. DDL in pipeline_table_info
    valid_tables = extract_table_names_from_ddl(table_info)

    # 2. table_descriptions + classification_schemas from pipeline_products
    products = state.get("pipeline_products")
    if products and hasattr(products, "classification_schemas"):
        for schema in products.classification_schemas:
            if schema in table_descriptions:
                for table in table_descriptions[schema]:
                    valid_tables.add(f"{schema}.{table['table_name']}")

    # 3. Add the specific classification lookup tables needed for JOINs
    schemas = (
        products.classification_schemas
        if (products and hasattr(products, "classification_schemas"))
        else []
    )
    for ct in _classification_tables_for_schemas(schemas, table_descriptions):
        valid_tables.add(ct["table_name"])

    result = validate_sql(sql, valid_tables)

    if not result.is_valid:
        error_msg = "SQL validation failed: " + "; ".join(result.errors)
        logger.warning(error_msg)
        return {"pipeline_sql": sql, "pipeline_result": "", "last_error": error_msg}

    return {"pipeline_sql": result.sql, "last_error": ""}


async def execute_sql_node(
    state: AtlasAgentState, *, async_engine: AsyncEngine | Engine
) -> dict:
    """Execute SQL via async or sync SQLAlchemy engine.

    Uses true async DB I/O when given an AsyncEngine (production path).
    Falls back to asyncio.to_thread with a sync Engine (test/legacy path).

    Returns structured columns/rows alongside the existing string representation
    and query execution timing in milliseconds.
    """
    sql = state["pipeline_sql"]
    use_async = isinstance(async_engine, AsyncEngine)
    _empty_structured = {
        "pipeline_result_columns": [],
        "pipeline_result_rows": [],
        "pipeline_execution_time_ms": 0,
    }

    if use_async:

        async def _run_query() -> Tuple[str, list[str], list[list]]:
            async with async_engine.connect() as conn:
                result = await conn.execute(text(sql))
                if not result.returns_rows:
                    return "", [], []
                columns = list(result.keys())
                rows = result.fetchall()
                rows_as_lists = [list(row) for row in rows]
                if not rows:
                    return "", columns, []
                result_str = "\n".join(str(dict(zip(columns, row))) for row in rows)
                return result_str, columns, rows_as_lists

        try:
            t0 = time.monotonic()
            result_str, columns, rows = await async_execute_with_retry(_run_query)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except QueryExecutionError as e:
            logger.error("Query execution failed: %s", e)
            return {"pipeline_result": "", "last_error": str(e), **_empty_structured}
        except Exception as e:
            logger.error("Unexpected error executing SQL: %s", e)
            return {"pipeline_result": "", "last_error": str(e), **_empty_structured}
    else:
        # Sync fallback (for tests or when async_engine is a sync Engine)
        engine = async_engine

        def _run_query_sync() -> Tuple[str, list[str], list[list]]:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                if not result.returns_rows:
                    return "", [], []
                columns = list(result.keys())
                rows = result.fetchall()
                rows_as_lists = [list(row) for row in rows]
                if not rows:
                    return "", columns, []
                result_str = "\n".join(str(dict(zip(columns, row))) for row in rows)
                return result_str, columns, rows_as_lists

        try:
            t0 = time.monotonic()
            result_str, columns, rows = await asyncio.to_thread(
                execute_with_retry, _run_query_sync
            )
            elapsed_ms = int((time.monotonic() - t0) * 1000)
        except QueryExecutionError as e:
            logger.error("Query execution failed: %s", e)
            return {"pipeline_result": "", "last_error": str(e), **_empty_structured}
        except Exception as e:
            logger.error("Unexpected error executing SQL: %s", e)
            return {"pipeline_result": "", "last_error": str(e), **_empty_structured}

    if not result_str or not result_str.strip():
        result_str = "SQL query returned no results."
    return {
        "pipeline_result": result_str,
        "last_error": "",
        "pipeline_result_columns": columns,
        "pipeline_result_rows": rows,
        "pipeline_execution_time_ms": elapsed_ms,
    }


async def format_results_node(state: AtlasAgentState) -> dict:
    """Create a ToolMessage for every tool_call and route back to agent.

    When the LLM produces multiple parallel tool_calls, only the first is
    actually executed through the pipeline.  The remaining tool_call_ids
    still need a corresponding ToolMessage so that provider APIs (e.g.
    OpenAI) don't reject the message history.
    """
    last_msg = state["messages"][-1]
    tool_calls = last_msg.tool_calls

    if state.get("last_error"):
        content = f"Error executing query: {state['last_error']}"
    else:
        content = state.get("pipeline_result", "SQL query returned no results.")

    messages: list[ToolMessage] = [
        ToolMessage(
            content=content, tool_call_id=tool_calls[0]["id"], name="query_tool"
        )
    ]
    for tc in tool_calls[1:]:
        messages.append(
            ToolMessage(
                content="Only one query can be executed at a time. Please make additional queries sequentially.",
                tool_call_id=tc["id"],
                name="query_tool",
            )
        )

    return {
        "messages": messages,
        "queries_executed": state.get("queries_executed", 0) + 1,
    }


async def max_queries_exceeded_node(state: AtlasAgentState) -> dict:
    """Return a ToolMessage for every tool_call indicating the query limit was hit."""
    last_msg = state["messages"][-1]
    error_content = "Error: Maximum number of queries exceeded."
    messages = [
        ToolMessage(content=error_content, tool_call_id=tc["id"], name=tc["name"])
        for tc in last_msg.tool_calls
    ]
    return {"messages": messages}


# ---------------------------------------------------------------------------
# Set of pipeline node names (used by streaming code in text_to_sql.py)
# ---------------------------------------------------------------------------


PIPELINE_NODES = frozenset(
    {
        "extract_tool_question",
        "extract_products",
        "lookup_codes",
        "get_table_info",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "format_results",
        "max_queries_exceeded",
    }
)
