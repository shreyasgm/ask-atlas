from typing import Dict, List, Union, Generator, Tuple, Optional
from pathlib import Path
from langchain_openai import ChatOpenAI
import logging
import datetime
import json
from sqlalchemy import create_engine
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
import warnings
from dataclasses import dataclass
from sqlalchemy import exc as sa_exc
from src.sql_multiple_schemas import SQLDatabaseWithSchemas
from src.generate_query import (
    load_example_queries,
    create_sql_agent,
)
from src.config import get_settings
from src.persistence import CheckpointerManager
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
    def __init__(
        self,
        db_uri: str | None = None,
        table_descriptions_json: str = "db_table_descriptions.json",
        table_structure_json: str = "db_table_structure.json",
        queries_json: str = "queries.json",
        example_queries_dir: str = "example_queries",
        max_results: int | None = None,
        max_queries: int | None = None,
    ):
        """
        Initialize the Atlas Text-to-SQL system.

        Args:
            db_uri: Database connection URI (defaults to settings.atlas_db_url)
            table_descriptions_json: Path to JSON file containing names of the tables and their descriptions
            table_structure_json: Path to JSON file containing table structure
            queries_json: Path to JSON file containing example queries
            example_queries_dir: Directory containing example SQL queries
            max_results: Maximum number of results to return from SELECT queries on the database
                        (defaults to settings.max_results_per_query)
            max_queries: Maximum number of queries per question
                        (defaults to settings.max_queries_per_question)
        """
        # Use settings defaults if not provided
        db_uri = db_uri or settings.atlas_db_url
        max_results = max_results if max_results is not None else settings.max_results_per_query
        max_queries = max_queries if max_queries is not None else settings.max_queries_per_question

        # Initialize engine
        self.engine = create_engine(
            db_uri,
            execution_options={"postgresql_readonly": True},
            connect_args={"connect_timeout": 10},
        )

        # Initialize database connection
        self.db = SQLDatabaseWithSchemas(engine=self.engine)

        # Load schema and structure information
        self.table_descriptions = self._load_json_as_dict(table_descriptions_json)
        self.table_structure = self._load_json_as_dict(table_structure_json)
        self.example_queries = load_example_queries(queries_json, example_queries_dir)

        # Initialize language models using settings
        self.metadata_llm = ChatOpenAI(model=settings.metadata_model, temperature=0)
        self.query_llm = ChatOpenAI(model=settings.query_model, temperature=0)

        self.max_results = max_results
        self.max_queries = max_queries

        # Initialize checkpointer (PostgresSaver if URL configured, else MemorySaver)
        self._checkpointer_manager = CheckpointerManager()

        # Initialize the agent once
        self.agent = create_sql_agent(
            llm=self.query_llm,
            db=self.db,
            engine=self.engine,
            example_queries=self.example_queries,
            table_descriptions=self.table_descriptions,
            top_k_per_query=self.max_results,
            max_uses=self.max_queries,
            checkpointer=self._checkpointer_manager.checkpointer,
        )

    @staticmethod
    def _load_json_as_dict(file_path: str) -> Dict:
        """Loads a JSON file as a dictionary."""
        with open(file_path, "r") as f:
            return json.load(f)

    def debug_message_ids(self, question: str, thread_id: str = None):
        """
        Debug function to inspect message IDs and their ordering, logging to a file.

        Args:
            question: The user's question about the trade data
            thread_id: The ID of the thread to use for the conversation

        Returns:
            Analysis of message IDs and their ordering
        """
        # Use the root logger that was already configured
        logger = logging.getLogger(__name__)

        config = {
            "configurable": {"thread_id": thread_id if thread_id else str(uuid.uuid4())}
        }

        logger.info(f"DEBUG RUN: {datetime.datetime.now().isoformat()}")
        logger.info(f"QUESTION: {question}")
        logger.info(f"THREAD ID: {thread_id}")

        # Collect all messages with their IDs and metadata
        all_messages = []
        tool_messages = []
        agent_messages = []

        for stream_data in self.agent.stream(
            {"messages": [HumanMessage(content=question)]},
            stream_mode="messages",
            config=config,
        ):
            msg, metadata = stream_data
            msg_id = getattr(msg, "id", None)
            langgraph_node = metadata.get("langgraph_node", "unknown")

            message_info = {
                "id": msg_id,
                "type": type(msg).__name__,
                "node": langgraph_node,
                "content_preview": (msg.content[:50] + "...")
                if msg.content
                else "No content",
                "has_tool_calls": hasattr(msg, "tool_calls")
                and bool(getattr(msg, "tool_calls", None)),
                "tool_name": getattr(msg, "name", None)
                if isinstance(msg, ToolMessage)
                else None,
            }

            all_messages.append(message_info)

            if isinstance(msg, ToolMessage):
                tool_messages.append(message_info)
            elif isinstance(msg, AIMessage) and langgraph_node == "agent":
                agent_messages.append(message_info)

        # Analyze and report findings
        logger.info(f"\n{'=' * 80}\nMESSAGE ID ANALYSIS\n{'=' * 80}")
        logger.info(f"Total messages: {len(all_messages)}")
        logger.info(f"Tool messages: {len(tool_messages)}")
        logger.info(f"Agent messages: {len(agent_messages)}")

        # Check if all messages have IDs
        messages_with_ids = [m for m in all_messages if m["id"]]
        logger.info(
            f"Messages with IDs: {len(messages_with_ids)} ({len(messages_with_ids) / len(all_messages) * 100:.1f}%)"
        )

        # Sort tool messages by ID to see their natural order
        if tool_messages:
            logger.info("\nTOOL MESSAGES BY ID:")
            sorted_tool_msgs = sorted(
                [m for m in tool_messages if m["id"]], key=lambda m: m["id"]
            )

            for i, msg in enumerate(sorted_tool_msgs):
                logger.info(
                    f"{i + 1}. ID: {msg['id']} | Tool: {msg['tool_name']} | Preview: {msg['content_preview']}"
                )

        # Look for patterns in IDs
        if messages_with_ids:
            id_patterns = {}
            for msg in messages_with_ids:
                # Try to match ID pattern (adjust based on your actual IDs)
                parts = msg["id"].split("_") if msg["id"] else []
                pattern = "_".join([p for p in parts if not p.isdigit()])
                if not pattern:
                    pattern = "numeric_only"

                if pattern not in id_patterns:
                    id_patterns[pattern] = []
                id_patterns[pattern].append(msg["id"])

            logger.info("\nID PATTERNS FOUND:")
            for pattern, ids in id_patterns.items():
                logger.info(f"- Pattern '{pattern}': {len(ids)} messages")
                logger.info(
                    f"  Example IDs: {', '.join(ids[:3])}"
                    + ("..." if len(ids) > 3 else "")
                )

        # Log more detailed information about each message
        logger.info(f"\n{'=' * 80}\nDETAILED MESSAGE LOG\n{'=' * 80}")
        for i, msg in enumerate(all_messages):
            logger.info(f"Message #{i + 1}:")
            logger.info(f"  ID: {msg['id']}")
            logger.info(f"  Type: {msg['type']}")
            logger.info(f"  Node: {msg['node']}")
            logger.info(f"  Tool Name: {msg['tool_name']}")
            logger.info(f"  Has Tool Calls: {msg['has_tool_calls']}")
            logger.info(f"  Content Preview: {msg['content_preview']}")
            logger.info("")

        return all_messages

    def answer_question(
        self,
        question: str,
        stream_response: bool = True,
        thread_id: str = None,
    ) -> Union[
        Tuple[Generator[Tuple[str, StreamData], None, None], List[StreamData]], str
    ]:
        """
        Process a user's question and return the answer with simplified streaming output.

        Args:
            question: The user's question about the trade data
            stream_response: Whether to stream the response back to the user
            thread_id: The ID of the thread to use for the conversation

        Returns:
            If stream_response is True:
                - A tuple containing:
                    1. Generator yielding tuples of (stream_mode, stream_data)
                    where stream_mode is either "updates" or "messages"
                    and stream_data is a StreamData object
                    2. List of all StreamData objects accumulated during processing
            If stream_response is False:
                - A string containing the final answer
        """
        # Generate thread_id if not provided to handle fresh conversations
        config = {
            "configurable": {"thread_id": thread_id if thread_id else str(uuid.uuid4())}
        }

        if not stream_response:
            # Non-streaming mode: Return final message directly
            result = self.agent.stream(
                {"messages": [HumanMessage(content=question)]},
                stream_mode="values",
                config=config,
            )
            for step in result:
                message = step["messages"][-1]
            return message.content

        # Initialize messages list to store all StreamData objects
        messages: List[StreamData] = []

        # # Create a generator that yields ordered stream data
        # def collect_messages():
        #     for stream_mode, stream_data in self.stream_agent_response(question, config):
        #         messages.append(stream_data)
        #         yield stream_mode, stream_data

        # return collect_messages(), messages

        return self.stream_agent_response(question=question, config=config)

    def stream_agent_response(
        self,
        question: str,
        config: Dict,
    ) -> Generator[Tuple[str, StreamData], None, None]:
        """
        Generate ordered stream output from agent responses.

        Args:
            question: The user's question
            config: Configuration dictionary for the agent

        Yields:
            Tuples of (stream_mode, StreamData) where stream_mode is either
            "updates" or "messages" and StreamData contains normalized message information
        """
        # Buffer to collect tool messages by ID
        tool_buffers = {}
        current_tool_id = None
        in_tool_stream = False

        for stream_mode, stream_data in self.agent.stream(
            {"messages": [HumanMessage(content=question)]},
            stream_mode=["messages", "updates"],
            config=config,
        ):
            if stream_mode == "updates":
                # Process updates stream for agent messages and tool calls
                if "agent" in stream_data:
                    # If we see an agent message after tool messages, flush all tool buffers
                    if in_tool_stream:
                        # Flush all tool buffers before yielding new agent content
                        for tool_id in list(tool_buffers.keys()):
                            for buffered_msg in tool_buffers[tool_id]:
                                yield "messages", buffered_msg
                            del tool_buffers[tool_id]
                        in_tool_stream = False
                        current_tool_id = None

                    for msg in stream_data["agent"].get("messages", []):
                        if isinstance(msg, AIMessage) and msg.content:
                            # Handle tool calls
                            tool_calls = getattr(msg, "tool_calls", [])
                            if tool_calls:
                                for tool_call in tool_calls:
                                    stream_obj = StreamData(
                                        source="agent",
                                        content=msg.content or "",
                                        message_type="tool_call",
                                        tool_call=tool_call.get("name"),
                                    )
                                    yield stream_mode, stream_obj
                            # Handle regular agent messages
                            elif msg.content:
                                stream_obj = StreamData(
                                    source="agent",
                                    content=msg.content,
                                    message_type="agent_talk",
                                )
                                yield stream_mode, stream_obj

                # Updates for tools exports tool output but not llm tokens
                elif "tools" in stream_data:
                    in_tool_stream = True
                    for msg in stream_data["tools"].get("messages", []):
                        if isinstance(msg, ToolMessage) and msg.content:
                            stream_obj = StreamData(
                                source="tool",
                                content=msg.content,
                                message_type="tool_output",
                                name=msg.name
                            )
                            yield stream_mode, stream_obj

            elif stream_mode == "messages":
                # This streams llm tokens
                msg, metadata = stream_data
                msg_id = getattr(msg, "id", None)
                
                # For agent messages (not from tools), yield directly and flush any active tool buffers
                if (
                    isinstance(msg, AIMessage)
                    and metadata.get("langgraph_node") != "tools"
                    and msg.content
                ):
                    # Flush all tool buffers before yielding new agent content
                    if in_tool_stream:
                        for tool_id in list(tool_buffers.keys()):
                            for buffered_msg in tool_buffers[tool_id]:
                                yield "messages", buffered_msg
                            del tool_buffers[tool_id]
                        in_tool_stream = False
                        current_tool_id = None
                    
                    # Now yield the agent message
                    stream_obj = StreamData(source="agent", content=msg.content)
                    yield stream_mode, stream_obj
                
                # For tool messages with content, buffer by ID
                elif (
                    isinstance(msg, AIMessage)
                    and metadata.get("langgraph_node") == "tools"
                    and msg.content
                ):
                    in_tool_stream = True
                    
                    # If there's no message ID, generate a pseudo-ID based on the tool name
                    if not msg_id:
                        tool_name = getattr(msg, "name", "unknown_tool")
                        msg_id = f"pseudo_{tool_name}_{hash(msg.content[:20])}"
                    
                    # If this is a new tool or continuing message from current tool
                    if current_tool_id is None or current_tool_id == msg_id:
                        # Start or continue streaming from this tool
                        current_tool_id = msg_id
                        stream_obj = StreamData(
                            source="tool", 
                            content=msg.content, 
                            message_type="tool_output",
                            name=getattr(msg, "name", None),
                            message_id=msg_id  # Include message ID here
                        )
                        yield stream_mode, stream_obj
                    else:
                        # This is from a different tool - buffer it
                        if msg_id not in tool_buffers:
                            tool_buffers[msg_id] = []
                        
                        tool_buffers[msg_id].append(
                            StreamData(
                                source="tool", 
                                content=msg.content, 
                                message_type="tool_output",
                                name=getattr(msg, "name", None),
                                message_id=msg_id  # Include message ID here
                            )
                        )

        # Flush any remaining tool buffers at the end of streaming
        for tool_id in list(tool_buffers.keys()):
            for buffered_msg in tool_buffers[tool_id]:
                yield "messages", buffered_msg

    def stream_agent_response_debug(
        self,
        question: str,
        config: Dict,
        max_messages: Optional[int] = None
    ) -> None:
        """
        Debug function to log stream output from agent responses in a pretty JSON format.

        Args:
            question: User's question
            config: Configuration dictionary for the agent
            max_messages: Optional maximum number of messages to log. If None, log all messages.
        """
        # Set up debug logging
        debug_log_file = log_dir / f"stream_debug_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        debug_logger = logging.getLogger('stream_debug')
        debug_logger.setLevel(logging.INFO)
        debug_handler = logging.FileHandler(debug_log_file)
        debug_handler.setFormatter(logging.Formatter('%(message)s'))
        debug_logger.addHandler(debug_handler)

        # Log stream data
        debug_logger.info(f"Question: {question}\n")
        debug_logger.info("=" * 80 + "\nStream Data\n" + "=" * 80 + "\n")
        
        message_count = 0
        for stream_mode, stream_data in self.agent.stream(
            {"messages": [HumanMessage(content=question)]},
            stream_mode=["messages", "updates"],
            config=config,
        ):
            if max_messages is not None and message_count >= max_messages:
                debug_logger.info("\nReached message limit. Stopping debug output.")
                break
            
            # Convert stream data to a serializable format
            if stream_mode == "messages":
                msg, metadata = stream_data
                serialized_data = {
                    "stream_mode": stream_mode,
                    "msg": msg,
                    "metadata": metadata
                }
            else:  # stream_mode == "updates"
                serialized_data = {
                    "stream_mode": stream_mode,
                    "data": stream_data
                }
            
            # Log the formatted JSON
            debug_logger.info(f"Message #{message_count + 1}:")
            debug_logger.info(json.dumps(serialized_data, indent=2, default=str))
            debug_logger.info("-" * 80 + "\n")
            
            message_count += 1

        # Clean up the handler to prevent duplicate logging
        debug_logger.removeHandler(debug_handler)
        debug_handler.close()
        
        return debug_log_file

    def process_agent_messages(self, messages: List[Dict]) -> str:
        """
        DEPRECATED: This method is deprecated and will be removed in a future version.
        
        Process agent messages to extract final response content.
        """
        final_message_str = ""
        for message in reversed(messages):
            if message["metadata"]["langgraph_node"] == "agent":
                final_message_str = message["msg"].content + final_message_str
            else:
                break
        return final_message_str

    def process_stream_output(
        self,
        stream_generator: Generator[Tuple[str, StreamData], None, None],
        question: str,
        *,
        show_tool_details: bool = True,
        colorize_output: bool = True,
    ) -> str:
        """
        Process streaming output from the LLM agent and display it clearly to stdout.
        
        Args:
            stream_generator: Generator yielding (stream_mode, StreamData) tuples
            question: The original question for context
            show_tool_details: Whether to display verbose tool output details (default: False)
            colorize_output: Whether to colorize the terminal output (default: True)
        
        Returns:
            str: The complete accumulated answer from the agent
        """
        import sys
        
        # Terminal colors for better readability
        colors = {
            "reset": "\033[0m",
            "bold": "\033[1m",
            "agent": "\033[94m",  # Blue
            "tool_name": "\033[93m",  # Yellow
            "tool_content": "\033[96m",  # Cyan
            "question": "\033[92m",  # Green
            "separator": "\033[90m",  # Gray
        }
        
        # Disable colors if not requested or if not in a terminal
        if not colorize_output or not sys.stdout.isatty():
            colors = {k: "" for k in colors}
        
        # Print the question with formatting
        print(f"\n{colors['bold']}{colors['question']}Question: {question}{colors['reset']}\n")
        
        # Print a separator line
        separator = f"{colors['separator']}{'—' * 50}{colors['reset']}"
        print(separator)
        
        # Track current mode and accumulated content
        full_answer = ""
        current_tool_name = None
        in_tool_output = False
        
        for stream_mode, stream_data in stream_generator:
            # Handle message streams (direct content)
            if stream_mode == "messages":
                if stream_data.source == "agent":
                    # If we were in tool output mode, add a separator
                    if in_tool_output:
                        print(separator)
                        in_tool_output = False
                    
                    # Print agent message
                    print(f"{colors['agent']}{stream_data.content}{colors['reset']}", end="", flush=True)
                    
                    # Add to full answer only if it's from the agent
                    full_answer += stream_data.content
                
                elif stream_data.source == "tool" and show_tool_details:
                    # Start of a new tool output
                    if current_tool_name != stream_data.name:
                        if in_tool_output:
                            print("\n")
                        
                        # Display tool name header
                        tool_header = f"[Tool: {stream_data.name or 'Unknown'}]"
                        print(f"\n{colors['bold']}{colors['tool_name']}{tool_header}{colors['reset']}")
                        
                        current_tool_name = stream_data.name
                        in_tool_output = True
                    
                    # Display tool content
                    print(f"{colors['tool_content']}{stream_data.content}{colors['reset']}", end="", flush=True)
            
            # Handle update streams (typically tool calls)
            elif stream_mode == "updates" and show_tool_details:
                if stream_data.source == "agent" and stream_data.tool_call:
                    print(f"\n{colors['bold']}{colors['tool_name']}[Calling Tool: {stream_data.tool_call}]{colors['reset']}")
        
        # Print a final newline
        print("\n")
        
        return full_answer

    def __enter__(self):
        """Context manager entry point"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit point - ensures proper cleanup"""
        self.close()

    def close(self):
        """Close database connections and cleanup resources"""
        if hasattr(self, "_checkpointer_manager"):
            self._checkpointer_manager.close()
        if hasattr(self, "engine"):
            self.engine.dispose()


if __name__ == "__main__":
    with AtlasTextToSQL(
        table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
        table_structure_json=BASE_DIR / "db_table_structure.json",
        queries_json=BASE_DIR / "src/example_queries/queries.json",
        example_queries_dir=BASE_DIR / "src/example_queries",
    ) as atlas_sql:

        # ======
        # DEBUG
        # ======
        question = "What were the top 5 products exported by the US to China in 2020?"
        log_file = atlas_sql.stream_agent_response_debug(
            question,
            config={"configurable": {"thread_id": "debug_thread"}},
            max_messages=None,
        )
        print(f"Debug log written to: {log_file}")

        # =======================
        # Simple questions
        # question = "What were the top 5 products exported by the US to China in 2020?"
        # follow_up_question = "How did these products change in 2021?"
        # print(f"User question: {question}")
        # # atlas_sql.debug_message_ids(question, thread_id="debug_thread")
        # stream_gen = atlas_sql.stream_agent_response(question, config={"configurable": {"thread_id": "test_thread"}})
        # answer = atlas_sql.process_stream_output(stream_gen, question, show_tool_details=True)
        # print(f"Follow-up question: {follow_up_question}")
        # # atlas_sql.debug_message_ids(follow_up_question, thread_id="debug_thread")
        # stream_gen = atlas_sql.answer_question(
        #     follow_up_question, stream_response=True, thread_id="test_thread"
        # )
        # answer = atlas_sql.process_stream_output(stream_gen, follow_up_question, show_tool_details=True)

        # # Set up a separate thread for a more complex question
        # question = "How does Kenya's trade balance vary across its top 10 trading partners, and what factors drive these differences?"
        # stream_gen = atlas_sql.stream_agent_response(question, config={"configurable": {"thread_id": "test_thread"}})
        # answer = atlas_sql.process_stream_output(stream_gen, question)
