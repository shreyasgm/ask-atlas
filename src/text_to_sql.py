import os
from typing import Dict, List
from pathlib import Path
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import logging
import sys
import json
from sqlalchemy import create_engine
from langchain.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain_core.output_parsers import StrOutputParser

from src.select_schema_and_tables import (
    create_schema_selection_chain,
    get_tables_in_schemas,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.generate_query import load_example_queries, create_query_generation_chain

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]
print(f"BASE_DIR: {BASE_DIR}")

# Set up logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

# Load environment variables
load_dotenv(BASE_DIR / ".env")


class AtlasTextToSQL:
    def __init__(
        self,
        db_uri: str,
        table_descriptions_json: str = "db_table_descriptions.json",
        table_structure_json: str = "db_table_structure.json",
        queries_json: str = "queries.json",
        example_queries_dir: str = "example_queries",
        max_results: int = 15,
    ):
        """
        Initialize the Atlas Text-to-SQL system.

        Args:
            db_uri: Database connection URI
            table_descriptions_json: Path to JSON file containing names of the tables and their descriptions
            table_structure_json: Path to JSON file containing table structure
            queries_json: Path to JSON file containing example queries
            example_queries_dir: Directory containing example SQL queries
            max_results: Maximum number of results to return from SELECT queries on the database
        """
        # Initialize engine
        engine = create_engine(
            db_uri,
            execution_options={"postgresql_readonly": True},
            connect_args={"connect_timeout": 10},
        )

        # Initialize database connection
        self.db = SQLDatabaseWithSchemas(engine=engine)

        # Load schema and structure information
        self.table_descriptions = self._load_json_as_dict(table_descriptions_json)
        self.table_structure = self._load_json_as_dict(table_structure_json)
        self.example_queries = load_example_queries(queries_json, example_queries_dir)

        # Initialize language models
        self.schema_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.query_llm = ChatOpenAI(model="gpt-4o", temperature=0)

        self.max_results = max_results

    @staticmethod
    def _load_json_as_dict(file_path: str) -> Dict:
        """Loads a JSON file as a dictionary."""
        with open(file_path, "r") as f:
            return json.load(f)

    def get_table_info_for_schemas(self, schemas: List[str]) -> str:
        """Get table information for a list of schemas."""
        table_descriptions = get_tables_in_schemas(schemas, self.table_descriptions)
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
            table_info += self.db.get_table_info(table_names=[table["table_name"]])
            table_info += "\n\n"
        return table_info

    def answer_question(self, question: str) -> str:
        """
        Process a user's question and return the answer.

        Args:
            question: The user's question about the trade data

        Returns:
            The answer to the user's question
        """
        # Select relevant schemas
        table_info_chain = (
            create_schema_selection_chain(self.schema_llm)
            | self.get_table_info_for_schemas
        )

        # Create query generation chain with selected tables
        query_chain = create_query_generation_chain(
            llm=self.query_llm,
            example_queries=self.example_queries,
        )

        # Get query results
        execute_query = QuerySQLDataBaseTool(db=self.db)

        # Ensure that the query resulted in at least a few results - results (stripped) should not be empty
        def check_results(results: str) -> str:
            if results.strip() == "":
                return "SQL query returned no results."
            return results

        execute_query_chain = execute_query | check_results

        # Answer question given the query and results
        answer_prompt = PromptTemplate.from_template(
            """Given the following user question, corresponding SQL query, and SQL result, answer the user question. When interpreting the SQL results, convert large dollar amounts (if any) to easily readable formats. Use millions, billions, etc. as appropriate.

        Question: {question}
        SQL Query: {query}
        SQL Result: {result}
        Answer: """
        )
        answer_chain = answer_prompt | self.query_llm | StrOutputParser()

        # # Execute step-wise for now
        # table_info = table_info_chain.invoke({"question": question})
        # query = query_chain.invoke(
        #     {"question": question, "top_k": self.max_results, "table_info": table_info}
        # )
        # results = execute_query_chain.invoke({"query": query})
        # answer = answer_chain.invoke(
        #     {"question": question, "query": query, "result": results}
        # )

        # Combine all elements into a single chain
        full_chain = (
            RunnablePassthrough.assign(table_info=table_info_chain)
            .assign(query=query_chain)
            .assign(result=execute_query_chain)
            | answer_chain
        )

        answer = full_chain.invoke({"question": question, "top_k": self.max_results})

        return answer


# Usage example:
if __name__ == "__main__":
    atlas_sql = AtlasTextToSQL(
        db_uri=os.getenv("ATLAS_DB_URL"),
        table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
        table_structure_json=BASE_DIR / "db_table_structure.json",
        queries_json=BASE_DIR / "src/example_queries/queries.json",
        example_queries_dir=BASE_DIR / "src/example_queries",
        max_results=15,
    )
    question = (
        "What were the top 5 products exported from United States to China in 2020?"
    )
    print(f"User question: {question}")
    answer = atlas_sql.answer_question(question)
    print(f"Answer: {answer}")
