from typing import AsyncGenerator, Dict, Optional, Tuple
from pathlib import Path
import logging
import datetime
import json
from sqlalchemy import create_engine, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import warnings
from dataclasses import dataclass
from sqlalchemy import exc as sa_exc
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.generate_query import (
    load_example_queries,
    create_sql_agent,
    PIPELINE_NODES,
)
from src.config import get_settings, create_llm
from src.persistence import AsyncCheckpointerManager
import uuid

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[1]
print(f"BASE_DIR: {BASE_DIR}")

# Create logs directory if it doesn't exist
log_dir = BASE_DIR / "logs"
log_dir.mkdir(exist_ok=True)

# Set up logging to file with timestamp
log_file = (
    log_dir / f"atlas_sql_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Suppress SQLAlchemy warning about vector type
warnings.filterwarnings(
    "ignore",
    category=sa_exc.SAWarning,
    message="Did not recognize type 'vector' of column 'embedding'",
)

# Load settings (replaces load_dotenv)
settings = get_settings()


@dataclass
class StreamData:
    """Data structure for normalized stream output from agent or tool"""

    source: str  # 'agent' or 'tool'
    content: str
    message_type: str  # 'tool_call', 'tool_output', 'agent_talk', etc.
    name: Optional[str] = None  # name of the message if applicable
    tool_call: Optional[str] = None  # Tool call name if applicable
    message_id: Optional[str] = None  # ID of the original message for tracking


class AtlasTextToSQL:
    # --- SYNC API (commented out — async-only after Phase 3) ---
    # def __init__(
    #     self,
    #     db_uri: str | None = None,
    #     table_descriptions_json: str = "db_table_descriptions.json",
    #     table_structure_json: str = "db_table_structure.json",
    #     queries_json: str = "queries.json",
    #     example_queries_dir: str = "example_queries",
    #     max_results: int | None = None,
    #     max_queries: int | None = None,
    # ):
    #     """
    #     Initialize the Atlas Text-to-SQL system.
    #
    #     Args:
    #         db_uri: Database connection URI (defaults to settings.atlas_db_url)
    #         table_descriptions_json: Path to JSON file containing names of the tables and their descriptions
    #         table_structure_json: Path to JSON file containing table structure
    #         queries_json: Path to JSON file containing example queries
    #         example_queries_dir: Directory containing example SQL queries
    #         max_results: Maximum number of results to return from SELECT queries on the database
    #                     (defaults to settings.max_results_per_query)
    #         max_queries: Maximum number of queries per question
    #                     (defaults to settings.max_queries_per_question)
    #     """
    #     # Use settings defaults if not provided
    #     db_uri = db_uri or settings.atlas_db_url
    #     max_results = max_results if max_results is not None else settings.max_results_per_query
    #     max_queries = max_queries if max_queries is not None else settings.max_queries_per_question
    #
    #     # Initialize engine with connection pooling for concurrent usage
    #     self.engine = create_engine(
    #         db_uri,
    #         execution_options={"postgresql_readonly": True},
    #         connect_args={"connect_timeout": 10},
    #         pool_size=10,
    #         max_overflow=20,
    #         pool_timeout=30,
    #         pool_recycle=1800,
    #         pool_pre_ping=True,
    #     )
    #
    #     # Initialize database connection
    #     self.db = SQLDatabaseWithSchemas(engine=self.engine)
    #
    #     # Load schema and structure information
    #     self.table_descriptions = self._load_json_as_dict(table_descriptions_json)
    #     self.table_structure = self._load_json_as_dict(table_structure_json)
    #     self.example_queries = load_example_queries(queries_json, example_queries_dir)
    #
    #     # Initialize language models using settings
    #     self.metadata_llm = create_llm(settings.metadata_model, settings.metadata_model_provider, temperature=0)
    #     self.query_llm = create_llm(settings.query_model, settings.query_model_provider, temperature=0)
    #
    #     self.max_results = max_results
    #     self.max_queries = max_queries
    #
    #     # Initialize checkpointer (PostgresSaver if URL configured, else MemorySaver)
    #     self._checkpointer_manager = CheckpointerManager()
    #
    #     # Initialize the agent once
    #     self.agent = create_sql_agent(
    #         llm=self.query_llm,
    #         db=self.db,
    #         engine=self.engine,
    #         example_queries=self.example_queries,
    #         table_descriptions=self.table_descriptions,
    #         top_k_per_query=self.max_results,
    #         max_uses=self.max_queries,
    #         checkpointer=self._checkpointer_manager.checkpointer,
    #     )
    # --- END SYNC __init__ ---

    @staticmethod
    def _turn_input(question: str) -> dict:
        """Build the input dict for a new conversational turn.

        Resets per-turn counters so that Turn N doesn't inherit
        Turn N-1's ``queries_executed`` / ``last_error`` / ``retry_count``
        from the checkpoint.
        """
        return {
            "messages": [HumanMessage(content=question)],
            "queries_executed": 0,
            "last_error": "",
            "retry_count": 0,
        }

    @staticmethod
    def _extract_text(content: str | list) -> str:
        """Normalize message content to a plain string.

        Some providers (e.g. Google Gemini) return content as a list of
        content blocks rather than a plain string. This extracts the text.
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return str(content)

    @staticmethod
    def _load_json_as_dict(file_path: str) -> Dict:
        """Loads a JSON file as a dictionary."""
        with open(file_path, "r") as f:
            return json.load(f)

    # --- SYNC API (commented out — async-only after Phase 3) ---
    # def debug_message_ids(self, question: str, thread_id: str = None):
    #     ...  # See git history for full implementation

    # def answer_question(self, question, stream_response=True, thread_id=None):
    #     ...  # See git history for full implementation

    # def stream_agent_response(self, question, config):
    #     ...  # See git history for full implementation

    # def stream_agent_response_debug(self, question, config, max_messages=None):
    #     ...  # See git history for full implementation

    # def process_stream_output(self, stream_generator, question, *, ...):
    #     ...  # See git history for full implementation

    # def __enter__(self):
    #     return self

    # def __exit__(self, exc_type, exc_val, exc_tb):
    #     self.close()

    # def close(self):
    #     ...  # See git history for full implementation
    # --- END SYNC API ---

    async def aclose(self) -> None:
        """Async close — release async checkpointer and DB engines."""
        if hasattr(self, "_async_checkpointer_manager"):
            await self._async_checkpointer_manager.close()
        if hasattr(self, "async_engine"):
            await self.async_engine.dispose()
        if hasattr(self, "engine"):
            self.engine.dispose()

    async def __aenter__(self):
        """Async context manager entry point."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit point — ensures proper cleanup."""
        await self.aclose()

    # ------------------------------------------------------------------
    # Async factory & methods
    # ------------------------------------------------------------------

    @classmethod
    async def create_async(
        cls,
        db_uri: str | None = None,
        table_descriptions_json: str = "db_table_descriptions.json",
        table_structure_json: str = "db_table_structure.json",
        queries_json: str = "queries.json",
        example_queries_dir: str = "example_queries",
        max_results: int | None = None,
        max_queries: int | None = None,
    ) -> "AtlasTextToSQL":
        """Factory for async-capable instances (uses AsyncCheckpointerManager).

        Same interface as ``__init__`` but compiles the graph with an
        async-capable checkpointer so ``.astream()`` / ``.ainvoke()`` work
        correctly with PostgresSaver.

        Args:
            db_uri: Database connection URI (defaults to settings.atlas_db_url)
            table_descriptions_json: Path to JSON with table descriptions
            table_structure_json: Path to JSON with table structure
            queries_json: Path to JSON with example queries
            example_queries_dir: Directory with example SQL queries
            max_results: Max rows per SELECT query
            max_queries: Max queries per question
        """
        instance = cls.__new__(cls)

        _settings = get_settings()
        db_uri = db_uri or _settings.atlas_db_url
        max_results = max_results if max_results is not None else _settings.max_results_per_query
        max_queries = max_queries if max_queries is not None else _settings.max_queries_per_question

        # Sync engine: used for SQLDatabaseWithSchemas (metadata reflection)
        # and get_table_info_node (still sync, wrapped in asyncio.to_thread)
        instance.engine = create_engine(
            db_uri,
            execution_options={"postgresql_readonly": True},
            connect_args={"connect_timeout": 10},
            pool_size=5,
            max_overflow=5,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )

        # Async engine: used for query execution and product lookups (true async I/O).
        # Convert dialect to psycopg3 async: postgresql:// -> postgresql+psycopg://
        async_url = make_url(db_uri).set(drivername="postgresql+psycopg")
        instance.async_engine = create_async_engine(
            async_url,
            execution_options={"postgresql_readonly": True},
            connect_args={"connect_timeout": 10},
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )

        instance.db = SQLDatabaseWithSchemas(engine=instance.engine)
        instance.table_descriptions = cls._load_json_as_dict(table_descriptions_json)
        instance.table_structure = cls._load_json_as_dict(table_structure_json)
        instance.example_queries = load_example_queries(queries_json, example_queries_dir)
        instance.metadata_llm = create_llm(
            _settings.metadata_model, _settings.metadata_model_provider, temperature=0
        )
        instance.query_llm = create_llm(
            _settings.query_model, _settings.query_model_provider, temperature=0
        )
        instance.max_results = max_results
        instance.max_queries = max_queries

        # Async checkpointer (AsyncPostgresSaver or MemorySaver fallback)
        instance._async_checkpointer_manager = AsyncCheckpointerManager()
        checkpointer = await instance._async_checkpointer_manager.get_checkpointer()

        instance.agent = create_sql_agent(
            llm=instance.query_llm,
            db=instance.db,
            engine=instance.engine,
            example_queries=instance.example_queries,
            table_descriptions=instance.table_descriptions,
            top_k_per_query=instance.max_results,
            max_uses=instance.max_queries,
            checkpointer=checkpointer,
            async_engine=instance.async_engine,
        )

        return instance

    async def aanswer_question(
        self,
        question: str,
        thread_id: str | None = None,
    ) -> str:
        """Non-streaming async answer.

        Args:
            question: The user's question about the trade data.
            thread_id: Conversation thread ID (generated if not provided).

        Returns:
            The agent's final text answer.
        """
        config = {
            "configurable": {"thread_id": thread_id or str(uuid.uuid4())}
        }
        message = None
        async for step in self.agent.astream(
            self._turn_input(question),
            stream_mode="values",
            config=config,
        ):
            message = step["messages"][-1]
        return self._extract_text(message.content)

    async def astream_agent_response(
        self,
        question: str,
        config: Dict,
    ) -> AsyncGenerator[Tuple[str, StreamData], None]:
        """Async variant of ``stream_agent_response()``.

        Same buffering / ordering logic but uses ``async for`` over
        ``self.agent.astream()``.

        Args:
            question: The user's question.
            config: Configuration dictionary for the agent.

        Yields:
            Tuples of (stream_mode, StreamData).
        """
        tool_buffers: Dict[str, list[StreamData]] = {}
        current_tool_id: str | None = None
        in_tool_stream = False

        async for stream_mode, stream_data in self.agent.astream(
            self._turn_input(question),
            stream_mode=["messages", "updates"],
            config=config,
        ):
            if stream_mode == "updates":
                if "agent" in stream_data:
                    if in_tool_stream:
                        for tool_id in list(tool_buffers.keys()):
                            for buffered_msg in tool_buffers[tool_id]:
                                yield "messages", buffered_msg
                            del tool_buffers[tool_id]
                        in_tool_stream = False
                        current_tool_id = None

                    for msg in stream_data["agent"].get("messages", []):
                        if isinstance(msg, AIMessage) and msg.content:
                            tool_calls = getattr(msg, "tool_calls", [])
                            if tool_calls:
                                for tool_call in tool_calls:
                                    yield stream_mode, StreamData(
                                        source="agent",
                                        content=msg.content or "",
                                        message_type="tool_call",
                                        tool_call=tool_call.get("name"),
                                    )
                            elif msg.content:
                                yield stream_mode, StreamData(
                                    source="agent",
                                    content=msg.content,
                                    message_type="agent_talk",
                                )
                else:
                    pipeline_keys = set(stream_data.keys()) & PIPELINE_NODES
                    if pipeline_keys:
                        in_tool_stream = True
                        for node_name in pipeline_keys:
                            for msg in stream_data[node_name].get("messages", []):
                                if isinstance(msg, ToolMessage) and msg.content:
                                    yield stream_mode, StreamData(
                                        source="tool",
                                        content=msg.content,
                                        message_type="tool_output",
                                        name=msg.name,
                                    )

            elif stream_mode == "messages":
                msg, metadata = stream_data
                msg_id = getattr(msg, "id", None)

                if (
                    isinstance(msg, AIMessage)
                    and metadata.get("langgraph_node") not in PIPELINE_NODES
                    and msg.content
                ):
                    if in_tool_stream:
                        for tool_id in list(tool_buffers.keys()):
                            for buffered_msg in tool_buffers[tool_id]:
                                yield "messages", buffered_msg
                            del tool_buffers[tool_id]
                        in_tool_stream = False
                        current_tool_id = None

                    yield stream_mode, StreamData(
                        source="agent",
                        content=msg.content,
                        message_type="agent_talk",
                    )

                elif (
                    isinstance(msg, AIMessage)
                    and metadata.get("langgraph_node") in PIPELINE_NODES
                    and msg.content
                ):
                    in_tool_stream = True

                    if not msg_id:
                        tool_name = getattr(msg, "name", "unknown_tool")
                        msg_id = f"pseudo_{tool_name}_{hash(msg.content[:20])}"

                    if current_tool_id is None or current_tool_id == msg_id:
                        current_tool_id = msg_id
                        yield stream_mode, StreamData(
                            source="tool",
                            content=msg.content,
                            message_type="tool_output",
                            name=getattr(msg, "name", None),
                            message_id=msg_id,
                        )
                    else:
                        if msg_id not in tool_buffers:
                            tool_buffers[msg_id] = []
                        tool_buffers[msg_id].append(
                            StreamData(
                                source="tool",
                                content=msg.content,
                                message_type="tool_output",
                                name=getattr(msg, "name", None),
                                message_id=msg_id,
                            )
                        )

        # Flush remaining tool buffers
        for tool_id in list(tool_buffers.keys()):
            for buffered_msg in tool_buffers[tool_id]:
                yield "messages", buffered_msg

    async def aanswer_question_stream(
        self,
        question: str,
        thread_id: str | None = None,
    ) -> AsyncGenerator[StreamData, None]:
        """High-level async streaming that yields ``StreamData`` objects.

        Convenience wrapper around ``astream_agent_response`` that handles
        config creation and strips the stream-mode prefix.

        Args:
            question: The user's question.
            thread_id: Conversation thread ID (generated if not provided).

        Yields:
            StreamData objects for each piece of streamed content.
        """
        config = {
            "configurable": {"thread_id": thread_id or str(uuid.uuid4())}
        }
        async for _stream_mode, stream_data in self.astream_agent_response(
            question, config
        ):
            yield stream_data


if __name__ == "__main__":
    import asyncio

    async def main():
        async with await AtlasTextToSQL.create_async(
            table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
            table_structure_json=BASE_DIR / "db_table_structure.json",
            queries_json=BASE_DIR / "src/example_queries/queries.json",
            example_queries_dir=BASE_DIR / "src/example_queries",
        ) as atlas_sql:
            question = "What were the top 5 products exported by the US to China in 2020?"
            config = {"configurable": {"thread_id": "debug_thread"}}
            async for stream_mode, stream_data in atlas_sql.astream_agent_response(
                question, config
            ):
                print(f"[{stream_mode}] {stream_data.source}: {stream_data.content[:80]}")

    asyncio.run(main())
