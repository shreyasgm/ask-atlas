import os
from typing import Dict, List, Union, Generator, Tuple
from pathlib import Path
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import logging
import sys
import json
from sqlalchemy import create_engine
from langchain_core.messages import HumanMessage, AIMessage
import warnings
from sqlalchemy import exc as sa_exc
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.generate_query import (
    load_example_queries,
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

        # Initialize the agent once
        self.agent = create_sql_agent(
            llm=self.query_llm,
            db=self.db,
            engine=self.engine,
            example_queries=self.example_queries,
            table_descriptions=self.table_descriptions,
            top_k_per_query=self.max_results,
            max_uses=10,
        )

    @staticmethod
    def _load_json_as_dict(file_path: str) -> Dict:
        """Loads a JSON file as a dictionary."""
        with open(file_path, "r") as f:
            return json.load(f)

    def answer_question(
        self,
        question: str,
        stream_response: bool = True,
        history: List[Dict] = None,
    ) -> Union[Tuple[Generator[str, None, None], List[Dict]], str]:
        """
        Process a user's question and return the answer.

        Args:
            question: The user's question about the trade data
            stream_response: Whether to stream the response back to the user
            history: A list of messages from the conversation history

        Returns:
            Either a string answer or a generator yielding string chunks
            List of dictionaries containing messages from the agent
        """
        
        if stream_response:
            messages = []

            def stream_agent_response():
                for msg, metadata in self.agent.stream(
                    {"messages": [HumanMessage(content=question)]},
                    stream_mode="messages",
                ):
                    if (
                        msg.content
                        and isinstance(msg, AIMessage)
                        and metadata["langgraph_node"] != "tools"
                    ):
                        yield msg.content
                    messages.append({"msg": msg, "metadata": metadata})

            return stream_agent_response(), messages

        else:
            # Get the last message directly without streaming
            result = self.agent.stream(
                {"messages": [HumanMessage(content=question)]},
                stream_mode="values",
            )
            for step in result:
                message = step["messages"][-1]
            final_message = message.content
            return final_message

    def process_agent_messages(self, messages: List[Dict]) -> str:
        final_message_str = ""
        for message in reversed(messages):
            if message["metadata"]["langgraph_node"] == "agent":
                final_message_str = message["msg"].content + final_message_str
            else:
                break
        return final_message_str


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
    # question = (
    #     "Analyze the trade relationship between Germany and Poland from 2010-2020."
    # )
    question = (
        "What were the top 5 products exported by the US to China in 2020?"
    )
    print(f"User question: {question}")
    stream_response = True
    answer, messages = atlas_sql.answer_question(
        question, stream_response=True, history=None
    )
    
    print("Answer: ")
    full_answer = ""
    for chunk in answer:
        print(chunk, end="", flush=True)
        full_answer += chunk

    print("\nFull answer stored in variable:", full_answer)

    # if messages:
    #     # Get the final agent message
    #     final_message_str = atlas_sql.process_agent_messages(messages)
    #     print(f"\n==================\nFinal message:\n{final_message_str}")


    # Test conversation history
    # follow_up_question = "How did these products change in 2021?"
    # answer, messages = atlas_sql.answer_question(
    #     follow_up_question, stream_response=True, use_agent=True, history=messages
    # )
    # print(f"Follow-up question: {follow_up_question}")
    # print("Answer: ")
    # for chunk in answer:
    #     print(chunk, end="", flush=True)