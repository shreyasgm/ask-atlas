"""Tests for the GraphQL correction sub-agent (graphql_subagent.py).

Tests the sub-agent's tool nodes, routing, assessment gate, wrapper node,
serialization, and initial context building.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from unittest.mock import AsyncMock, MagicMock

from src.graphql_pipeline import (
    ResultAssessment,
    TECHFRONTIER_COUNTRIES,
    _get_root_data_list,
    assess_graphql_result,
    route_after_assessment,
)
from src.graphql_subagent import (
    FREEFORM_RESPONSE_TRUNCATION_THRESHOLD,
    INITIAL_CONTEXT_RESPONSE_THRESHOLD,
    _build_initial_message,
    _serialize_graphql_subagent_messages,
    execute_freeform_tool_node,
    execute_template_tool_node,
    explore_catalog_node,
    graphql_correction_agent_node,
    introspect_schema_node,
    report_results_node,
    route_after_reasoning,
    route_after_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_call(name: str, args: dict, call_id: str = "tc-1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _base_subagent_state(**overrides) -> dict:
    state: dict = {
        "question": "What did Kenya export in 2024?",
        "context": "",
        "original_query_type": "treemap_products",
        "original_classification": {
            "query_type": "treemap_products",
            "api_target": "explore",
        },
        "original_resolved_params": {"country_id": 404, "product_class": "HS12"},
        "original_query_string": "query CPY { ... }",
        "original_response_sample": "{}",
        "assessment_verdict": "fail",
        "assessment_reasoning": "empty results",
        "messages": [],
        "current_query_type": "treemap_products",
        "current_resolved_params": {"country_id": 404},
        "current_raw_response": None,
        "current_query_string": "",
        "execution_time_ms": 0,
        "iteration_count": 0,
        "last_error": "",
        "used_freeform_final": False,
        "attempt_history": [],
    }
    state.update(overrides)
    return state


def _base_parent_state(**overrides) -> dict:
    state: dict = {
        "messages": [
            HumanMessage(content="What did Kenya export?"),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("atlas_graphql", {"question": "What did Kenya export?"})
                ],
            ),
        ],
        "queries_executed": 0,
        "last_error": "",
        "retry_count": 0,
        "pipeline_question": "",
        "pipeline_context": "",
        "pipeline_products": None,
        "pipeline_codes": "",
        "pipeline_table_info": "",
        "pipeline_sql": "",
        "pipeline_result": "",
        "pipeline_result_columns": [],
        "pipeline_result_rows": [],
        "pipeline_execution_time_ms": 0,
        "pipeline_assessment": "",
        "pipeline_surface_to_agent": False,
        "turn_summaries": [],
        "token_usage": [],
        "step_timing": [],
        "pipeline_sql_history": [],
        "pipeline_reasoning_trace": [],
        "override_schema": None,
        "override_direction": None,
        "override_mode": None,
        "override_agent_mode": None,
        "graphql_question": "What did Kenya export?",
        "graphql_context": "",
        "graphql_classification": {
            "query_type": "treemap_products",
            "api_target": "explore",
        },
        "graphql_entity_extraction": {"country_name": "Kenya", "product_class": "HS12"},
        "graphql_resolved_params": {"country_id": 404, "product_class": "HS12"},
        "graphql_query": "query CPY { ... }",
        "graphql_api_target": "explore",
        "graphql_raw_response": {"countryProductYear": []},
        "graphql_execution_time_ms": 150,
        "graphql_atlas_links": [],
        "sql_call_history": [],
        "graphql_call_history": [],
        "graphql_assessment": "fail|empty_results|Query returned zero results",
        "graphql_surface_to_agent": False,
        "graphql_reasoning_trace": [],
        "docs_question": "",
        "docs_context": "",
        "docs_selected_files": [],
        "docs_synthesis": "",
    }
    state.update(overrides)
    return state


# ===================================================================
# Tool node tests
# ===================================================================


@pytest.mark.asyncio
async def test_execute_template_success():
    """Template tool executes a valid query and updates state."""
    mock_client = AsyncMock()
    mock_client.execute.return_value = {"countryProductYear": [{"exportValue": 1000}]}

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_template",
                        {
                            "reasoning": "Try country_year instead",
                            "query_type": "country_year",
                            "params": {
                                "country_id": 404,
                                "year_min": 2024,
                                "year_max": 2024,
                            },
                        },
                    )
                ],
            )
        ]
    )

    result = await execute_template_tool_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    assert result["current_query_type"] == "country_year"
    assert result["current_raw_response"] == {
        "countryProductYear": [{"exportValue": 1000}]
    }
    assert result["last_error"] == ""
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], ToolMessage)
    assert result["used_freeform_final"] is False


@pytest.mark.asyncio
async def test_execute_template_bad_query_type():
    """Template tool returns error for unknown query type."""
    mock_client = AsyncMock()

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_template",
                        {
                            "reasoning": "test",
                            "query_type": "nonexistent_type",
                            "params": {},
                        },
                    )
                ],
            )
        ]
    )

    result = await execute_template_tool_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    assert "Invalid query type" in result["messages"][0].content
    assert result["last_error"]
    mock_client.execute.assert_not_called()


@pytest.mark.asyncio
async def test_execute_template_budget_exhausted():
    """Template tool catches BudgetExhaustedError."""
    from src.graphql_client import BudgetExhaustedError

    mock_client = AsyncMock()
    mock_client.execute.side_effect = BudgetExhaustedError()

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_template",
                        {
                            "reasoning": "retry",
                            "query_type": "country_year",
                            "params": {"country_id": 404},
                        },
                    )
                ],
            )
        ]
    )

    result = await execute_template_tool_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    assert "budget exhausted" in result["messages"][0].content.lower()
    assert result["last_error"]


@pytest.mark.asyncio
async def test_execute_freeform_success():
    """Freeform tool executes raw query successfully."""
    mock_client = AsyncMock()
    mock_client.execute.return_value = {"countryYear": [{"gdp": 95000000000}]}

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_freeform",
                        {
                            "reasoning": "probe",
                            "query": "{ countryYear(countryId: 404) { gdp } }",
                            "api_target": "explore",
                        },
                    )
                ],
            )
        ]
    )

    result = await execute_freeform_tool_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    assert result["current_raw_response"] == {"countryYear": [{"gdp": 95000000000}]}
    assert result["used_freeform_final"] is True
    assert result["last_error"] == ""


@pytest.mark.asyncio
async def test_execute_freeform_truncation():
    """Freeform responses over threshold are truncated."""
    mock_client = AsyncMock()
    # Create a response that serializes to > FREEFORM_RESPONSE_TRUNCATION_THRESHOLD chars
    big_data = {"data": "x" * (FREEFORM_RESPONSE_TRUNCATION_THRESHOLD + 1000)}
    mock_client.execute.return_value = big_data

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_freeform",
                        {
                            "reasoning": "probe",
                            "query": "{ big }",
                            "api_target": "explore",
                        },
                    )
                ],
            )
        ]
    )

    result = await execute_freeform_tool_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    content = result["messages"][0].content
    assert "[Truncated:" in content
    # Content should be capped around FREEFORM_TRUNCATED_RETURN_SIZE
    assert len(content) < FREEFORM_RESPONSE_TRUNCATION_THRESHOLD


@pytest.mark.asyncio
async def test_explore_catalog_by_code():
    """Catalog lookup finds entry by exact code."""
    mock_cache = AsyncMock()
    mock_cache.lookup = AsyncMock(
        return_value={
            "nameShortEn": "Kenya",
            "code": "KEN",
            "id": "404",
        }
    )
    mock_cache.search = AsyncMock(return_value=[])

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "explore_catalog",
                        {"catalog": "country", "search_term": "KEN"},
                    )
                ],
            )
        ]
    )

    result = await explore_catalog_node(
        state,
        country_cache=mock_cache,
        product_caches={},
        services_cache=None,
        group_cache=None,
    )

    assert "Kenya" in result["messages"][0].content
    mock_cache.lookup.assert_called_once_with("code", "KEN")


@pytest.mark.asyncio
async def test_explore_catalog_by_name():
    """Catalog lookup falls back to name search."""
    mock_cache = AsyncMock()
    mock_cache.lookup = AsyncMock(return_value=None)
    mock_cache.search = AsyncMock(
        return_value=[
            {"nameShortEn": "Kenya", "code": "KEN", "id": "404"},
            {"nameShortEn": "Kentucky", "code": "KY", "id": "999"},
        ]
    )

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "explore_catalog",
                        {"catalog": "country", "search_term": "Ken"},
                    )
                ],
            )
        ]
    )

    result = await explore_catalog_node(
        state,
        country_cache=mock_cache,
        product_caches={},
        services_cache=None,
        group_cache=None,
    )

    assert "2 match" in result["messages"][0].content
    mock_cache.search.assert_called_once_with("nameShortEn", "Ken", limit=20)


@pytest.mark.asyncio
async def test_explore_catalog_no_match():
    """Catalog lookup returns graceful message when nothing found."""
    mock_cache = AsyncMock()
    mock_cache.lookup = AsyncMock(return_value=None)
    mock_cache.search = AsyncMock(return_value=[])

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "explore_catalog",
                        {"catalog": "country", "search_term": "Atlantis"},
                    )
                ],
            )
        ]
    )

    result = await explore_catalog_node(
        state,
        country_cache=mock_cache,
        product_caches={},
        services_cache=None,
        group_cache=None,
    )

    assert "No matches" in result["messages"][0].content


@pytest.mark.asyncio
async def test_introspect_schema_targeted():
    """Introspect schema returns type info."""
    mock_client = AsyncMock()
    mock_client.execute.return_value = {
        "__type": {
            "fields": [
                {
                    "name": "exportValue",
                    "type": {"name": "Float", "kind": "SCALAR", "ofType": None},
                },
            ]
        }
    }

    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "introspect_schema",
                        {"type_name": "CountryYear", "api_target": "explore"},
                    )
                ],
            )
        ]
    )

    result = await introspect_schema_node(
        state, graphql_client=mock_client, country_pages_client=None
    )

    assert "exportValue" in result["messages"][0].content


@pytest.mark.asyncio
async def test_report_results_terminal():
    """Report results with needs_verification=False is terminal."""
    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "report_results",
                        {
                            "assessment": "Fixed: used country_year instead",
                            "surface_to_agent": False,
                            "used_freeform_final": False,
                            "needs_verification": False,
                        },
                    )
                ],
            )
        ]
    )

    result = await report_results_node(state)

    assert "Results reported" in result["messages"][0].content


@pytest.mark.asyncio
async def test_report_results_bounce_back():
    """Report results with needs_verification=True bounces back."""
    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "report_results",
                        {
                            "assessment": "Need to verify year",
                            "surface_to_agent": False,
                            "used_freeform_final": False,
                            "needs_verification": True,
                        },
                    )
                ],
            )
        ]
    )

    result = await report_results_node(state)

    assert "verification" in result["messages"][0].content.lower()


# ===================================================================
# Routing tests
# ===================================================================


def test_route_after_reasoning_all_tools():
    """Each tool name dispatches to the correct node."""
    for tool_name in [
        "execute_graphql_template",
        "execute_graphql_freeform",
        "explore_catalog",
        "introspect_schema",
        "report_results",
    ]:
        state = _base_subagent_state(
            messages=[AIMessage(content="", tool_calls=[_tool_call(tool_name, {})])]
        )
        assert route_after_reasoning(state) == tool_name


def test_route_after_reasoning_no_tool_calls():
    """Graceful END when no tool calls."""
    state = _base_subagent_state(messages=[AIMessage(content="I'm done")])
    assert route_after_reasoning(state) == "__end__"


def test_route_after_report_verification():
    """needs_verification=True routes back to reasoning."""
    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "report_results",
                        {
                            "assessment": "test",
                            "needs_verification": True,
                            "surface_to_agent": False,
                            "used_freeform_final": False,
                        },
                    )
                ],
            ),
            ToolMessage(content="verify", tool_call_id="tc-1", name="report_results"),
        ]
    )
    assert route_after_report(state) == "reasoning"


def test_route_after_report_terminal():
    """needs_verification=False routes to END."""
    state = _base_subagent_state(
        messages=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "report_results",
                        {
                            "assessment": "done",
                            "needs_verification": False,
                            "surface_to_agent": False,
                            "used_freeform_final": False,
                        },
                    )
                ],
            ),
            ToolMessage(content="done", tool_call_id="tc-1", name="report_results"),
        ]
    )
    assert route_after_report(state) == "__end__"


# ===================================================================
# Assessment node tests
# ===================================================================


@pytest.mark.asyncio
async def test_tier1_api_error():
    """API error response → fail assessment."""
    state = _base_parent_state(
        graphql_raw_response={"error": "graphql_error", "detail": "bad query"},
    )

    result = await assess_graphql_result(state, lightweight_model=MagicMock())

    assessment = result["graphql_assessment"]
    assert assessment.startswith("fail|api_error")


@pytest.mark.asyncio
async def test_tier1_empty_results():
    """Empty root list → fail with empty_results."""
    state = _base_parent_state(
        graphql_raw_response={"countryProductYear": []},
        graphql_classification={
            "query_type": "treemap_products",
            "api_target": "explore",
        },
        graphql_resolved_params={"country_id": 404},
    )

    result = await assess_graphql_result(state, lightweight_model=MagicMock())

    assert result["graphql_assessment"].startswith("fail|empty_results")


@pytest.mark.asyncio
async def test_tier1_techfrontier_empty():
    """Empty feasibility for TechFrontier country → tier 2 (LLM called)."""
    mock_llm = MagicMock()
    structured_llm = AsyncMock()
    structured_llm.ainvoke.return_value = ResultAssessment(
        verdict="pass", failure_type=None, reasoning="Expected for advanced economy"
    )
    mock_llm.with_structured_output.return_value = structured_llm

    us_id = 840  # USA is in TECHFRONTIER_COUNTRIES
    assert us_id in TECHFRONTIER_COUNTRIES

    state = _base_parent_state(
        graphql_raw_response={"countryProductYear": []},
        graphql_classification={"query_type": "feasibility", "api_target": "explore"},
        graphql_resolved_params={"country_id": us_id},
        graphql_question="What growth opportunities does the US have?",
    )

    result = await assess_graphql_result(state, lightweight_model=mock_llm)

    # LLM should have been called (tier 2)
    mock_llm.with_structured_output.assert_called_once()
    assert result["graphql_assessment"].startswith("pass")


@pytest.mark.asyncio
async def test_tier1_coverage_gap():
    """Country Pages with HS12 → fail with coverage_gap."""
    state = _base_parent_state(
        graphql_raw_response={"countryProfile": [{"eci": 0.5}]},
        graphql_classification={
            "query_type": "country_profile",
            "api_target": "country_pages",
        },
        graphql_entity_extraction={"product_class": "HS12"},
        graphql_resolved_params={"location": "location-404"},
    )

    result = await assess_graphql_result(state, lightweight_model=MagicMock())

    assert "coverage_gap" in result["graphql_assessment"]


@pytest.mark.asyncio
async def test_tier1_pass():
    """Valid non-empty response → pass (skip LLM)."""
    mock_llm = MagicMock()

    state = _base_parent_state(
        graphql_raw_response={
            "countryProductYear": [{"exportValue": 1000, "productId": 1}]
        },
        graphql_classification={
            "query_type": "treemap_products",
            "api_target": "explore",
        },
        graphql_resolved_params={"country_id": 404},
    )

    result = await assess_graphql_result(state, lightweight_model=mock_llm)

    assert result["graphql_assessment"].startswith("pass")
    # LLM should NOT have been called
    mock_llm.with_structured_output.assert_not_called()


@pytest.mark.asyncio
async def test_tier1_null_response():
    """None response → fail with api_error."""
    state = _base_parent_state(graphql_raw_response=None)

    result = await assess_graphql_result(state, lightweight_model=MagicMock())

    assert result["graphql_assessment"].startswith("fail|api_error")


# ===================================================================
# Assessment routing tests
# ===================================================================


def test_route_after_assessment_pass():
    state = _base_parent_state(graphql_assessment="pass|None|All good")
    assert route_after_assessment(state) == "format_graphql_results"


def test_route_after_assessment_fail():
    state = _base_parent_state(graphql_assessment="fail|empty_results|No data")
    assert route_after_assessment(state) == "graphql_correction_agent"


def test_route_after_assessment_suspicious():
    state = _base_parent_state(graphql_assessment="suspicious|None|Ambiguous")
    assert route_after_assessment(state) == "graphql_correction_agent"


# ===================================================================
# Wrapper node test
# ===================================================================


@pytest.mark.asyncio
async def test_correction_agent_node_maps_state():
    """Wrapper node maps sub-agent results back to parent state correctly."""
    mock_subagent = AsyncMock()
    mock_subagent.ainvoke.return_value = {
        "messages": [
            HumanMessage(content="context"),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "execute_graphql_template",
                        {
                            "reasoning": "fixed",
                            "query_type": "country_year",
                            "params": {"country_id": 404},
                        },
                    )
                ],
            ),
            ToolMessage(
                content="data", tool_call_id="tc-1", name="execute_graphql_template"
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "report_results",
                        {
                            "assessment": "Fixed query type",
                            "final_query_type": "country_year",
                            "final_resolved_params": {
                                "country_id": 404,
                                "year_min": 2024,
                            },
                            "surface_to_agent": True,
                            "used_freeform_final": False,
                            "needs_verification": False,
                        },
                        call_id="tc-2",
                    )
                ],
            ),
            ToolMessage(content="done", tool_call_id="tc-2", name="report_results"),
        ],
        "current_query_type": "country_year",
        "current_resolved_params": {"country_id": 404, "year_min": 2024},
        "current_raw_response": {"countryYear": [{"exportValue": 5e9}]},
        "current_query_string": "query CY { ... }",
    }

    state = _base_parent_state()

    result = await graphql_correction_agent_node(state, subagent=mock_subagent)

    # Classification should be updated
    assert result["graphql_classification"]["query_type"] == "country_year"
    # Resolved params should be updated
    assert result["graphql_resolved_params"] == {"country_id": 404, "year_min": 2024}
    # Raw response should be updated
    assert result["graphql_raw_response"] == {"countryYear": [{"exportValue": 5e9}]}
    # Query string should be updated
    assert result["graphql_query"] == "query CY { ... }"
    # Assessment should come from report_results
    assert result["graphql_assessment"] == "Fixed query type"
    assert result["graphql_surface_to_agent"] is True
    # Reasoning trace should be populated
    assert len(result["graphql_reasoning_trace"]) == 1
    # Should NOT contain messages key (critical for format_graphql_results)
    assert "messages" not in result


# ===================================================================
# Serialization tests
# ===================================================================


def test_serialize_skips_human_message():
    """HumanMessages are filtered from the trace."""
    messages = [
        HumanMessage(content="big context dump"),
        AIMessage(content="thinking..."),
        ToolMessage(content="result", tool_call_id="tc-1"),
    ]

    trace = _serialize_graphql_subagent_messages(messages)

    roles = [entry["role"] for entry in trace]
    assert "user" not in roles
    assert len(trace) == 2
    assert trace[0]["role"] == "assistant"
    assert trace[1]["role"] == "tool"


def test_serialize_truncates_large_content():
    """ToolMessage content > 2000 chars is truncated."""
    large_content = "x" * 5000
    messages = [
        ToolMessage(
            content=large_content, tool_call_id="tc-1", name="execute_graphql_freeform"
        ),
    ]

    trace = _serialize_graphql_subagent_messages(messages)

    assert len(trace[0]["content"]) < 2100  # 2000 + truncation notice
    assert "[truncated from" in trace[0]["content"]


# ===================================================================
# Initial context tests
# ===================================================================


def test_initial_message_small_response():
    """Small raw response is included in full."""
    small_response = {"countryYear": [{"gdp": 1e10}]}

    msg = _build_initial_message(
        question="Test?",
        context="",
        original_query_type="country_year",
        original_classification=None,
        original_resolved_params=None,
        original_query_string="",
        original_raw_response=small_response,
        assessment_reasoning="fail: empty_results",
    )

    response_str = json.dumps(small_response, default=str)
    assert response_str in msg.content


def test_initial_message_large_response():
    """Large raw response is sampled."""
    # Create response > INITIAL_CONTEXT_RESPONSE_THRESHOLD
    large_response = {"data": "x" * INITIAL_CONTEXT_RESPONSE_THRESHOLD}

    msg = _build_initial_message(
        question="Test?",
        context="",
        original_query_type="treemap_products",
        original_classification=None,
        original_resolved_params=None,
        original_query_string="",
        original_raw_response=large_response,
        assessment_reasoning="fail: data_shape_mismatch",
    )

    assert "sample" in msg.content.lower()
    # Full response should NOT be in the message
    full_str = json.dumps(large_response, default=str)
    assert full_str not in msg.content


# ===================================================================
# Helper tests
# ===================================================================


def test_get_root_data_list_flat():
    """Extracts list from flat response."""
    assert _get_root_data_list({"countryYear": [1, 2, 3]}) == [1, 2, 3]


def test_get_root_data_list_nested():
    """Extracts list from nested response."""
    assert _get_root_data_list({"data": {"countryYear": [1]}}) == [1]


def test_get_root_data_list_none():
    """Returns None for non-dict input."""
    assert _get_root_data_list(None) is None


def test_get_root_data_list_no_list():
    """Returns None when no list value exists."""
    assert _get_root_data_list({"scalar": 42}) is None
