import os
from typing import List, Dict, Union
import json
from pathlib import Path
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import Runnable
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from langchain_community.tools.sql_database.tool import QuerySQLDatabaseTool
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]

QUERIES_JSON_PATH = BASE_DIR / "src/example_queries/queries.json"
EXAMPLE_QUERIES_DIR = BASE_DIR / "src/example_queries"
DB_URI = os.getenv("ATLAS_DB_URL")
QUERY_LLM = os.getenv("QUERY_LLM")


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
You are a SQL expert that writes queries for a postgres database containing international trade data.
Given an input question, create a syntactically correct SQL query to answer the user's question. Unless otherwise specified, do not return more than {top_k} rows. If a time period is not specified, assume the query is about the latest available year in the database.

Notes on these tables:
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

Just return the SQL query, nothing else.

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


class QueryToolInput(BaseModel):
    question: str = Field(description="A question about international trade data")


def create_query_tool(
    llm: BaseLanguageModel,
    db: SQLDatabaseWithSchemas,
    example_queries: List[Dict[str, str]],
    table_info: str,
    codes: str = None,
    top_k_per_query: int = 15,
    max_uses: int = 3,
) -> BaseTool:
    """
    Factory function that creates a QueryTool with all dependencies pre-configured.
    Returns a tool that only requires the question as input.
    """
    execute_query_tool = QuerySQLDatabaseTool(db=db)

    # Create the query generation chain and convert to tool
    query_gen_chain = create_query_generation_chain(
        llm=llm,
        top_k=top_k_per_query,
        table_info=table_info,
        example_queries=example_queries,
        codes=codes,
    )
    query_gen_tool = query_gen_chain.as_tool(
        name="Query generator",
        description="Tool to convert a user question to a SQL query",
    )

    # Track uses in a closure
    uses_counter = {"current": 0}

    @tool("database_query_tool", args_schema=QueryToolInput)
    def query_tool(question: str) -> str:
        """
        A tool that generates and executes SQL queries based on natural language questions.
        Input should be a natural language question about the database.
        The tool will generate an appropriate SQL query and execute it.
        """
        uses_counter["current"] += 1
        if uses_counter["current"] > max_uses:
            return "Error: Maximum number of queries exceeded."

        query = query_gen_tool.invoke({"question": question})
        results = execute_query_tool.invoke({"query": query})

        return results

    return query_tool


def create_sql_agent(
    llm: BaseLanguageModel,
    db: SQLDatabaseWithSchemas,
    codes: str = None,
    example_queries: List[Dict[str, str]] = [],
    table_info: str = "",
    top_k_per_query: int = 15,
    max_uses: int = 3,
):
    """
    Creates a React agent for handling complex SQL queries through multiple steps.

    Args:
        llm: Language model for the agent
        query_chain: Chain for generating SQL queries
        execute_query_tool: Tool for executing SQL queries
        table_info: Information about database tables
        top_k_per_query: Maximum rows to return per query
        max_uses: Maximum number of queries the agent can execute
    """
    # Define the query generation and execution tool
    query_tool = create_query_tool(
        llm=llm,
        db=db,
        codes=codes,
        example_queries=example_queries,
        table_info=table_info,
        top_k_per_query=top_k_per_query,
        max_uses=max_uses,
    )

    # Create the system message
    AGENT_PREFIX = f"""You are Ask-Atlas - an expert agent designed to answer complex questions about international trade data using a postgres database of international trade data. You have access to a tool that can generate and execute SQL queries on the database given a natural language question.

**Your Primary Goal and Workflow:**

Your primary goal is to provide accurate and comprehensive answers to user questions by following these steps:
1. Understand the user's question about international trade and formulate a plan for answering the question
2. For simple questions:
    - Just send the user's question to the tool and answer the question based on the results
3. For complex questions:
    - Formulate a plan for answering the question by breaking it down into smaller, manageable sub-questions. Explain how these sub-questions will help answer the main question.
    - Use the tool to answer each sub-question one at a time.
    - After each tool run, analyze the results and determine if you need additional queries to answer the question.

**Understanding the Data:**

The data you are using is derived from the UN COMTRADE database, which is a comprehensive source of international trade statistics. The data has been further cleaned and enhanced by the Growth Lab at Harvard University to improve data quality. This cleaning process leverages the fact that trade is reported by both importing and exporting countries. Discrepancies are resolved, and estimates are used to fill gaps and correct for biases.

**Limitations:**

- Data Imperfections: International trade data, even after cleaning, can contain imperfections. Be aware of potential issues like re-exports, valuation discrepancies, and reporting lags. The data represents the best available estimates, but it's not perfect.
- Hallucinations: As a language model, you may sometimes generate plausible-sounding but incorrect answers (hallucinate). If you are unsure about an answer, express this uncertainty to the user.

**Technical Metrics:**

You should be aware of the following key metrics related to economic complexity theory that are pre-calculated and available in the database.:

- Revealed comparative advantage (RCA): The degree to which a country effectively exports a product. Defined at country-product-year level. If RCA >= 1, then the country is said to effectively export the product.
- Diversity: A measure of how many different types of products a country is able to make competitively. A country’s total diversity is one way of expressing the amount of collective know-how held within that country. Defined at country-year level.
- Ubiquity: Ubiquity measures the number of countries that are able to make a product competitively. Defined at product-year level.
- Product Proximity: Measures the minimum conditional probability that a country exports product A given that it exports product B, or vice versa. Given that a country makes one product, proximity captures the ease of obtaining the know-how needed to move into another product. Defined at product-product-year level.
- Distance: A measure of a location’s ability to enter a specific product. A product’s distance (from 0 to 1) looks to capture the extent of a location’s existing capabilities to make the product as measured by how closely related a product is to its current export structure. A ‘nearby’ product of a shorter distance requires related capabilities to those that are existing, with greater likelihood of success. Defined at country-product-year level.
- Economic Complexity Index (ECI): A measure of countries based on how diversified and complex their export basket is. Countries that are home to a great diversity of productive know-how, particularly complex specialized know-how, are able to produce a great diversity of sophisticated products. Defined at country-year level.
- Product Complexity Index (PCI): A measure of the diversity and sophistication of the productive know-how required to produce a product. PCI is calculated based on how many other countries can produce the product and the economic complexity of those countries. In effect, PCI captures the amount and sophistication of know-how required to produce a product. Defined at product-year level.
- Complexity Outlook Index (COI): A measure of how many complex products are near a country’s current set of productive capabilities. The COI captures the ease of diversification for a country, where a high COI reflects an abundance of nearby complex products that rely on similar capabilities or know-how as that present in current production. Complexity outlook captures the connectedness of an economy’s existing capabilities to drive easy (or hard) diversification into related complex production, using the Product Space. Defined at country-year level.
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
    # Add chat memory
    memory = MemorySaver()

    # Create the agent
    agent = create_react_agent(
        model=llm,
        tools=[query_tool],
        checkpointer=memory,
        state_modifier=SystemMessage(content=AGENT_PREFIX),
    )

    return agent
