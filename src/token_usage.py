"""Token usage tracking, cost estimation, and per-step timing for the Atlas agent.

Provides data structures and helpers for recording LLM token usage across
pipeline nodes, aggregating by tool pipeline, estimating costs using
a cache-aware pricing model, and tracking wall-clock / LLM / I/O time
per graph node.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from src.model_config import DEFAULT_PRICING, MODEL_PRICING, ModelPricing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias (plain dict — cheaper than dataclass for state accumulation)
# ---------------------------------------------------------------------------

# UsageRecord keys:
#   node: str             — graph node name (e.g. "agent", "generate_sql")
#   tool_pipeline: str    — pipeline grouping (e.g. "agent", "query_tool", "atlas_graphql", "docs_tool")
#   model_name: str       — model identifier from response_metadata
#   input_tokens: int
#   output_tokens: int
#   total_tokens: int
#   input_token_details: dict | None  — {cache_read: int, cache_creation: int}
#   output_token_details: dict | None — {reasoning: int}

UsageRecord = dict[str, Any]


# ---------------------------------------------------------------------------
# Model name → pricing lookup
# ---------------------------------------------------------------------------

# Date-suffix pattern: e.g. "gpt-5.2-2025-12-19" → "gpt-5.2"
_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def _lookup_pricing(model_name: str) -> ModelPricing:
    """Look up pricing for a model name, with date-suffix fallback.

    Args:
        model_name: Model identifier (may include date suffix).

    Returns:
        ModelPricing for the model, or DEFAULT_PRICING if unknown.
    """
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]
    # Strip date suffix and retry
    stripped = _DATE_SUFFIX_RE.sub("", model_name)
    if stripped != model_name and stripped in MODEL_PRICING:
        return MODEL_PRICING[stripped]
    return DEFAULT_PRICING


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def make_usage_record(
    node: str,
    tool_pipeline: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    model_name: str = "",
    input_token_details: dict | None = None,
    output_token_details: dict | None = None,
) -> UsageRecord:
    """Build a UsageRecord dict.

    Args:
        node: Graph node name.
        tool_pipeline: Pipeline grouping key.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        total_tokens: Total tokens (input + output).
        model_name: Model identifier.
        input_token_details: Optional cache breakdown.
        output_token_details: Optional reasoning breakdown.

    Returns:
        A UsageRecord dict.
    """
    return {
        "node": node,
        "tool_pipeline": tool_pipeline,
        "model_name": model_name,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens or (input_tokens + output_tokens),
        "input_token_details": input_token_details,
        "output_token_details": output_token_details,
    }


def extract_usage_from_ai_message(msg: AIMessage) -> dict[str, Any]:
    """Extract token usage from an AIMessage's usage_metadata.

    Args:
        msg: An AIMessage returned from an LLM call.

    Returns:
        Dict with input_tokens, output_tokens, total_tokens, model_name,
        and optionally input_token_details/output_token_details.
    """
    meta = getattr(msg, "usage_metadata", None) or {}
    resp_meta = getattr(msg, "response_metadata", None) or {}

    result: dict[str, Any] = {
        "input_tokens": meta.get("input_tokens", 0),
        "output_tokens": meta.get("output_tokens", 0),
        "total_tokens": meta.get("total_tokens", 0),
        "model_name": resp_meta.get("model_name", resp_meta.get("model", "")),
    }

    input_details = meta.get("input_token_details")
    if input_details:
        result["input_token_details"] = dict(input_details)

    output_details = meta.get("output_token_details")
    if output_details:
        result["output_token_details"] = dict(output_details)

    return result


def extract_usage_from_callback(handler: Any) -> dict[str, Any]:
    """Extract token usage from a UsageMetadataCallbackHandler.

    Args:
        handler: A langchain_core.callbacks.UsageMetadataCallbackHandler.

    Returns:
        Dict with input_tokens, output_tokens, total_tokens, model_name,
        and optionally input_token_details/output_token_details.
    """
    meta = getattr(handler, "total_usage", None) or {}
    if hasattr(meta, "__dict__"):
        meta = {k: v for k, v in meta.__dict__.items() if not k.startswith("_")}
    elif not isinstance(meta, dict):
        meta = dict(meta) if meta else {}

    result: dict[str, Any] = {
        "input_tokens": meta.get("input_tokens", 0),
        "output_tokens": meta.get("output_tokens", 0),
        "total_tokens": meta.get("total_tokens", 0),
        "model_name": "",
    }

    input_details = meta.get("input_token_details")
    if input_details:
        if hasattr(input_details, "__dict__"):
            result["input_token_details"] = {
                k: v for k, v in input_details.__dict__.items() if not k.startswith("_")
            }
        else:
            result["input_token_details"] = dict(input_details)

    output_details = meta.get("output_token_details")
    if output_details:
        if hasattr(output_details, "__dict__"):
            result["output_token_details"] = {
                k: v
                for k, v in output_details.__dict__.items()
                if not k.startswith("_")
            }
        else:
            result["output_token_details"] = dict(output_details)

    return result


def make_usage_record_from_msg(
    node: str,
    tool_pipeline: str,
    msg: AIMessage,
) -> UsageRecord:
    """Convenience: build a UsageRecord from an AIMessage.

    Args:
        node: Graph node name.
        tool_pipeline: Pipeline grouping key.
        msg: AIMessage with usage_metadata.

    Returns:
        A UsageRecord dict.
    """
    usage = extract_usage_from_ai_message(msg)
    return make_usage_record(
        node,
        tool_pipeline,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        model_name=usage["model_name"],
        input_token_details=usage.get("input_token_details"),
        output_token_details=usage.get("output_token_details"),
    )


def make_usage_record_from_callback(
    node: str,
    tool_pipeline: str,
    handler: Any,
) -> UsageRecord:
    """Convenience: build a UsageRecord from a callback handler.

    Args:
        node: Graph node name.
        tool_pipeline: Pipeline grouping key.
        handler: UsageMetadataCallbackHandler.

    Returns:
        A UsageRecord dict.
    """
    usage = extract_usage_from_callback(handler)
    return make_usage_record(
        node,
        tool_pipeline,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=usage["total_tokens"],
        model_name=usage["model_name"],
        input_token_details=usage.get("input_token_details"),
        output_token_details=usage.get("output_token_details"),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_usage(records: list[UsageRecord]) -> dict[str, Any]:
    """Aggregate token usage records by tool_pipeline.

    Args:
        records: List of UsageRecord dicts.

    Returns:
        Dict with ``by_pipeline`` (per-pipeline totals) and ``total``
        (grand totals for input_tokens, output_tokens, total_tokens).
    """
    by_pipeline: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "call_count": 0,
        }
    )
    grand = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "call_count": 0}

    for rec in records:
        pipeline = rec.get("tool_pipeline", "unknown")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            by_pipeline[pipeline][key] += rec.get(key, 0)
            grand[key] += rec.get(key, 0)
        by_pipeline[pipeline]["call_count"] += 1
        grand["call_count"] += 1

    return {
        "by_pipeline": dict(by_pipeline),
        "total": grand,
    }


def count_tool_calls(messages: list[Any]) -> dict[str, int]:
    """Count tool invocations from the message list.

    Counts ToolMessage instances grouped by ``.name``.

    Args:
        messages: List of LangChain messages from the agent state.

    Returns:
        Dict mapping tool name → invocation count.
    """
    counts: dict[str, int] = defaultdict(int)
    for msg in messages:
        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", None) or "unknown"
            counts[name] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def _estimate_single_record_cost(rec: UsageRecord) -> float:
    """Estimate cost in USD for a single usage record.

    Uses cache-aware pricing when input_token_details is available,
    otherwise falls back to simple input_tokens × input_price.

    Args:
        rec: A UsageRecord dict.

    Returns:
        Estimated cost in USD.
    """
    model_name = rec.get("model_name", "")
    pricing = _lookup_pricing(model_name) if model_name else DEFAULT_PRICING

    input_tokens = rec.get("input_tokens", 0)
    output_tokens = rec.get("output_tokens", 0)
    details = rec.get("input_token_details")

    if details:
        cache_read = details.get("cache_read", 0) or 0
        cache_creation = details.get("cache_creation", 0) or 0
        fresh_input = max(0, input_tokens - cache_read - cache_creation)

        input_cost = (
            fresh_input * pricing.input
            + cache_read * (pricing.cache_read or pricing.input)
            + cache_creation * (pricing.cache_creation or pricing.input)
        )
    else:
        input_cost = input_tokens * pricing.input

    output_cost = output_tokens * pricing.output
    return (input_cost + output_cost) / 1_000_000


def estimate_cost(records: list[UsageRecord]) -> dict[str, Any]:
    """Estimate total cost from a list of usage records.

    Args:
        records: List of UsageRecord dicts.

    Returns:
        Dict with ``by_pipeline`` (per-pipeline cost in USD),
        ``total_cost_usd`` (grand total), and ``record_count``.
    """
    by_pipeline: dict[str, float] = defaultdict(float)
    total = 0.0

    for rec in records:
        cost = _estimate_single_record_cost(rec)
        pipeline = rec.get("tool_pipeline", "unknown")
        by_pipeline[pipeline] += cost
        total += cost

    return {
        "by_pipeline": {k: round(v, 6) for k, v in by_pipeline.items()},
        "total_cost_usd": round(total, 6),
        "record_count": len(records),
    }


# ---------------------------------------------------------------------------
# Per-step timing
# ---------------------------------------------------------------------------

# TimingRecord keys:
#   node: str             — graph node name (e.g. "agent", "generate_sql")
#   tool_pipeline: str    — pipeline grouping (e.g. "agent", "query_tool")
#   wall_time_ms: float   — total elapsed time for the node
#   llm_time_ms: float    — time spent in LLM calls
#   io_time_ms: float     — time spent in DB / HTTP I/O
#   overhead_ms: float    — wall_time_ms - llm_time_ms - io_time_ms

TimingRecord = dict[str, Any]


def make_timing_record(
    node: str,
    tool_pipeline: str,
    *,
    wall_time_ms: float,
    llm_time_ms: float = 0.0,
    io_time_ms: float = 0.0,
) -> TimingRecord:
    """Build a TimingRecord dict.

    Args:
        node: Graph node name.
        tool_pipeline: Pipeline grouping key.
        wall_time_ms: Total wall-clock time in milliseconds.
        llm_time_ms: Time spent in LLM calls (ms).
        io_time_ms: Time spent in DB/HTTP I/O (ms).

    Returns:
        A TimingRecord dict with computed overhead.
    """
    overhead = max(0.0, wall_time_ms - llm_time_ms - io_time_ms)
    return {
        "node": node,
        "tool_pipeline": tool_pipeline,
        "wall_time_ms": round(wall_time_ms, 2),
        "llm_time_ms": round(llm_time_ms, 2),
        "io_time_ms": round(io_time_ms, 2),
        "overhead_ms": round(overhead, 2),
    }


class _TimingBuilder:
    """Accumulates LLM and I/O sub-timings within a node_timer block."""

    __slots__ = ("_node", "_pipeline", "_start", "_llm_ms", "_io_ms")

    def __init__(self, node: str, pipeline: str) -> None:
        self._node = node
        self._pipeline = pipeline
        self._start = time.monotonic()
        self._llm_ms = 0.0
        self._io_ms = 0.0

    def mark_llm(self, start: float, end: float) -> None:
        """Record an LLM call sub-timing.

        Args:
            start: monotonic timestamp when LLM call began.
            end: monotonic timestamp when LLM call completed.
        """
        self._llm_ms += (end - start) * 1000

    def mark_io(self, start: float, end: float) -> None:
        """Record a DB/HTTP I/O sub-timing.

        Args:
            start: monotonic timestamp when I/O began.
            end: monotonic timestamp when I/O completed.
        """
        self._io_ms += (end - start) * 1000

    @property
    def record(self) -> TimingRecord:
        """Build the final TimingRecord from accumulated data."""
        wall_ms = (time.monotonic() - self._start) * 1000
        return make_timing_record(
            self._node,
            self._pipeline,
            wall_time_ms=wall_ms,
            llm_time_ms=self._llm_ms,
            io_time_ms=self._io_ms,
        )


@asynccontextmanager
async def node_timer(node: str, tool_pipeline: str):
    """Async context manager that tracks wall-clock time for a graph node.

    Yields a ``_TimingBuilder`` with ``.mark_llm(start, end)`` and
    ``.mark_io(start, end)`` methods for recording sub-timings.

    Usage::

        async with node_timer("generate_sql", "query_tool") as t:
            llm_start = time.monotonic()
            result = await chain.ainvoke(...)
            t.mark_llm(llm_start, time.monotonic())
        timing_record = t.record

    Args:
        node: Graph node name.
        tool_pipeline: Pipeline grouping key.

    Yields:
        A _TimingBuilder instance.
    """
    builder = _TimingBuilder(node, tool_pipeline)
    yield builder


def aggregate_timing(records: list[TimingRecord]) -> dict[str, Any]:
    """Aggregate timing records by node and by pipeline.

    Args:
        records: List of TimingRecord dicts.

    Returns:
        Dict with ``by_node``, ``by_pipeline``, ``total``, and
        ``slowest_node`` summaries.
    """
    if not records:
        return {
            "by_node": {},
            "by_pipeline": {},
            "total": {
                "wall_time_ms": 0.0,
                "llm_time_ms": 0.0,
                "io_time_ms": 0.0,
                "overhead_ms": 0.0,
            },
            "slowest_node": None,
        }

    by_node: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "wall_time_ms": 0.0,
            "llm_time_ms": 0.0,
            "io_time_ms": 0.0,
            "call_count": 0,
        }
    )
    by_pipeline: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "wall_time_ms": 0.0,
            "llm_time_ms": 0.0,
            "io_time_ms": 0.0,
            "call_count": 0,
        }
    )
    grand = {
        "wall_time_ms": 0.0,
        "llm_time_ms": 0.0,
        "io_time_ms": 0.0,
        "overhead_ms": 0.0,
    }

    for rec in records:
        node = rec.get("node", "unknown")
        pipeline = rec.get("tool_pipeline", "unknown")
        for key in ("wall_time_ms", "llm_time_ms", "io_time_ms"):
            val = rec.get(key, 0.0)
            by_node[node][key] += val
            by_pipeline[pipeline][key] += val
            grand[key] += val
        grand["overhead_ms"] += rec.get("overhead_ms", 0.0)
        by_node[node]["call_count"] += 1
        by_pipeline[pipeline]["call_count"] += 1

    # Identify slowest node
    slowest_name = max(by_node, key=lambda n: by_node[n]["wall_time_ms"])
    slowest_entry = dict(by_node[slowest_name])
    slowest_entry["node"] = slowest_name

    return {
        "by_node": {k: dict(v) for k, v in by_node.items()},
        "by_pipeline": {k: dict(v) for k, v in by_pipeline.items()},
        "total": grand,
        "slowest_node": slowest_entry,
    }
