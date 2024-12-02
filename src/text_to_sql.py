import os
from typing import Dict, List, Union, Generator
from pathlib import Path
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import logging
import sys
import json
from sqlalchemy import create_engine
from langchain.prompts import PromptTemplate
from langchain_core.runnables import (
    RunnableLambda,
    RunnablePassthrough,
    RunnableParallel,
)
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage
import warnings
from sqlalchemy import exc as sa_exc
from operator import itemgetter

from src.product_and_schema_lookup import (
    ProductAndSchemaLookup,
    ProductCodesMapping,
    format_product_codes_for_prompt,
)
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.generate_query import (
    load_example_queries,
    create_query_generation_chain,
    create_sql_agent,
)

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]
print(f"BASE_DIR: {BASE_DIR}")

# Set up logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(stream=sys.stdout))

# Suppress SQLAlchemy warning about vector type
warnings.filterwarnings(
    "ignore",
    category=sa_exc.SAWarning,
    message="Did not recognize type 'vector' of column 'embedding'",
)

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
        self.engine = engine
        self.db = SQLDatabaseWithSchemas(engine=engine)

        # Load schema and structure information
        self.table_descriptions = self._load_json_as_dict(table_descriptions_json)
        self.table_structure = self._load_json_as_dict(table_structure_json)
        self.example_queries = load_example_queries(queries_json, example_queries_dir)

        # Initialize language models
        self.metadata_llm = ChatOpenAI(model="gpt-4o", temperature=0)
        self.query_llm = ChatOpenAI(model="gpt-4o", temperature=0)

        self.max_results = max_results

    @staticmethod
    def _load_json_as_dict(file_path: str) -> Dict:
        """Loads a JSON file as a dictionary."""
        with open(file_path, "r") as f:
            return json.load(f)

    def get_table_info_for_schemas(self, classification_schemas: List[str]) -> str:
        """Get table information for a list of schemas."""
        table_descriptions = self.get_tables_in_schemas(classification_schemas)
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

    def get_tables_in_schemas(self, classification_schemas: List[str]) -> List[Dict]:
        """
        Gets all tables and their descriptions for the selected schemas.

        Args:
            classification_schemas: List of classification schema names

        Returns:
            List of dictionaries containing table information with schema-qualified table_name and context_str
        """
        tables = []
        for schema in classification_schemas:
            if schema in self.table_descriptions:
                for table in self.table_descriptions[schema]:
                    # Create a new dict with schema-qualified table name
                    tables.append(
                        {
                            "table_name": f"{schema}.{table['table_name']}",
                            "context_str": table["context_str"],
                        }
                    )
        return tables

    def answer_question(
        self, question: str, stream_response: bool = True, use_agent: bool = True
    ) -> Union[str, Generator[str, None, None]]:
        """
        Process a user's question and return the answer.

        Args:
            question: The user's question about the trade data
            stream_response: Whether to stream the response back to the user
            use_agent: Whether to use an agent to do query planning and execution

        Returns:
            Either a string answer or a generator yielding string chunks
        """
        # Product and schema lookup
        product_lookup = ProductAndSchemaLookup(
            llm=self.metadata_llm,
            connection=self.engine,
        )

        # Extract product codes and schemas
        mentions_chain = product_lookup.extract_schemas_and_product_mentions()

        # Extract official product codes
        codes_chain = (
            RunnableLambda(product_lookup.get_candidate_codes)
            | RunnableLambda(product_lookup.select_final_codes)
            | RunnableLambda(format_product_codes_for_prompt)
        )

        # Select relevant schemas
        table_info_chain = RunnableLambda(
            lambda x: self.get_table_info_for_schemas(x.classification_schemas)
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
        answer_chain = answer_prompt | self.metadata_llm | StrOutputParser()

        # # Execute step-wise for now
        # mentions = mentions_chain.invoke({"question": question})
        # codes = codes_chain.invoke(mentions)
        # table_info = self.get_table_info_for_schemas(
        #     classification_schemas=mentions.classification_schemas
        # )
        # query = query_chain.invoke(
        #     {
        #         "question": question,
        #         "top_k": self.max_results,
        #         "table_info": table_info,
        #         "codes": codes,
        #     }
        # )
        # results = execute_query_chain.invoke({"query": query})
        # answer = answer_chain.invoke(
        #     {"question": question, "query": query, "result": results}
        # )
        if use_agent:
            # Get product codes and schemas and then use agent to plan and execute query
            # Split the chain into two parts: mentions and query agent
            mentions = mentions_chain.invoke({"question": question})
            candidate_codes = product_lookup.get_candidate_codes(
                products_found=mentions
            )
            final_codes_chain = product_lookup.select_final_codes(candidate_codes)
            if isinstance(final_codes_chain, ProductCodesMapping):
                final_codes = final_codes_chain
            else:
                final_codes = final_codes_chain.invoke(
                    {"question": question}
                )
            final_codes_str = format_product_codes_for_prompt(final_codes)
            table_info = self.get_table_info_for_schemas(
                classification_schemas=mentions.classification_schemas
            )
            # Use agent to plan and execute query
            agent = create_sql_agent(
                llm=self.query_llm,
                db=self.db,
                example_queries=self.example_queries,
                table_info=table_info,
                codes=final_codes_str,
                top_k_per_query=self.max_results,
                max_uses=8,
            )

            print(f"Mentions: {mentions}")
            print(f"Codes: {final_codes}")
            if stream_response:
                for msg, metadata in agent.stream(
                    {"messages": [HumanMessage(content=question)]},
                    stream_mode="messages",
                ):
                    if (
                        msg.content
                        and isinstance(msg, AIMessage)
                        and metadata["langgraph_node"] != "tools"
                    ):
                        yield msg.content

            else:
                # Get the last message directly without streaming
                result = agent.stream(
                    {"messages": [HumanMessage(content=question)]}, stream_mode="values"
                )
                for step in result:
                    message = step["messages"][-1]
                final_message = message.content
                return final_message

        else:
            # Combine all elements into a single chain
            full_chain = (
                RunnableParallel(
                    {
                        "products_found": mentions_chain,
                        "question": itemgetter("question") | RunnablePassthrough(),
                    }
                )
                | {
                    "codes": itemgetter("products_found") | codes_chain,
                    "table_info": itemgetter("products_found") | table_info_chain,
                    "top_k": lambda x: self.max_results,
                    "question": itemgetter("question"),
                }
                | {"query": query_chain, "question": itemgetter("question")}
                | {
                    "result": execute_query_chain,
                    "question": itemgetter("question"),
                    "query": itemgetter("query"),
                }
                | answer_chain
            )

            if stream_response:
                return full_chain.stream({"question": question})
            else:
                answer = full_chain.invoke({"question": question})
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
    question = "Analyze the trade relationship between Germany and Eastern European countries (Poland, Czech Republic, Hungary) from 2010-2020. How has the nature of traded goods evolved, particularly in the automotive sector?"
    print(f"User question: {question}")
    answer = atlas_sql.answer_question(question, stream_response=True, use_agent=True)
    print("Answer: ")
    for chunk in answer:
        print(chunk, end="", flush=True)
