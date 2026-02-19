"""Unit tests for AtlasAgentState typed state."""

from langchain_core.messages import HumanMessage, AIMessage
from src.state import AtlasAgentState


class TestAtlasAgentState:
    """Tests for the AtlasAgentState TypedDict."""

    def test_create_initial_state(self):
        """Can create a valid initial state with all required fields."""
        state: AtlasAgentState = {
            "messages": [],
            "queries_executed": 0,
            "last_error": "",
            "retry_count": 0,
        }
        assert state["messages"] == []
        assert state["queries_executed"] == 0
        assert state["last_error"] == ""
        assert state["retry_count"] == 0

    def test_state_with_messages(self):
        """State accepts LangChain message objects."""
        msgs = [
            HumanMessage(content="What are top exports?"),
            AIMessage(content="Let me look that up."),
        ]
        state: AtlasAgentState = {
            "messages": msgs,
            "queries_executed": 1,
            "last_error": "",
            "retry_count": 0,
        }
        assert len(state["messages"]) == 2
        assert isinstance(state["messages"][0], HumanMessage)
        assert isinstance(state["messages"][1], AIMessage)

    def test_state_tracks_errors(self):
        """State can track error information."""
        state: AtlasAgentState = {
            "messages": [],
            "queries_executed": 2,
            "last_error": "Timeout connecting to database",
            "retry_count": 3,
        }
        assert state["last_error"] == "Timeout connecting to database"
        assert state["retry_count"] == 3

    def test_state_is_plain_dict(self):
        """AtlasAgentState is a TypedDict — instances are plain dicts."""
        state: AtlasAgentState = {
            "messages": [],
            "queries_executed": 0,
            "last_error": "",
            "retry_count": 0,
        }
        assert isinstance(state, dict)

    def test_annotations_present(self):
        """The messages field should have the add_messages annotation."""
        annotations = AtlasAgentState.__annotations__
        assert "messages" in annotations
        assert "queries_executed" in annotations
        assert "last_error" in annotations
        assert "retry_count" in annotations
        # Pipeline intermediate state fields
        assert "pipeline_question" in annotations
        assert "pipeline_products" in annotations
        assert "pipeline_codes" in annotations
        assert "pipeline_table_info" in annotations
        assert "pipeline_sql" in annotations
        assert "pipeline_result" in annotations
