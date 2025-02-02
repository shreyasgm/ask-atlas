import os
from typing import List, Dict, Union
import json
from pathlib import Path
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate, FewShotPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent
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
    prefix = """You are a SQL expert that writes queries for a postgres database containing trade data.
Given an input question, create a syntactically correct SQL query to answer the user's question. Unless otherwise specified, do not return more than {top_k} rows.

Notes on these tables:
- Never use the location_level or partner_level columns in your query. Just ignore those columns.
- product_id and product_code are NOT the same thing. product_id is an internal ID used by the db, but when looking up specific product codes, use product_code, which contains the actual official product codes. Similarly, country_id and iso3_code are NOT the same thing, and if you need to look up specific countries, use iso3_code. Use the id variables for joins, but not for looking up official codes.
- What this means concretely is that the query should never have a WHERE clause that filters on product_id or country_id. Use product_code and iso3_code instead.


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
    AGENT_PREFIX = f"""You are Ask-Atlas - an agent designed to answer complex questions about international trade data using a postgres database of international trade data. You have access to a tool that can generate and execute SQL queries on the database given a natural language question.

Your primary goal is to provide accurate and comprehensive answers to user questions by following these steps:
1. Understand the user's question about international trade and formulate a plan for answering the question
2. For simple questions:
    - Just send the user's question to the tool and answer the question based on the results
3. For complex questions:
    - Formulate a plan for answering the question by breaking it down into smaller, manageable sub-questions. Explain how these sub-questions will help answer the main question.
    - Use the tool to answer each sub-question one at a time.
    - After each tool run, analyze the results and determine if you need additional queries to answer the question.

Important rules:
- You can use the SQL generation and execution tool up to {max_uses} times to answer a single user question
- Try to keep your uses of the tool to a minimum, and try to answer the user question in simple steps
- If you realize that you will need to run more than {max_uses} queries to answer a single user question, respond to the user saying that the question would need more steps than allowed to answer, so ask the user to ask a simpler question. Suggest that they split their question into multiple short questions.
- Each query will return at most {top_k_per_query} rows, so plan accordingly
- Remember to be precise and efficient with your queries. Don't query for information you don't need.
- Your responses should be to the point and precise. Don't say any more than you need to.

Note that export and import values returned by the DB (if any) are in current USD.  When interpreting the SQL results, convert large dollar amounts (if any) to easily readable formats. Use millions, billions, etc. as appropriate. Also, instead of just listing out the DB results, try to interpret the results in a way that answers the user's question directly.

When responding to the user, your responses should be in markdown format, capable of rendering mathjax. Escape dollar signs properly to avoid rendering errors.
"""

    # Create the agent
    agent = create_react_agent(
        model=llm,
        tools=[query_tool],
        state_modifier=SystemMessage(content=AGENT_PREFIX),
    )

    return agent
