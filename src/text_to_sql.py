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
from decimal import Decimal
from sqlalchemy import exc as sa_exc
import sqlglot
from sqlglot import exp
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


# ---------------------------------------------------------------------------
# Pipeline topology constants (used for emitting node_start/pipeline_state)
# ---------------------------------------------------------------------------

PIPELINE_SEQUENCE = [
    "extract_tool_question",
    "extract_products",
    "lookup_codes",
    "get_table_info",
    "generate_sql",
    "validate_sql",
    "execute_sql",
    "format_results",
]

NODE_LABELS = {
    "extract_tool_question": "Extracting question",
    "extract_products": "Identifying products",
    "lookup_codes": "Looking up product codes",
    "get_table_info": "Loading table metadata",
    "generate_sql": "Generating SQL query",
    "validate_sql": "Validating SQL",
    "execute_sql": "Executing query",
    "format_results": "Formatting results",
    "max_queries_exceeded": "Query limit reached",
}


def _json_safe(value: object) -> object:
    """Convert non-JSON-serializable values to strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _json_safe_deep(obj: object) -> object:
    """Recursively make an object JSON-safe."""
    if isinstance(obj, dict):
        return {k: _json_safe_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_deep(v) for v in obj]
    return _json_safe(obj)


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract schema-qualified table names from a SQL string.

    Args:
        sql: The SQL query string.

    Returns:
        Sorted list of unique table names found (e.g. ``["hs92.country_year"]``).
    """
    if not sql or not sql.strip():
        return []
    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        tables: set[str] = set()
        for table_node in parsed.find_all(exp.Table):
            db = table_node.db  # schema in sqlglot terms
            name = table_node.name
            if db:
                tables.add(f"{db}.{name}")
            elif name:
                tables.add(name)
        return sorted(tables)
    except Exception:
        return []


def _extract_pipeline_state(node_name: str, state_snapshot: dict) -> dict:
    """Extract structured payload for a pipeline_state event.

    Args:
        node_name: The pipeline node that just completed.
        state_snapshot: Accumulated state updates from the pipeline.

    Returns:
        A dict with "stage" and node-specific keys.
    """
    base = {"stage": node_name}

    if node_name == "extract_tool_question":
        base["question"] = state_snapshot.get("pipeline_question", "")

    elif node_name == "extract_products":
        products = state_snapshot.get("pipeline_products")
        if products:
            base["schemas"] = products.classification_schemas
            base["products"] = [
                {"name": p.name, "codes": p.codes, "schema": p.classification_schema}
                for p in (products.products or [])
            ]
            base["requires_lookup"] = products.requires_product_lookup
        else:
            base["schemas"] = []
            base["products"] = []
            base["requires_lookup"] = False

    elif node_name == "lookup_codes":
        codes = state_snapshot.get("pipeline_codes", "")
        base["codes"] = codes
        base["has_codes"] = bool(codes)

    elif node_name == "get_table_info":
        products = state_snapshot.get("pipeline_products")
        base["schemas"] = products.classification_schemas if products else []

    elif node_name == "generate_sql":
        base["sql"] = state_snapshot.get("pipeline_sql", "")
        base["question"] = state_snapshot.get("pipeline_question", "")

    elif node_name == "validate_sql":
        error = state_snapshot.get("last_error", "")
        base["sql"] = state_snapshot.get("pipeline_sql", "")
        base["is_valid"] = not bool(error)
        if error:
            base["error"] = error

    elif node_name == "execute_sql":
        sql = state_snapshot.get("pipeline_sql", "")
        base["sql"] = sql
        base["columns"] = state_snapshot.get("pipeline_result_columns", [])
        base["rows"] = _json_safe_deep(state_snapshot.get("pipeline_result_rows", []))
        base["row_count"] = len(base["rows"])
        base["execution_time_ms"] = state_snapshot.get("pipeline_execution_time_ms", 0)
        base["tables"] = _extract_tables_from_sql(sql)
        products = state_snapshot.get("pipeline_products")
        if products and products.classification_schemas:
            base["schema"] = products.classification_schemas[0]

    elif node_name == "format_results":
        base["query_index"] = state_snapshot.get("_query_index", 0)

    return base


@dataclass
class AnswerResult:
    """Structured result from aanswer_question().

    Attributes:
        answer: The agent's final text answer.
        queries: List of executed query dicts, each with sql, columns, rows,
            row_count, execution_time_ms, tables, and optional schema_name.
        resolved_products: Product resolution data (schemas + products), or None.
        schemas_used: List of classification schemas used in queries.
        total_rows: Sum of row_count across all queries.
        total_execution_time_ms: Sum of execution_time_ms across all queries.
    """

    answer: str
    queries: list[dict]
    resolved_products: dict | None
    schemas_used: list[str]
    total_rows: int
    total_execution_time_ms: int


@dataclass
class StreamData:
    """Data structure for normalized stream output from agent or tool"""

    source: str  # 'agent' or 'tool'
    content: str
    message_type: str  # 'tool_call', 'tool_output', 'agent_talk', 'node_start', 'pipeline_state'
    name: Optional[str] = None  # name of the message if applicable
    tool_call: Optional[str] = None  # Tool call name if applicable
    message_id: Optional[str] = None  # ID of the original message for tracking
    payload: Optional[Dict] = None  # Structured data for new event types


def _build_turn_summary(
    queries: list[dict], resolved_products: dict | None
) -> dict:
    """Build a turn summary dict from pipeline results.

    Args:
        queries: List of executed query dicts from the turn.
        resolved_products: Product resolution data, or None.

    Returns:
        A summary dict with entities, queries, total_rows, total_execution_time_ms.
    """
    return {
        "entities": resolved_products,
        "queries": queries,
        "total_rows": sum(q.get("row_count", 0) for q in queries),
        "total_execution_time_ms": sum(q.get("execution_time_ms", 0) for q in queries),
    }


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
    def _turn_input(
        question: str,
        *,
        override_schema: str | None = None,
        override_direction: str | None = None,
        override_mode: str | None = None,
    ) -> dict:
        """Build the input dict for a new conversational turn.

        Resets per-turn counters so that Turn N doesn't inherit
        Turn N-1's ``queries_executed`` / ``last_error`` / ``retry_count``
        from the checkpoint.

        Args:
            question: The user's question.
            override_schema: Optional schema override (hs92/hs12/sitc).
            override_direction: Optional direction override (exports/imports).
            override_mode: Optional mode override (goods/services).
        """
        return {
            "messages": [HumanMessage(content=question)],
            "queries_executed": 0,
            "last_error": "",
            "retry_count": 0,
            "pipeline_result_columns": [],
            "pipeline_result_rows": [],
            "pipeline_execution_time_ms": 0,
            "override_schema": override_schema,
            "override_direction": override_direction,
            "override_mode": override_mode,
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
        table_descriptions_json: str | Path = BASE_DIR / "db_table_descriptions.json",
        table_structure_json: str | Path = BASE_DIR / "db_table_structure.json",
        queries_json: str | Path = BASE_DIR / "src" / "example_queries" / "queries.json",
        example_queries_dir: str | Path = BASE_DIR / "src" / "example_queries",
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
        *,
        override_schema: str | None = None,
        override_direction: str | None = None,
        override_mode: str | None = None,
    ) -> AnswerResult:
        """Non-streaming async answer with structured pipeline data.

        Args:
            question: The user's question about the trade data.
            thread_id: Conversation thread ID (generated if not provided).
            override_schema: Optional schema override (hs92/hs12/sitc).
            override_direction: Optional direction override (exports/imports).
            override_mode: Optional mode override (goods/services).

        Returns:
            AnswerResult with the answer text and pipeline data.
        """
        config = {
            "configurable": {"thread_id": thread_id or str(uuid.uuid4())}
        }
        turn_input = self._turn_input(
            question,
            override_schema=override_schema,
            override_direction=override_direction,
            override_mode=override_mode,
        )
        message = None
        prev_queries_executed = 0
        queries: list[dict] = []
        last_state: dict = {}

        async for step in self.agent.astream(
            turn_input,
            stream_mode="values",
            config=config,
        ):
            message = step["messages"][-1]
            last_state = step

            # Detect when a new query has been executed
            current_queries_executed = step.get("queries_executed", 0)
            if current_queries_executed > prev_queries_executed:
                sql = step.get("pipeline_sql", "")
                queries.append({
                    "sql": sql,
                    "columns": step.get("pipeline_result_columns", []),
                    "rows": _json_safe_deep(step.get("pipeline_result_rows", [])),
                    "row_count": len(step.get("pipeline_result_rows", [])),
                    "execution_time_ms": step.get("pipeline_execution_time_ms", 0),
                    "tables": _extract_tables_from_sql(sql),
                    "schema_name": None,
                })
                # Set schema_name from pipeline_products if available
                products = step.get("pipeline_products")
                if products and products.classification_schemas:
                    queries[-1]["schema_name"] = products.classification_schemas[0]
                prev_queries_executed = current_queries_executed

        # Extract resolved products from the final state
        resolved_products = None
        schemas_used: list[str] = []
        pipeline_products = last_state.get("pipeline_products")
        if pipeline_products and queries:
            schemas_used = pipeline_products.classification_schemas or []
            resolved_products = {
                "schemas": schemas_used,
                "products": [
                    {"name": p.name, "codes": p.codes, "schema": p.classification_schema}
                    for p in (pipeline_products.products or [])
                ],
            }

        # Persist turn summary to checkpoint for history restoration
        summary = _build_turn_summary(queries, resolved_products)
        await self.agent.aupdate_state(config, {"turn_summaries": [summary]})

        return AnswerResult(
            answer=self._extract_text(message.content),
            queries=queries,
            resolved_products=resolved_products,
            schemas_used=schemas_used,
            total_rows=sum(q["row_count"] for q in queries),
            total_execution_time_ms=sum(q["execution_time_ms"] for q in queries),
        )

    async def astream_agent_response(
        self,
        question: str,
        config: Dict,
        *,
        override_schema: str | None = None,
        override_direction: str | None = None,
        override_mode: str | None = None,
    ) -> AsyncGenerator[Tuple[str, StreamData], None]:
        """Async variant of ``stream_agent_response()``.

        Yields ``(stream_mode, StreamData)`` tuples. In addition to the
        original ``agent_talk``, ``tool_call``, and ``tool_output`` events,
        this now emits:

        - **node_start**: when a pipeline node begins execution
        - **pipeline_state**: when a pipeline node completes, carrying
          structured data about what that node produced

        Uses ``stream_mode=["messages", "updates"]`` and infers node
        transitions from ``updates`` chunks whose keys are pipeline
        node names.

        Args:
            question: The user's question.
            config: Configuration dictionary for the agent.
            override_schema: Optional schema override (hs92/hs12/sitc).
            override_direction: Optional direction override (exports/imports).
            override_mode: Optional mode override (goods/services).

        Yields:
            Tuples of (stream_mode, StreamData).
        """
        tool_buffers: Dict[str, list[StreamData]] = {}
        current_tool_id: str | None = None
        in_tool_stream = False

        # Dedup tracking: messages mode emits token-by-token agent_talk;
        # updates mode emits the same content as one big agent_talk.
        # Prefer messages mode (incremental) and skip the updates duplicate.
        agent_talk_emitted_from_messages = False

        # Pipeline tracking
        query_index = 0
        pipeline_snapshot: dict = {}  # accumulated state across pipeline nodes
        pipeline_started = False  # True after first pipeline node seen in a cycle

        def _make_node_start(node: str) -> StreamData:
            return StreamData(
                source="pipeline",
                content="",
                message_type="node_start",
                payload={
                    "node": node,
                    "label": NODE_LABELS.get(node, node),
                    "query_index": query_index,
                },
            )

        def _make_pipeline_state(node: str) -> StreamData:
            pipeline_snapshot["_query_index"] = query_index
            return StreamData(
                source="pipeline",
                content="",
                message_type="pipeline_state",
                payload=_extract_pipeline_state(node, pipeline_snapshot),
            )

        def _next_pipeline_node(current_node: str) -> str | None:
            """Return the next node in PIPELINE_SEQUENCE, respecting routing."""
            if current_node == "validate_sql":
                # Check if validation failed → skip execute_sql
                if pipeline_snapshot.get("last_error"):
                    return "format_results"
                return "execute_sql"
            try:
                idx = PIPELINE_SEQUENCE.index(current_node)
                if idx + 1 < len(PIPELINE_SEQUENCE):
                    return PIPELINE_SEQUENCE[idx + 1]
            except ValueError:
                pass
            return None

        turn_input = self._turn_input(
            question,
            override_schema=override_schema,
            override_direction=override_direction,
            override_mode=override_mode,
        )

        async for stream_mode, stream_data in self.agent.astream(
            turn_input,
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
                        if isinstance(msg, AIMessage):
                            tool_calls = getattr(msg, "tool_calls", [])
                            if tool_calls:
                                # Agent issued tool_call → new pipeline cycle
                                query_index += 1
                                pipeline_snapshot = {}
                                pipeline_started = False

                                yield stream_mode, StreamData(
                                    source="agent",
                                    content=msg.content or "",
                                    message_type="tool_call",
                                    tool_call=tool_calls[0].get("name"),
                                )
                            elif msg.content:
                                if not agent_talk_emitted_from_messages:
                                    yield stream_mode, StreamData(
                                        source="agent",
                                        content=msg.content,
                                        message_type="agent_talk",
                                    )
                                # Reset for next agent turn
                                agent_talk_emitted_from_messages = False
                else:
                    pipeline_keys = set(stream_data.keys()) & PIPELINE_NODES
                    if pipeline_keys:
                        in_tool_stream = True
                        for node_name in pipeline_keys:
                            node_update = stream_data[node_name]

                            # Emit node_start for this node (first time in cycle)
                            if not pipeline_started:
                                yield stream_mode, _make_node_start(node_name)
                                pipeline_started = True

                            # Accumulate state from this node's update
                            for key, value in node_update.items():
                                if key != "messages":
                                    pipeline_snapshot[key] = value

                            # Emit pipeline_state for the completed node
                            yield stream_mode, _make_pipeline_state(node_name)

                            # Emit node_start for the NEXT node (if applicable)
                            next_node = _next_pipeline_node(node_name)
                            if next_node and next_node != "format_results":
                                # format_results will emit its own node_start
                                # when it appears in the updates stream
                                yield stream_mode, _make_node_start(next_node)
                            elif next_node == "format_results":
                                # We need format_results node_start now since
                                # it produces a ToolMessage
                                yield stream_mode, _make_node_start("format_results")

                            # Emit ToolMessages from this node (existing behavior)
                            for msg in node_update.get("messages", []):
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

                    agent_talk_emitted_from_messages = True
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
        *,
        override_schema: str | None = None,
        override_direction: str | None = None,
        override_mode: str | None = None,
    ) -> AsyncGenerator[StreamData, None]:
        """High-level async streaming that yields ``StreamData`` objects.

        Convenience wrapper around ``astream_agent_response`` that handles
        config creation and strips the stream-mode prefix.

        Args:
            question: The user's question.
            thread_id: Conversation thread ID (generated if not provided).
            override_schema: Optional schema override (hs92/hs12/sitc).
            override_direction: Optional direction override (exports/imports).
            override_mode: Optional mode override (goods/services).

        Yields:
            StreamData objects for each piece of streamed content.
        """
        config = {
            "configurable": {"thread_id": thread_id or str(uuid.uuid4())}
        }
        async for _stream_mode, stream_data in self.astream_agent_response(
            question,
            config,
            override_schema=override_schema,
            override_direction=override_direction,
            override_mode=override_mode,
        ):
            yield stream_data


if __name__ == "__main__":
    import asyncio

    async def main():
        async with await AtlasTextToSQL.create_async() as atlas_sql:
            question = "What were the top 5 products exported by the US to China in 2020?"
            config = {"configurable": {"thread_id": "debug_thread"}}
            async for stream_mode, stream_data in atlas_sql.astream_agent_response(
                question, config
            ):
                print(f"[{stream_mode}] {stream_data.source}: {stream_data.content[:80]}")

    asyncio.run(main())
