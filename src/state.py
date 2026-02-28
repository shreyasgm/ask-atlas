"""Typed state definitions for the Atlas agent graph.

Provides a well-typed state schema used by the StateGraph that powers
the Atlas agent and its inner query pipeline.
"""

from typing import Annotated, Literal, Optional

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


def add_token_usage(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Reducer that accumulates token usage records across graph nodes.

    Args:
        existing: Previously accumulated usage records (may be None).
        new: New usage records to append (may be None).

    Returns:
        Combined list of all token usage records.
    """
    return (existing or []) + (new or [])


def add_step_timing(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Reducer that accumulates per-step timing records across graph nodes.

    Args:
        existing: Previously accumulated timing records (may be None).
        new: New timing records to append (may be None).

    Returns:
        Combined list of all timing records.
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
        pipeline_context: Optional context extracted from the agent's tool_call args.
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
        override_agent_mode: Per-request agent mode override (auto/sql_only/graphql_sql).
        graphql_question: Question extracted from the GraphQL tool_call args.
        graphql_context: Conversational context for the GraphQL question.
        graphql_classification: Classification result dict (query_type, api_target, etc.).
        graphql_entity_extraction: Extracted entities dict (country, product, year, etc.).
        graphql_resolved_params: Resolved entity IDs and API parameters.
        graphql_query: The constructed GraphQL query string.
        graphql_api_target: Target API identifier (e.g., "explore", "country_pages").
        graphql_raw_response: Raw response data from the GraphQL API.
        graphql_execution_time_ms: GraphQL query execution time in milliseconds.
        graphql_atlas_links: Atlas visualization links generated from resolved params.
        docs_question: Question extracted from the docs_tool tool_call args.
        docs_context: Broader user context for the docs question.
        docs_selected_files: Filenames of documentation files selected by the LLM.
        docs_synthesis: Synthesized documentation response.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
    # Pipeline intermediate state (populated during query execution)
    pipeline_question: str
    pipeline_context: str
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
    # Accumulated LLM token usage records (per-node granularity)
    token_usage: Annotated[list[dict], add_token_usage]
    # Accumulated per-step timing records (wall clock, LLM, I/O per node)
    step_timing: Annotated[list[dict], add_step_timing]
    # Trade toggle overrides (None = auto-detect)
    override_schema: Optional[str]
    override_direction: Optional[str]
    override_mode: Optional[str]
    # Per-request agent mode override (auto/sql_only/graphql_sql); takes precedence over build-time config
    override_agent_mode: Optional[str]
    # === GraphQL pipeline state (reset by extract_graphql_question at cycle start) ===
    graphql_question: str
    graphql_context: str
    graphql_classification: Optional[dict]
    graphql_entity_extraction: Optional[dict]
    graphql_resolved_params: Optional[dict]
    graphql_query: Optional[str]
    graphql_api_target: Literal["explore", "country_pages"] | None
    graphql_raw_response: Optional[dict]
    graphql_execution_time_ms: int
    graphql_atlas_links: list[dict]
    # === Docs pipeline state (reset by extract_docs_question at cycle start) ===
    docs_question: str
    docs_context: str
    docs_selected_files: list[str]
    docs_synthesis: str
