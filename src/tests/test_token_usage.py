"""Unit tests for src/token_usage.py.

Tests the core math and data transforms: cost estimation, aggregation,
model name lookup, and per-step timing. No mocks, no DB, no LLM.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.model_config import DEFAULT_PRICING, MODEL_PRICING
from src.token_usage import (
    _lookup_pricing,
    aggregate_timing,
    aggregate_usage,
    count_tool_calls,
    estimate_cost,
    make_timing_record,
    make_usage_record,
    node_timer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    node: str = "test",
    pipeline: str = "query_tool",
    model: str = "gpt-5.2",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> dict:
    """Build a usage record for testing."""
    details = None
    if cache_read or cache_creation:
        details = {"cache_read": cache_read, "cache_creation": cache_creation}
    return make_usage_record(
        node,
        pipeline,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        model_name=model,
        input_token_details=details,
    )


# ---------------------------------------------------------------------------
# Tests: estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_cache_aware_cost(self):
        """Cache-aware formula: fresh input, cache_read, cache_creation each priced differently."""
        # gpt-5.2: input=1.75, output=14.00, cache_read=0.175, cache_creation=1.75
        rec = _record(
            model="gpt-5.2",
            input_tokens=1000,
            output_tokens=200,
            cache_read=800,
            cache_creation=100,
        )
        result = estimate_cost([rec])

        # fresh = 1000 - 800 - 100 = 100
        pricing = MODEL_PRICING["gpt-5.2"]
        expected_input_cost = (
            100 * pricing.input
            + 800 * pricing.cache_read
            + 100 * pricing.cache_creation
        )
        expected_output_cost = 200 * pricing.output
        expected = (expected_input_cost + expected_output_cost) / 1_000_000

        assert result["total_cost_usd"] == pytest.approx(expected, abs=1e-8)
        assert result["record_count"] == 1

    def test_no_cache_details_fallback(self):
        """Without input_token_details, all input tokens priced at standard rate."""
        rec = _record(
            model="gpt-5.2",
            input_tokens=1000,
            output_tokens=200,
        )
        result = estimate_cost([rec])

        pricing = MODEL_PRICING["gpt-5.2"]
        expected = (1000 * pricing.input + 200 * pricing.output) / 1_000_000

        assert result["total_cost_usd"] == pytest.approx(expected, abs=1e-8)

    def test_unknown_model_uses_default(self):
        """A model not in MODEL_PRICING should use DEFAULT_PRICING, not crash."""
        rec = _record(
            model="unknown-model-v99",
            input_tokens=500,
            output_tokens=100,
        )
        result = estimate_cost([rec])

        expected = (
            500 * DEFAULT_PRICING.input + 100 * DEFAULT_PRICING.output
        ) / 1_000_000
        assert result["total_cost_usd"] == pytest.approx(expected, abs=1e-8)
        assert result["total_cost_usd"] > 0

    def test_empty_records(self):
        """Empty list should return zero cost."""
        result = estimate_cost([])
        assert result["total_cost_usd"] == 0
        assert result["record_count"] == 0

    def test_by_pipeline_breakdown(self):
        """Cost should be broken down by pipeline."""
        records = [
            _record(pipeline="query_tool", input_tokens=100, output_tokens=50),
            _record(pipeline="atlas_graphql", input_tokens=200, output_tokens=100),
            _record(pipeline="query_tool", input_tokens=300, output_tokens=150),
        ]
        result = estimate_cost(records)

        assert "query_tool" in result["by_pipeline"]
        assert "atlas_graphql" in result["by_pipeline"]
        assert (
            result["by_pipeline"]["query_tool"] > result["by_pipeline"]["atlas_graphql"]
        )


# ---------------------------------------------------------------------------
# Tests: model name matching
# ---------------------------------------------------------------------------


class TestModelNameMatching:
    def test_exact_match(self):
        """Exact model name should match directly."""
        pricing = _lookup_pricing("gpt-5.2")
        assert pricing == MODEL_PRICING["gpt-5.2"]

    def test_date_suffix_stripped(self):
        """Model names with date suffixes should match the base name."""
        pricing = _lookup_pricing("gpt-5.2-2025-12-19")
        assert pricing == MODEL_PRICING["gpt-5.2"]

    def test_unknown_model_returns_default(self):
        """Unknown model should return DEFAULT_PRICING."""
        pricing = _lookup_pricing("totally-unknown-model")
        assert pricing == DEFAULT_PRICING

    def test_anthropic_model_match(self):
        """Anthropic model names should match."""
        pricing = _lookup_pricing("claude-haiku-4-5-20251001")
        assert pricing == MODEL_PRICING["claude-haiku-4-5-20251001"]

    def test_empty_string_returns_default(self):
        """Empty model name should return default."""
        pricing = _lookup_pricing("")
        assert pricing == DEFAULT_PRICING


# ---------------------------------------------------------------------------
# Tests: aggregate_usage
# ---------------------------------------------------------------------------


class TestAggregateUsage:
    def test_groups_by_pipeline(self):
        """Records from different pipelines should be grouped correctly."""
        records = [
            _record(pipeline="agent", input_tokens=100, output_tokens=50),
            _record(pipeline="query_tool", input_tokens=200, output_tokens=100),
            _record(pipeline="agent", input_tokens=150, output_tokens=75),
            _record(pipeline="atlas_graphql", input_tokens=300, output_tokens=200),
        ]
        result = aggregate_usage(records)

        assert result["total"]["input_tokens"] == 750
        assert result["total"]["output_tokens"] == 425
        assert result["total"]["call_count"] == 4

        by_pipe = result["by_pipeline"]
        assert by_pipe["agent"]["input_tokens"] == 250
        assert by_pipe["agent"]["call_count"] == 2
        assert by_pipe["query_tool"]["input_tokens"] == 200
        assert by_pipe["atlas_graphql"]["input_tokens"] == 300

    def test_empty_records(self):
        """Empty list should return zero totals."""
        result = aggregate_usage([])
        assert result["total"]["input_tokens"] == 0
        assert result["total"]["call_count"] == 0
        assert result["by_pipeline"] == {}


# ---------------------------------------------------------------------------
# Tests: count_tool_calls
# ---------------------------------------------------------------------------


class TestCountToolCalls:
    def test_counts_tool_messages(self):
        """ToolMessages should be counted by name."""
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        messages = [
            HumanMessage(content="Q1"),
            AIMessage(
                content="", tool_calls=[{"id": "c1", "name": "query_tool", "args": {}}]
            ),
            ToolMessage(content="result1", tool_call_id="c1", name="query_tool"),
            AIMessage(
                content="",
                tool_calls=[{"id": "c2", "name": "atlas_graphql", "args": {}}],
            ),
            ToolMessage(content="result2", tool_call_id="c2", name="atlas_graphql"),
            AIMessage(
                content="", tool_calls=[{"id": "c3", "name": "query_tool", "args": {}}]
            ),
            ToolMessage(content="result3", tool_call_id="c3", name="query_tool"),
            AIMessage(content="Final answer"),
        ]
        result = count_tool_calls(messages)

        assert result["query_tool"] == 2
        assert result["atlas_graphql"] == 1

    def test_empty_messages(self):
        """Empty message list should return empty counts."""
        result = count_tool_calls([])
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: make_timing_record
# ---------------------------------------------------------------------------


class TestMakeTimingRecord:
    def test_overhead_computed_correctly(self):
        """Overhead = wall_time - llm_time - io_time."""
        rec = make_timing_record(
            "generate_sql",
            "query_tool",
            wall_time_ms=1000.0,
            llm_time_ms=600.0,
            io_time_ms=200.0,
        )
        assert rec["node"] == "generate_sql"
        assert rec["tool_pipeline"] == "query_tool"
        assert rec["wall_time_ms"] == 1000.0
        assert rec["llm_time_ms"] == 600.0
        assert rec["io_time_ms"] == 200.0
        assert rec["overhead_ms"] == 200.0

    def test_defaults_to_zero_sub_timings(self):
        """Without sub-timings, overhead equals wall time."""
        rec = make_timing_record("agent", "agent", wall_time_ms=500.0)
        assert rec["llm_time_ms"] == 0.0
        assert rec["io_time_ms"] == 0.0
        assert rec["overhead_ms"] == 500.0

    def test_overhead_never_negative(self):
        """If sub-timings exceed wall time (unlikely), overhead should be 0."""
        rec = make_timing_record(
            "test", "test", wall_time_ms=100.0, llm_time_ms=80.0, io_time_ms=50.0
        )
        assert rec["overhead_ms"] == 0.0


# ---------------------------------------------------------------------------
# Tests: node_timer
# ---------------------------------------------------------------------------


class TestNodeTimer:
    def test_captures_wall_time(self):
        """node_timer should measure wall-clock time."""

        async def _run():
            async with node_timer("agent", "agent") as t:
                await asyncio.sleep(0.05)
            return t.record

        rec = asyncio.run(_run())
        assert rec["node"] == "agent"
        assert rec["tool_pipeline"] == "agent"
        assert rec["wall_time_ms"] >= 40  # at least ~50ms minus scheduling jitter

    def test_marks_llm_and_io(self):
        """mark_llm and mark_io should accumulate sub-timings."""

        async def _run():
            async with node_timer("generate_sql", "query_tool") as t:
                llm_start = time.monotonic()
                await asyncio.sleep(0.02)
                t.mark_llm(llm_start, time.monotonic())

                io_start = time.monotonic()
                await asyncio.sleep(0.01)
                t.mark_io(io_start, time.monotonic())
            return t.record

        rec = asyncio.run(_run())
        assert rec["llm_time_ms"] >= 15  # ~20ms
        assert rec["io_time_ms"] >= 5  # ~10ms
        assert rec["wall_time_ms"] >= rec["llm_time_ms"] + rec["io_time_ms"]
        assert rec["overhead_ms"] >= 0


# ---------------------------------------------------------------------------
# Tests: aggregate_timing
# ---------------------------------------------------------------------------


class TestAggregateTiming:
    def test_groups_by_node(self):
        """Records from different nodes should be grouped correctly."""
        records = [
            make_timing_record("agent", "agent", wall_time_ms=100.0, llm_time_ms=80.0),
            make_timing_record(
                "generate_sql", "query_tool", wall_time_ms=500.0, llm_time_ms=400.0
            ),
            make_timing_record(
                "execute_sql", "query_tool", wall_time_ms=200.0, io_time_ms=180.0
            ),
        ]
        result = aggregate_timing(records)

        assert "agent" in result["by_node"]
        assert "generate_sql" in result["by_node"]
        assert "execute_sql" in result["by_node"]
        assert result["by_node"]["agent"]["wall_time_ms"] == 100.0
        assert result["by_node"]["generate_sql"]["llm_time_ms"] == 400.0

    def test_groups_by_pipeline(self):
        """Records from same pipeline should be aggregated."""
        records = [
            make_timing_record("extract_products", "query_tool", wall_time_ms=100.0),
            make_timing_record("generate_sql", "query_tool", wall_time_ms=500.0),
        ]
        result = aggregate_timing(records)

        assert result["by_pipeline"]["query_tool"]["wall_time_ms"] == 600.0
        assert result["by_pipeline"]["query_tool"]["call_count"] == 2

    def test_identifies_slowest_node(self):
        """Should identify the node with the highest wall_time_ms."""
        records = [
            make_timing_record("agent", "agent", wall_time_ms=100.0),
            make_timing_record("generate_sql", "query_tool", wall_time_ms=500.0),
            make_timing_record("execute_sql", "query_tool", wall_time_ms=200.0),
        ]
        result = aggregate_timing(records)

        assert result["slowest_node"]["node"] == "generate_sql"
        assert result["slowest_node"]["wall_time_ms"] == 500.0

    def test_total_sums(self):
        """Total should sum all records."""
        records = [
            make_timing_record(
                "a", "p1", wall_time_ms=100.0, llm_time_ms=50.0, io_time_ms=30.0
            ),
            make_timing_record(
                "b", "p2", wall_time_ms=200.0, llm_time_ms=100.0, io_time_ms=60.0
            ),
        ]
        result = aggregate_timing(records)

        assert result["total"]["wall_time_ms"] == 300.0
        assert result["total"]["llm_time_ms"] == 150.0
        assert result["total"]["io_time_ms"] == 90.0

    def test_empty_records(self):
        """Empty list should return zero totals and no slowest node."""
        result = aggregate_timing([])
        assert result["total"]["wall_time_ms"] == 0.0
        assert result["slowest_node"] is None
        assert result["by_node"] == {}
        assert result["by_pipeline"] == {}


# ---------------------------------------------------------------------------
# Tests: add_step_timing reducer
# ---------------------------------------------------------------------------


class TestStepTimingReducer:
    """Tests for the state reducer that accumulates timing records across nodes.

    This is the critical accumulation mechanism — if the reducer is broken,
    all per-node timing data is silently lost even though individual nodes
    produce correct records.
    """

    def test_accumulates_records_across_nodes(self):
        """Timing records from separate nodes should concatenate in order."""
        from src.state import add_step_timing

        first = [make_timing_record("agent", "agent", wall_time_ms=100.0)]
        second = [make_timing_record("generate_sql", "query_tool", wall_time_ms=500.0)]
        third = [make_timing_record("execute_sql", "query_tool", wall_time_ms=200.0)]

        accumulated = add_step_timing(None, first)
        accumulated = add_step_timing(accumulated, second)
        accumulated = add_step_timing(accumulated, third)

        assert len(accumulated) == 3
        assert accumulated[0]["node"] == "agent"
        assert accumulated[1]["node"] == "generate_sql"
        assert accumulated[2]["node"] == "execute_sql"

    def test_handles_none_inputs(self):
        """Reducer should handle None for both existing and new."""
        from src.state import add_step_timing

        assert add_step_timing(None, None) == []
        assert add_step_timing([], None) == []
        rec = [make_timing_record("agent", "agent", wall_time_ms=100.0)]
        assert add_step_timing(None, rec) == rec


# ---------------------------------------------------------------------------
# Tests: node_timer exception safety
# ---------------------------------------------------------------------------


class TestNodeTimerExceptionSafety:
    """Verify that node_timer still produces a valid record when the
    wrapped code raises an exception — this is the production path for
    any node that fails (timeout, bad SQL, API error, etc.).
    """

    def test_record_available_after_exception(self):
        """If the wrapped code raises, the timing builder should still
        produce a valid record with the wall time up to the exception."""

        async def _run():
            builder = None
            try:
                async with node_timer("execute_sql", "query_tool") as t:
                    builder = t
                    await asyncio.sleep(0.02)
                    raise RuntimeError("DB connection lost")
            except RuntimeError:
                pass
            return builder.record

        rec = asyncio.run(_run())
        assert rec["node"] == "execute_sql"
        assert rec["wall_time_ms"] >= 15  # at least ~20ms minus jitter
        assert rec["llm_time_ms"] == 0.0
        assert rec["overhead_ms"] >= 0


# ---------------------------------------------------------------------------
# Tests: pipeline nodes return step_timing
# ---------------------------------------------------------------------------


class TestPipelineNodesReturnStepTiming:
    """Verify that the actual pipeline node functions return a 'step_timing'
    key in their result dict. These are regression guards — if someone
    refactors a node and drops the timing, the node becomes invisible
    in timing reports.
    """

    def test_extract_tool_question_returns_timing(self):
        """extract_tool_question should include step_timing in its result."""
        from src.sql_pipeline import extract_tool_question
        from langchain_core.messages import AIMessage

        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "query_tool",
                            "args": {"question": "test?"},
                        },
                    ],
                )
            ],
        }
        result = asyncio.run(extract_tool_question(state))

        assert "step_timing" in result
        assert len(result["step_timing"]) == 1
        rec = result["step_timing"][0]
        assert rec["node"] == "extract_tool_question"
        assert rec["tool_pipeline"] == "query_tool"
        assert rec["wall_time_ms"] >= 0

    def test_format_results_returns_timing(self):
        """format_results_node should include step_timing in its result."""
        from langchain_core.messages import AIMessage
        from src.sql_pipeline import format_results_node

        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "c1",
                            "name": "query_tool",
                            "args": {"question": "test?"},
                        },
                    ],
                )
            ],
            "pipeline_question": "test?",
            "pipeline_result_columns": ["country", "value"],
            "pipeline_result_rows": [["Brazil", 100]],
            "pipeline_execution_time_ms": 50,
            "pipeline_sql": "SELECT country FROM t",
        }
        result = asyncio.run(format_results_node(state))

        assert "step_timing" in result
        rec = result["step_timing"][0]
        assert rec["node"] == "format_results"
        assert rec["tool_pipeline"] == "query_tool"
