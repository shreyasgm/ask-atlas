"""Typed state definitions for the Atlas agent graph.

Provides a well-typed state schema that will replace the implicit
``{"messages": list}`` dict once the custom StateGraph (F-4) is wired up.
"""

from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AtlasAgentState(TypedDict):
    """State carried through each node of the Atlas agent graph.

    Attributes:
        messages: Conversation history managed by LangGraph's message reducer.
        queries_executed: Number of SQL queries executed so far for this turn.
        last_error: Most recent error message, or empty string if none.
        retry_count: Number of retries attempted for the current query.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    queries_executed: int
    last_error: str
    retry_count: int
