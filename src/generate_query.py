from typing import Dict, List, Union
import asyncio
import json
import logging
from functools import partial
from pathlib import Path

from langchain_core.language_models import BaseLanguageModel
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import FewShotPromptTemplate, PromptTemplate
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from src.error_handling import QueryExecutionError, async_execute_with_retry, execute_with_retry
from src.product_and_schema_lookup import (
    ProductAndSchemaLookup,
    format_product_codes_for_prompt,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.sql_validation import extract_table_names_from_ddl, validate_sql
from src.state import AtlasAgentState

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
) -> Runnable:
    """
    Creates a chain that generates SQL queries based on the user's question.

    Args:
        llm: The language model to use for query generation
        codes: Reference string of product codes
        top_k: Maximum rows to return per query
        table_info: Information about database tables
        example_queries: List of example SQL queries for reference

    Returns:
        A chain that generates SQL queries
    """
    prefix = """
You are a SQL expert that writes queries for a postgres database containing international trade data. Your task is to create a syntactically correct SQL query to answer the user's question about trade data.

Notes on these tables:
- Unless otherwise specified, do not return more than {top_k} rows.
- If a time period is not specified, assume the query is about the latest available year in the database.
- Never use the `location_level` or `partner_level` columns in your query. Just ignore those columns.
- `product_id` and `product_code` are **NOT** the same thing. `product_id` is an internal ID used by the db, but when looking up specific product codes, use `product_code`, which contains the actual official product codes. Similarly, `country_id` and `iso3_code` are **NOT** the same thing, and if you need to look up specific countries, use `iso3_code`. Use the `product_id` and `country_id` variables for joins, but not for looking up official codes in `WHERE` clauses.
- What this means concretely is that the query should never have a `WHERE` clause that filters on `product_id` or `country_id`. Use `product_code` and `iso3_code` instead in `WHERE` clauses.

Technical metrics:
- There are some technical metrics pre-calculated and stored in the database: RCA, diversity, ubiquity, proximity, distance, ECI, PCI, COI, COG. Use these values directly if needed and do not try to compute them yourself.
- There are some metrics that are not pre-calculated but are calculable from the data in the database:
  * Market Share: A country's exports of a product as a percentage of total global exports of that product in the same year.  Calculated as: (Country's exports of product X) / (Total global exports of product X) * 100%.
  * New Products: A product is considered "new" to a country in a given year if the country had an RCA <1 for that product in the previous year and an RCA >=1 in the current year.

Only use the tables and columns provided. Here is the relevant table information:
{table_info}

Now, analyze the question and plan your query:
1. Identify the main elements of the question:
   - Countries involved (if any)
   - Products or product categories specified (if any)
   - Time period specified (if any)
   - Specific metrics requested (e.g., export value, import value, PCI)

2. Determine the required product classifications and the digit-level(s) of the product codes:
   - Look for specific HS codes mentioned and determine the digit level accordingly (e.g., 1201 is a 4-digit code, 120110 is a 6-digit code)
   - If multiple levels are mentioned, plan to use multiple subqueries or UNION ALL to combine results from different tables.

3. Identify whether the query requires goods data, services data, or both
   - If the question is about trade in goods, only use the goods tables
   - If the question is about trade in services, only use the services tables
   - If the question is about both goods and services, use both the goods and services tables

4. Plan the query:
   - Select appropriate tables based on classification level (e.g., country_product_year_4 for 4-digit HS codes)
   - Plan necessary joins (e.g., with classification tables)
   - List out specific tables and columns needed for the query
   - Identify any calcualtions or aggregations that need to be performed
   - Identify any specific conditions or filters that need to be applied

5. Ensure the query will adhere to the rules and guidelines mentioned earlier:
   - Check that the query doesn't violate any of the given rules
   - Plan any necessary adjustments to comply with the guidelines

Based on your analysis, generate a SQL query that answers the user's question. Just return the SQL query, nothing else.

Ensure you use the correct table suffixes (_1, _2, _4, _6) based on the identified classification levels.

Below are some examples of user questions and their corresponding SQL queries.
"""

    if codes:
        prefix += f"""
Product codes for reference:
{codes}
Always use these product codes provided, and do not try to search for products based on their names from the database."""

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


@tool("query_tool", args_schema=QueryToolInput)
def _query_tool_schema(question: str) -> str:
    """A tool that generates and executes SQL queries on the trade database.
    Input should be a natural language question about trade data."""
    raise NotImplementedError("Schema-only tool; execution routes through graph nodes.")


# ---------------------------------------------------------------------------
# Table / schema helpers
# ---------------------------------------------------------------------------


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
    table_descriptions = get_tables_in_schemas(
        table_descriptions=table_descriptions,
        classification_schemas=classification_schemas,
    )
    # Temporarily, remove any tables that have the word "group" in the table name
    table_descriptions = [
        table
        for table in table_descriptions
        if "group" not in table["table_name"].lower()
    ]
    table_info = ""
    for table in table_descriptions:
        table_info += (
            f"Table: {table['table_name']}\nDescription: {table['context_str']}\n"
        )
        table_info += db.get_table_info(table_names=[table["table_name"]])
        table_info += "\n\n"
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
    return {"pipeline_question": question}


async def extract_products_node(
    state: AtlasAgentState, *, llm: BaseLanguageModel, engine: Engine
) -> dict:
    """Run product/schema extraction LLM chain."""
    lookup = ProductAndSchemaLookup(llm=llm, connection=engine)
    products = await lookup.aextract_schemas_and_product_mentions_direct(
        state["pipeline_question"]
    )
    return {"pipeline_products": products}


async def lookup_codes_node(
    state: AtlasAgentState,
    *,
    llm: BaseLanguageModel,
    engine: Engine,
    async_engine: AsyncEngine | None = None,
) -> dict:
    """Get candidate codes from DB and select final codes via LLM."""
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
    codes = await lookup.aselect_final_codes_direct(
        state["pipeline_question"], candidates
    )
    return {"pipeline_codes": format_product_codes_for_prompt(codes)}


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
    codes = state.get("pipeline_codes") or None
    chain = create_query_generation_chain(
        llm=llm,
        codes=codes,
        top_k=max_results,
        table_info=state.get("pipeline_table_info", ""),
        example_queries=example_queries,
    )
    sql = await chain.ainvoke({"question": state["pipeline_question"]})
    return {"pipeline_sql": sql}


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
    """
    sql = state["pipeline_sql"]
    use_async = isinstance(async_engine, AsyncEngine)

    if use_async:
        async def _run_query() -> str:
            async with async_engine.connect() as conn:
                result = await conn.execute(text(sql))
                if not result.returns_rows:
                    return ""
                columns = list(result.keys())
                rows = result.fetchall()
                if not rows:
                    return ""
                return "\n".join(str(dict(zip(columns, row))) for row in rows)

        try:
            result_str = await async_execute_with_retry(_run_query)
        except QueryExecutionError as e:
            logger.error("Query execution failed: %s", e)
            return {"pipeline_result": "", "last_error": str(e)}
        except Exception as e:
            logger.error("Unexpected error executing SQL: %s", e)
            return {"pipeline_result": "", "last_error": str(e)}
    else:
        # Sync fallback (for tests or when async_engine is a sync Engine)
        engine = async_engine

        def _run_query_sync() -> str:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                if not result.returns_rows:
                    return ""
                columns = list(result.keys())
                rows = result.fetchall()
                if not rows:
                    return ""
                return "\n".join(str(dict(zip(columns, row))) for row in rows)

        try:
            result_str = await asyncio.to_thread(execute_with_retry, _run_query_sync)
        except QueryExecutionError as e:
            logger.error("Query execution failed: %s", e)
            return {"pipeline_result": "", "last_error": str(e)}
        except Exception as e:
            logger.error("Unexpected error executing SQL: %s", e)
            return {"pipeline_result": "", "last_error": str(e)}

    if not result_str or not result_str.strip():
        result_str = "SQL query returned no results."
    return {"pipeline_result": result_str, "last_error": ""}


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
        ToolMessage(content=content, tool_call_id=tool_calls[0]["id"])
    ]
    for tc in tool_calls[1:]:
        messages.append(
            ToolMessage(
                content="Only one query can be executed at a time. Please make additional queries sequentially.",
                tool_call_id=tc["id"],
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
        ToolMessage(content=error_content, tool_call_id=tc["id"])
        for tc in last_msg.tool_calls
    ]
    return {"messages": messages}


# ---------------------------------------------------------------------------
# Set of pipeline node names (used by streaming code in text_to_sql.py)
# ---------------------------------------------------------------------------


PIPELINE_NODES = frozenset({
    "extract_tool_question",
    "extract_products",
    "lookup_codes",
    "get_table_info",
    "generate_sql",
    "validate_sql",
    "execute_sql",
    "format_results",
    "max_queries_exceeded",
})


# ---------------------------------------------------------------------------
# Agent + graph construction
# ---------------------------------------------------------------------------


def create_sql_agent(
    llm: BaseLanguageModel,
    db: SQLDatabaseWithSchemas,
    engine: Engine,
    table_descriptions: Dict,
    example_queries: List[Dict[str, str]] = [],
    top_k_per_query: int = 15,
    max_uses: int = 3,
    checkpointer: BaseCheckpointSaver | None = None,
    async_engine: AsyncEngine | None = None,
):
    """
    Creates a StateGraph agent for handling complex SQL queries.

    The outer agent loop uses *llm* to decide when to query the database.
    When the LLM produces a ``tool_calls`` entry for ``query_tool``, control
    flows through an explicit pipeline of nodes that extract products, look up
    codes, generate SQL, execute it, and return the results as a ToolMessage.

    Args:
        llm: Language model for the agent
        db: Database connection in SQLDatabaseWithSchemas format
        engine: SQLAlchemy engine
        table_descriptions: Dictionary of table descriptions keyed by schema
        example_queries: List of example question/query pairs
        top_k_per_query: Maximum rows to return per query
        max_uses: Maximum number of queries the agent can execute
        checkpointer: Optional checkpoint saver for conversation persistence.
            Falls back to MemorySaver when not provided.
    """
    # Create the system message
    AGENT_PREFIX = f"""You are Ask-Atlas - an expert agent designed to answer complex questions about international trade data using a postgres database of international trade data (including both goods and services trade). You have access to a tool that can generate and execute SQL queries on the database given a natural language question.

**Your Primary Goal and Workflow:**

Your primary goal is to provide accurate and comprehensive answers to user questions by following these steps:
1. Understand the user's question about international trade and formulate a plan for answering the question
2. For simple questions:
    - Just send the user's question to the tool and answer the question based on the results
3. For complex questions:
    - Formulate a plan for answering the question by breaking it down into smaller, manageable sub-questions. Explain how these sub-questions will help answer the main question.
    - Use the tool to answer each sub-question one at a time.
    - After each tool run, analyze the results and determine if you need additional queries to answer the question.

**Initial checks:**
- Safety check: Ensure that the user's question is not harmful or inappropriate.
- Verify that the user's question is about international trade data.
- If either check fails, politely refuse to answer the question.

**Understanding the Data:**

The data you are using is derived from the UN COMTRADE database, and has been further cleaned and enhanced by the Growth Lab at Harvard University to improve data quality. This cleaning process leverages the fact that trade is reported by both importing and exporting countries. Discrepancies are resolved, and estimates are used to fill gaps and correct for biases.

**Limitations:**

- Data Imperfections: International trade data, even after cleaning, can contain imperfections. Be aware of potential issues like re-exports, valuation discrepancies, and reporting lags. The data represents the best available estimates, but it's not perfect.
- Hallucinations: As a language model, you may sometimes generate plausible-sounding but incorrect answers (hallucinate). If you are unsure about an answer, express this uncertainty to the user.
- Services trade data is available but is not as granular as goods trade data.

**Technical Metrics:**

You should be aware of the following key metrics related to economic complexity theory that are pre-calculated and available in the database.:

- Revealed comparative advantage (RCA): The degree to which a country effectively exports a product. Defined at country-product-year level. If RCA >= 1, then the country is said to effectively export the product.
- Diversity: The number of types of products a country is able to export competitively. It acts as a measure of the amount of collective know-how held within that country. Defined at country-year level. This is a technical metric that has to be queried from the database, and cannot just be inferred from the product names.
- Ubiquity: Ubiquity measures the number of countries that are able to make a product competitively. Defined at product-year level.
- Product Proximity: Measures the minimum conditional probability that a country exports product A given that it exports product B, or vice versa. Given that a country makes one product, proximity captures the ease of obtaining the know-how needed to move into another product. Defined at product-product-year level.
- Distance: A measure of a location's ability to enter a specific product. A product's distance (from 0 to 1) looks to capture the extent of a location's existing capabilities to make the product as measured by how closely related a product is to its current export structure. A 'nearby' product of a shorter distance requires related capabilities to those that are existing, with greater likelihood of success. Defined at country-product-year level.
- Economic Complexity Index (ECI): A measure of countries based on how diversified and complex their export basket is. Countries that are home to a great diversity of productive know-how, particularly complex specialized know-how, are able to produce a great diversity of sophisticated products. Defined at country-year level.
- Product Complexity Index (PCI): A measure of the diversity and sophistication of the productive know-how required to produce a product. PCI is calculated based on how many other countries can produce the product and the economic complexity of those countries. In effect, PCI captures the amount and sophistication of know-how required to produce a product. Defined at product-year level.
- Complexity Outlook Index (COI): A measure of how many complex products are near a country's current set of productive capabilities. The COI captures the ease of diversification for a country, where a high COI reflects an abundance of nearby complex products that rely on similar capabilities or know-how as that present in current production. Complexity outlook captures the connectedness of an economy's existing capabilities to drive easy (or hard) diversification into related complex production, using the Product Space. Defined at country-year level.
- Complexity Outlook Gain (COG): Measures how much a location could benefit in opening future diversification opportunities by developing a particular product. Complexity outlook gain quantifies how a new product can open up links to more, and more complex, products. Complexity outlook gain classifies the strategic value of a product based on the new paths to diversification in more complex sectors that it opens up. Defined at country-product-year level.

Calculable metrics (not pre-calculated in the database):

- Market Share: A country's exports of a product as a percentage of total global exports of that product in the same year.  Calculated as: (Country's exports of product X) / (Total global exports of product X) * 100%.
- New Products: A product is considered "new" to a country in a given year if the country had an RCA <1 for that product in the previous year and an RCA >=1 in the current year.
- Product space: A visualization of all product-product proximities. A country's position on the product space is determined by what sectors it is competitive in. This is difficult to calculate correctly, so if the user asks about a country's position on the product space, just say it is out of scope for this tool.

**Using Metrics for Policy Questions:**

If a user asks a normative policy question, such as what products a country should focus on or diversify into, first make sure to tell the user that these broad questions are out of scope for you because they involve normative judgments about what is best for a country. However, you can still use these concepts to make factual observations about diversification strategies.
- Products that have low "distance" values for a country are products that are relatively close to the country's current capabilities. In theory, these are products that should be easier for a country to diversify into.
- Products that have high Product Complexity Index (PCI) are products that are complex to produce. These are attractive products for a country to produce because they bring a lot of sophistication to the country's export basket. However, these products are also more difficult to produce.
- Products that have high Complexity Outlook Gain (COG) are the products that would bring the biggest increase to a country's Economic Complexity if they were to be produced, by bringing the country's capabilities close to products that have high PCI.
- Usually, diversification is a balance between attractiveness (PCI and COG) and feasibility (distance).


**Important Rules:**

- You can use the SQL generation and execution tool up to {max_uses} times to answer a single user question
- Try to keep your uses of the tool to a minimum, and try to answer the user question in simple steps
- If you realize that you will need to run more than {max_uses} queries to answer a single user question, respond to the user saying that the question would need more steps than allowed to answer, so ask the user to ask a simpler question. Suggest that they split their question into multiple short questions.
- Each query will return at most {top_k_per_query} rows, so plan accordingly
- Remember to be precise and efficient with your queries. Don't query for information you don't need.
- If the SQL tool returns an error, warning, or returns an empty result, inform the user about this and explain that the answer might be affected.
- If you are uncertain about the answer due to data limitations or complexity, explicitly state your uncertainty to the user.
- Your responses should be to the point and precise. Don't say any more than you need to.


**Response Formatting:**

- Note that export and import values returned by the DB (if any) are in current USD. When interpreting the SQL results, convert large dollar amounts (if any) to easily readable formats. Use millions, billions, etc. as appropriate.
- Instead of just listing out the DB results, try to interpret the results in a way that answers the user's question directly.
- When responding to the user, your responses should be in markdown format, capable of rendering mathjax. Escape dollar signs properly to avoid rendering errors (e.g., `\\$`).
"""

    system_prompt = SystemMessage(content=AGENT_PREFIX)

    async def agent_node(state: AtlasAgentState) -> dict:
        model_with_tools = llm.bind_tools([_query_tool_schema])
        response = await model_with_tools.ainvoke(
            [system_prompt] + state["messages"]
        )
        return {"messages": [response]}

    def route_after_agent(state: AtlasAgentState) -> str:
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            if state.get("queries_executed", 0) >= max_uses:
                return "max_queries_exceeded"
            return "extract_tool_question"
        return END

    # Build the graph
    builder = StateGraph(AtlasAgentState)

    # Agent node
    builder.add_node("agent", agent_node)

    # Pipeline nodes (bound with dependencies via functools.partial)
    builder.add_node("extract_tool_question", extract_tool_question)
    builder.add_node(
        "extract_products",
        partial(extract_products_node, llm=llm, engine=engine),
    )
    _lookup_kwargs = {"llm": llm, "engine": engine}
    if async_engine is not None:
        _lookup_kwargs["async_engine"] = async_engine
    builder.add_node(
        "lookup_codes",
        partial(lookup_codes_node, **_lookup_kwargs),
    )
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
        partial(execute_sql_node, async_engine=async_engine if async_engine is not None else engine),
    )
    builder.add_node("format_results", format_results_node)
    builder.add_node("max_queries_exceeded", max_queries_exceeded_node)

    # Routing after SQL validation
    def route_after_validation(state: AtlasAgentState) -> str:
        if state.get("last_error"):
            return "format_results"
        return "execute_sql"

    # Edges
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("extract_tool_question", "extract_products")
    builder.add_edge("extract_products", "lookup_codes")
    builder.add_edge("lookup_codes", "get_table_info")
    builder.add_edge("get_table_info", "generate_sql")
    builder.add_edge("generate_sql", "validate_sql")
    builder.add_conditional_edges("validate_sql", route_after_validation)
    builder.add_edge("execute_sql", "format_results")
    builder.add_edge("format_results", "agent")
    builder.add_edge("max_queries_exceeded", "agent")

    memory = checkpointer if checkpointer is not None else MemorySaver()
    return builder.compile(checkpointer=memory)
