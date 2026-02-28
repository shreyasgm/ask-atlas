"""Unit tests for src/token_usage.py.

Tests the core math and data transforms: cost estimation, aggregation,
model name lookup. No mocks, no DB, no LLM.
"""

from __future__ import annotations

import pytest

from src.model_config import DEFAULT_PRICING, MODEL_PRICING
from src.token_usage import (
    _lookup_pricing,
    aggregate_usage,
    count_tool_calls,
    estimate_cost,
    make_usage_record,
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
