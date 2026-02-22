"""Typed state definitions for the Atlas agent graph.

Provides a well-typed state schema used by the StateGraph that powers
the Atlas agent and its inner query pipeline.
"""

from typing import Annotated, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.product_and_schema_lookup import SchemasAndProductsFound


def add_turn_summaries(
    existing: list[dict] | None, new: list[dict] | None
) -> list[dict]:
    """Reducer that accumulates turn summaries across conversation turns.

    Same append-only pattern as LangGraph's ``add_messages``.

    Args:
        existing: Previously accumulated summaries (may be None on first turn).
        new: New summaries to append (may be None if no summary produced).

    Returns:
        Combined list of all turn summaries.
    """
    return (existing or []) + (new or [])


class AtlasAgentState(TypedDict):
    """State carried through each node of the Atlas agent graph.

    Attributes:
        messages: Conversation history managed by LangGraph's message reducer.
        queries_executed: Number of SQL queries executed so far for this turn.
        last_error: Most recent error message, or empty string if none.
        retry_count: Number of retries attempted for the current query.
        pipeline_question: Question extracted from the agent's tool_call args.
        pipeline_products: Product/schema extraction results.
        pipeline_codes: Formatted product codes string for the SQL prompt.
        pipeline_table_info: Table DDL/descriptions for identified schemas.
        pipeline_sql: Generated SQL query string.
        pipeline_result: Formatted query result string.
        pipeline_result_columns: Column names from the last executed query.
        pipeline_result_rows: Row data from the last executed query.
        pipeline_execution_time_ms: Query execution time in milliseconds.
        turn_summaries: Accumulated per-turn pipeline summaries (entities, queries, stats).
        override_schema: User-specified classification schema override.
        override_direction: User-specified trade direction override.
        override_mode: User-specified trade mode override (goods/services).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
    # Pipeline intermediate state (populated during query execution)
    pipeline_question: str
    pipeline_products: Optional[SchemasAndProductsFound]
    pipeline_codes: str
    pipeline_table_info: str
    pipeline_sql: str
    pipeline_result: str
    pipeline_result_columns: list[str]
    pipeline_result_rows: list[list]
    pipeline_execution_time_ms: int
    # Accumulated per-turn pipeline summaries (persisted in checkpoint)
    turn_summaries: Annotated[list[dict], add_turn_summaries]
    # Trade toggle overrides (None = auto-detect)
    override_schema: Optional[str]
    override_direction: Optional[str]
    override_mode: Optional[str]
