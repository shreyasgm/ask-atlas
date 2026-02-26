#!/usr/bin/env python3
"""Execution accuracy: compare agent SQL results against ground truth data.

This is the gold-standard text-to-SQL metric — does the agent's SQL produce
the same result set as the ground truth SQL?

The current judge compares the agent's **text answer** to ground truth **data rows**.
Execution accuracy separates SQL quality from text quality by comparing the
**data returned by the agent's SQL** against the **ground truth data rows**.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Relative tolerance for numeric comparisons (±1%)
_NUMERIC_TOLERANCE = 0.01


def _normalize_value(val: Any) -> Any:
    """Normalize a value for comparison (lowercase strings, round floats)."""
    if val is None:
        return None
    if isinstance(val, str):
        return val.strip().lower()
    if isinstance(val, float):
        return val
    if isinstance(val, int):
        return float(val)
    return str(val).strip().lower()


def _values_match(a: Any, b: Any) -> bool:
    """Check if two values match, with tolerance for numeric values."""
    na = _normalize_value(a)
    nb = _normalize_value(b)

    if na is None and nb is None:
        return True
    if na is None or nb is None:
        return False

    # Both numeric
    if isinstance(na, float) and isinstance(nb, float):
        if na == 0 and nb == 0:
            return True
        if na == 0 or nb == 0:
            return abs(na - nb) < _NUMERIC_TOLERANCE
        return math.isclose(na, nb, rel_tol=_NUMERIC_TOLERANCE)

    return na == nb


def _row_to_tuple(row: dict, keys: list[str]) -> tuple:
    """Convert a row dict to a normalized tuple for set comparison."""
    return tuple(_normalize_value(row.get(k)) for k in keys)


def compare_result_sets(
    agent_rows: list[dict],
    ground_truth_rows: list[dict],
) -> dict[str, Any]:
    """Compare two result sets and compute overlap metrics.

    Args:
        agent_rows: Rows from executing the agent's SQL.
        ground_truth_rows: Rows from the ground truth data.

    Returns:
        Dict with execution_match, row_count_match, value_overlap_pct.
    """
    if not ground_truth_rows:
        return {
            "execution_match": len(agent_rows) == 0,
            "row_count_match": len(agent_rows) == 0,
            "value_overlap_pct": 100.0 if len(agent_rows) == 0 else 0.0,
            "agent_row_count": len(agent_rows),
            "ground_truth_row_count": 0,
        }

    if not agent_rows:
        return {
            "execution_match": False,
            "row_count_match": False,
            "value_overlap_pct": 0.0,
            "agent_row_count": 0,
            "ground_truth_row_count": len(ground_truth_rows),
        }

    row_count_match = len(agent_rows) == len(ground_truth_rows)

    # Get all keys from ground truth rows
    gt_keys = sorted({k for row in ground_truth_rows for k in row.keys()})
    agent_keys = sorted({k for row in agent_rows for k in row.keys()})

    # Find overlapping columns (case-insensitive)
    gt_keys_lower = {k.lower(): k for k in gt_keys}
    agent_keys_lower = {k.lower(): k for k in agent_keys}
    common_keys_lower = set(gt_keys_lower.keys()) & set(agent_keys_lower.keys())

    if not common_keys_lower:
        # No overlapping columns — try value-level matching
        # Flatten all values and compare
        gt_values = {
            _normalize_value(v)
            for row in ground_truth_rows
            for v in row.values()
            if v is not None
        }
        agent_values = {
            _normalize_value(v)
            for row in agent_rows
            for v in row.values()
            if v is not None
        }
        if not gt_values:
            overlap_pct = 0.0
        else:
            overlap = sum(
                1 for v in gt_values if any(_values_match(v, av) for av in agent_values)
            )
            overlap_pct = round(overlap / len(gt_values) * 100, 1)

        return {
            "execution_match": False,
            "row_count_match": row_count_match,
            "value_overlap_pct": overlap_pct,
            "agent_row_count": len(agent_rows),
            "ground_truth_row_count": len(ground_truth_rows),
            "note": "No overlapping column names; used value-level matching",
        }

    # Compare using common columns
    gt_mapped = [
        {k: row.get(gt_keys_lower[k]) for k in common_keys_lower}
        for row in ground_truth_rows
    ]
    agent_mapped = [
        {k: row.get(agent_keys_lower[k]) for k in common_keys_lower}
        for row in agent_rows
    ]

    # Count how many ground truth rows have a matching agent row
    matched_gt = 0
    used_agent_indices: set[int] = set()

    for gt_row in gt_mapped:
        for i, agent_row in enumerate(agent_mapped):
            if i in used_agent_indices:
                continue
            if all(
                _values_match(gt_row.get(k), agent_row.get(k))
                for k in common_keys_lower
            ):
                matched_gt += 1
                used_agent_indices.add(i)
                break

    total_gt = len(gt_mapped)
    overlap_pct = round(matched_gt / total_gt * 100, 1) if total_gt else 0.0
    execution_match = matched_gt == total_gt and row_count_match

    return {
        "execution_match": execution_match,
        "row_count_match": row_count_match,
        "value_overlap_pct": overlap_pct,
        "agent_row_count": len(agent_rows),
        "ground_truth_row_count": len(ground_truth_rows),
    }


async def compute_execution_accuracy(
    agent_sql: str,
    ground_truth_data: list[dict],
    db_url: str,
) -> dict[str, Any]:
    """Execute the agent's SQL and compare result set to ground truth.

    Args:
        agent_sql: The SQL query generated by the agent.
        ground_truth_data: Ground truth rows for comparison.
        db_url: PostgreSQL connection URL for the Atlas database.

    Returns:
        Dict with execution_match, row_count_match, value_overlap_pct,
        and execution_error if the SQL failed to execute.
    """
    if not agent_sql or not agent_sql.strip():
        return {
            "execution_match": False,
            "row_count_match": False,
            "value_overlap_pct": 0.0,
            "agent_row_count": 0,
            "ground_truth_row_count": len(ground_truth_data),
            "execution_error": "No SQL provided by agent",
        }

    try:
        conn = await asyncpg.connect(db_url)
        try:
            # Execute with a timeout to avoid runaway queries
            rows = await conn.fetch(agent_sql, timeout=30)
            agent_rows = [dict(row) for row in rows]
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Execution accuracy: SQL execution failed — {e}")
        return {
            "execution_match": False,
            "row_count_match": False,
            "value_overlap_pct": 0.0,
            "agent_row_count": 0,
            "ground_truth_row_count": len(ground_truth_data),
            "execution_error": str(e),
        }

    result = compare_result_sets(agent_rows, ground_truth_data)
    return result
