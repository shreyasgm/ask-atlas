#!/usr/bin/env python3
"""Generate JSON and Markdown evaluation reports.

Takes agent run results and judge verdicts and produces:
- A JSON report with all details
- A Markdown report for human consumption
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from utils import get_timestamp, logging

# Budget thresholds — flag questions exceeding these limits
BUDGET_THRESHOLD_DURATION_S = 30.0
BUDGET_THRESHOLD_COST_USD = 0.05


def _aggregate_scores(verdicts: list[dict]) -> dict[str, Any]:
    """Compute aggregate statistics over a list of judge verdicts."""
    if not verdicts:
        return {
            "count": 0,
            "avg_weighted_score": 0,
            "pass_rate": 0,
            "pass_count": 0,
            "partial_count": 0,
            "fail_count": 0,
        }

    scores = [v["weighted_score"] for v in verdicts if "weighted_score" in v]
    pass_count = sum(1 for v in verdicts if v.get("verdict") == "pass")
    partial_count = sum(1 for v in verdicts if v.get("verdict") == "partial")
    fail_count = sum(1 for v in verdicts if v.get("verdict") == "fail")

    return {
        "count": len(verdicts),
        "avg_weighted_score": round(sum(scores) / len(scores), 3) if scores else 0,
        "pass_count": pass_count,
        "partial_count": partial_count,
        "fail_count": fail_count,
        "pass_rate": round(pass_count / len(verdicts) * 100, 1) if verdicts else 0,
    }


def _dimension_averages(verdicts: list[dict]) -> dict[str, float]:
    """Average each scoring dimension across verdicts (ground-truth ones only)."""
    dims = ["factual_correctness", "data_accuracy", "completeness", "reasoning_quality"]
    avgs = {}
    for dim in dims:
        scores = [
            v[dim]["score"]
            for v in verdicts
            if isinstance(v.get(dim), dict) and "score" in v[dim]
        ]
        avgs[dim] = round(sum(scores) / len(scores), 2) if scores else 0
    return avgs


def _compute_run_stats(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate timing statistics from agent run results."""
    durations = [
        r["duration_s"] for r in run_results if r.get("duration_s") is not None
    ]
    if not durations:
        return {
            "total_duration_s": 0,
            "avg_question_duration_s": 0,
            "slowest_question": None,
        }

    slowest = max(run_results, key=lambda r: r.get("duration_s") or 0)
    return {
        "total_duration_s": round(sum(durations), 1),
        "avg_question_duration_s": round(sum(durations) / len(durations), 1),
        "slowest_question": {
            "id": str(slowest["question_id"]),
            "duration_s": slowest["duration_s"],
        },
    }


def _aggregate_costs(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate cost statistics from agent run results.

    Args:
        run_results: List of per-question result dicts, each optionally
            containing ``cost``, ``token_usage``, and ``tool_call_counts``.

    Returns:
        Dict with total cost, average cost, breakdowns by pipeline, and
        tool call statistics.
    """
    costs = [r["cost"]["total_cost_usd"] for r in run_results if r.get("cost")]
    if not costs:
        return {}

    # Cost by pipeline
    pipeline_costs: dict[str, float] = defaultdict(float)
    pipeline_tokens: dict[str, dict[str, int]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )
    tool_call_totals: dict[str, int] = defaultdict(int)
    questions_with_costs = 0

    for r in run_results:
        cost = r.get("cost")
        if cost:
            questions_with_costs += 1
            for pipeline, amount in cost.get("by_pipeline", {}).items():
                pipeline_costs[pipeline] += amount

        usage = r.get("token_usage")
        if usage:
            for pipeline, totals in usage.get("by_pipeline", {}).items():
                for key in ("input_tokens", "output_tokens", "total_tokens"):
                    pipeline_tokens[pipeline][key] += totals.get(key, 0)

        tc = r.get("tool_call_counts")
        if tc:
            for tool_name, count in tc.items():
                tool_call_totals[tool_name] += count

    n = questions_with_costs or 1
    return {
        "total_cost_usd": round(sum(costs), 4),
        "avg_cost_per_question_usd": round(sum(costs) / n, 4),
        "questions_with_costs": questions_with_costs,
        "cost_by_pipeline": {k: round(v, 4) for k, v in sorted(pipeline_costs.items())},
        "avg_cost_by_pipeline": {
            k: round(v / n, 6) for k, v in sorted(pipeline_costs.items())
        },
        "tokens_by_pipeline": dict(sorted(pipeline_tokens.items())),
        "tool_call_totals": dict(sorted(tool_call_totals.items())),
        "avg_tool_calls_per_question": {
            k: round(v / n, 1) for k, v in sorted(tool_call_totals.items())
        },
    }


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from an already-sorted list.

    Args:
        sorted_values: Pre-sorted list of numeric values.
        p: Percentile to compute (0-100).

    Returns:
        The interpolated percentile value.
    """
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _aggregate_latency(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate latency statistics from per-step timing summaries.

    Args:
        run_results: List of per-question result dicts, each optionally
            containing ``step_timing_summary``.

    Returns:
        Dict with percentiles, per-node averages, time breakdown, or empty
        dict if no timing data is available.
    """
    results_with_timing = [r for r in run_results if r.get("step_timing_summary")]
    if not results_with_timing:
        return {}

    # Collect total wall times
    total_wall_times = sorted(
        r["step_timing_summary"]["total"]["wall_time_ms"] for r in results_with_timing
    )
    n = len(results_with_timing)

    # Aggregate per-node averages
    node_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"wall_time_ms": 0.0, "llm_time_ms": 0.0, "io_time_ms": 0.0, "count": 0}
    )
    # Aggregate per-pipeline averages
    pipeline_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"wall_time_ms": 0.0, "llm_time_ms": 0.0, "io_time_ms": 0.0, "count": 0}
    )
    grand_llm = 0.0
    grand_io = 0.0
    grand_overhead = 0.0
    grand_wall = 0.0

    for r in results_with_timing:
        summary = r["step_timing_summary"]
        for node_name, node_data in summary.get("by_node", {}).items():
            node_totals[node_name]["wall_time_ms"] += node_data.get("wall_time_ms", 0)
            node_totals[node_name]["llm_time_ms"] += node_data.get("llm_time_ms", 0)
            node_totals[node_name]["io_time_ms"] += node_data.get("io_time_ms", 0)
            node_totals[node_name]["count"] += 1

        for pipe_name, pipe_data in summary.get("by_pipeline", {}).items():
            pipeline_totals[pipe_name]["wall_time_ms"] += pipe_data.get(
                "wall_time_ms", 0
            )
            pipeline_totals[pipe_name]["llm_time_ms"] += pipe_data.get("llm_time_ms", 0)
            pipeline_totals[pipe_name]["io_time_ms"] += pipe_data.get("io_time_ms", 0)
            pipeline_totals[pipe_name]["count"] += 1

        total = summary.get("total", {})
        grand_llm += total.get("llm_time_ms", 0)
        grand_io += total.get("io_time_ms", 0)
        grand_overhead += total.get("overhead_ms", 0)
        grand_wall += total.get("wall_time_ms", 0)

    # Compute averages per node
    avg_by_node = {}
    for node_name, data in sorted(
        node_totals.items(), key=lambda x: -x[1]["wall_time_ms"]
    ):
        cnt = data["count"]
        avg_by_node[node_name] = {
            "avg_wall_time_ms": round(data["wall_time_ms"] / cnt, 1),
            "avg_llm_time_ms": round(data["llm_time_ms"] / cnt, 1),
            "avg_io_time_ms": round(data["io_time_ms"] / cnt, 1),
            "pct_of_total": (
                round(data["wall_time_ms"] / grand_wall * 100, 1) if grand_wall else 0
            ),
            "appearances": cnt,
        }

    # Compute averages per pipeline
    avg_by_pipeline = {}
    for pipe_name, data in sorted(
        pipeline_totals.items(), key=lambda x: -x[1]["wall_time_ms"]
    ):
        cnt = data["count"]
        avg_by_pipeline[pipe_name] = {
            "avg_wall_time_ms": round(data["wall_time_ms"] / cnt, 1),
            "avg_llm_time_ms": round(data["llm_time_ms"] / cnt, 1),
            "avg_io_time_ms": round(data["io_time_ms"] / cnt, 1),
            "pct_of_total": (
                round(data["wall_time_ms"] / grand_wall * 100, 1) if grand_wall else 0
            ),
            "appearances": cnt,
        }

    # Time breakdown percentages
    time_breakdown = {
        "llm_pct": round(grand_llm / grand_wall * 100, 1) if grand_wall else 0,
        "io_pct": round(grand_io / grand_wall * 100, 1) if grand_wall else 0,
        "overhead_pct": (
            round(grand_overhead / grand_wall * 100, 1) if grand_wall else 0
        ),
    }

    result = {
        "questions_with_timing": n,
        "avg_total_ms": round(sum(total_wall_times) / n, 1),
        "p50_total_ms": round(_percentile(total_wall_times, 50), 1),
        "p90_total_ms": round(_percentile(total_wall_times, 90), 1),
        "p95_total_ms": round(_percentile(total_wall_times, 95), 1),
        "max_total_ms": round(max(total_wall_times), 1),
        "avg_by_node": avg_by_node,
        "time_breakdown": time_breakdown,
    }

    if avg_by_pipeline:
        result["avg_by_pipeline"] = avg_by_pipeline

    return result


def _check_budget_violations(run_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Check for questions exceeding duration or cost thresholds.

    Args:
        run_results: List of per-question result dicts.

    Returns:
        Dict with violation counts and details, or empty dict if none.
    """
    violations: list[dict[str, Any]] = []

    for r in run_results:
        qid = str(r.get("question_id", ""))
        reasons: list[str] = []

        duration = r.get("duration_s")
        if duration is not None and duration > BUDGET_THRESHOLD_DURATION_S:
            reasons.append(f"duration {duration:.1f}s > {BUDGET_THRESHOLD_DURATION_S}s")

        cost = r.get("cost", {})
        cost_usd = cost.get("total_cost_usd") if cost else None
        if cost_usd is not None and cost_usd > BUDGET_THRESHOLD_COST_USD:
            reasons.append(f"cost ${cost_usd:.4f} > ${BUDGET_THRESHOLD_COST_USD}")

        if reasons:
            violations.append(
                {
                    "question_id": qid,
                    "question_text": r.get("question_text", ""),
                    "reasons": reasons,
                    "duration_s": duration,
                    "cost_usd": cost_usd,
                }
            )

    if not violations:
        return {}

    return {
        "duration_violations": sum(
            1
            for v in violations
            if any("duration" in reason for reason in v["reasons"])
        ),
        "cost_violations": sum(
            1 for v in violations if any("cost" in reason for reason in v["reasons"])
        ),
        "total_violations": len(violations),
        "violations": violations,
    }


def generate_report(
    run_results: list[dict[str, Any]],
    judge_results: dict[str, dict],
    questions_meta: dict[str, dict],
    judge_model: str = "unknown",
    judge_provider: str = "unknown",
) -> dict[str, Any]:
    """Build the full report data structure.

    Args:
        run_results: List of per-question agent run results.
        judge_results: Dict mapping question_id → judge verdict dict.
        questions_meta: Dict mapping question_id → {category, difficulty, text, ...}.
        judge_model: Name of the LLM model used for judging.
        judge_provider: Provider of the judge model.

    Returns:
        Report dict containing aggregate and per-question data.
    """
    per_question = []
    by_category: dict[str, list[dict]] = defaultdict(list)
    by_difficulty: dict[str, list[dict]] = defaultdict(list)
    all_verdicts: list[dict] = []

    for run in run_results:
        qid = str(run["question_id"])
        verdict = judge_results.get(qid, {})
        meta = questions_meta.get(qid, {})

        run_cost = run.get("cost", {})
        entry = {
            "question_id": qid,
            "question_text": run.get("question_text", meta.get("text", "")),
            "category": run.get("category", meta.get("category", "unknown")),
            "difficulty": run.get("difficulty", meta.get("difficulty", "unknown")),
            "status": run.get("status", "error"),
            "duration_s": run.get("duration_s"),
            "verdict": verdict.get("verdict", "n/a"),
            "weighted_score": verdict.get("weighted_score", 0),
            "judge_mode": verdict.get("judge_mode", "n/a"),
            "error": run.get("error"),
            "judge_comment": verdict.get(
                "overall_comment", verdict.get("reasoning", "")
            ),
            "judge_details": verdict,
            "cost_usd": run_cost.get("total_cost_usd") if run_cost else None,
        }
        per_question.append(entry)

        if verdict:
            all_verdicts.append(verdict)
            by_category[entry["category"]].append(verdict)
            by_difficulty[entry["difficulty"]].append(verdict)

    cost_analysis = _aggregate_costs(run_results)

    report = {
        "timestamp": get_timestamp(),
        "judge_model": judge_model,
        "judge_provider": judge_provider,
        "aggregate": _aggregate_scores(all_verdicts),
        "dimension_averages": _dimension_averages(all_verdicts),
        "run_stats": _compute_run_stats(run_results),
        "by_category": {
            cat: _aggregate_scores(vds) for cat, vds in sorted(by_category.items())
        },
        "by_difficulty": {
            diff: _aggregate_scores(vds) for diff, vds in sorted(by_difficulty.items())
        },
        "failed_questions": [
            {
                "id": q["question_id"],
                "text": q["question_text"],
                "comment": q["judge_comment"],
            }
            for q in per_question
            if q["verdict"] == "fail"
        ],
        "per_question": per_question,
    }

    if cost_analysis:
        report["cost_analysis"] = cost_analysis

    latency_analysis = _aggregate_latency(run_results)
    if latency_analysis:
        report["latency_analysis"] = latency_analysis

    budget_violations = _check_budget_violations(run_results)
    if budget_violations:
        report["budget_violations"] = budget_violations

    return report


def report_to_markdown(report: dict[str, Any]) -> str:
    """Convert report dict to a readable Markdown string."""
    lines: list[str] = []
    agg = report["aggregate"]

    lines.append("# Evaluation Report")
    lines.append(f"\n_Generated: {report['timestamp']}_\n")

    # Model info
    if report.get("judge_model") and report["judge_model"] != "unknown":
        lines.append(
            f"_Judge: {report['judge_model']} ({report.get('judge_provider', 'unknown')})_\n"
        )

    # Aggregate summary
    lines.append("## Summary\n")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Questions evaluated | {agg['count']} |")
    lines.append(f"| Avg weighted score | {agg['avg_weighted_score']} / 5.0 |")
    lines.append(f"| Pass rate | {agg['pass_rate']}% |")
    lines.append(
        f"| Pass / Partial / Fail | {agg['pass_count']} / {agg['partial_count']} / {agg['fail_count']} |"
    )

    # Run stats
    run_stats = report.get("run_stats", {})
    if run_stats and run_stats.get("total_duration_s"):
        lines.append("\n## Run Stats\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total duration | {run_stats['total_duration_s']}s |")
        lines.append(
            f"| Avg question duration | {run_stats['avg_question_duration_s']}s |"
        )
        slowest = run_stats.get("slowest_question")
        if slowest:
            lines.append(
                f"| Slowest question | Q{slowest['id']} ({slowest['duration_s']}s) |"
            )

    # Dimension averages
    dims = report.get("dimension_averages", {})
    if dims:
        lines.append("\n## Dimension Averages\n")
        lines.append("| Dimension | Avg Score |")
        lines.append("|-----------|-----------|")
        for dim, score in dims.items():
            lines.append(f"| {dim.replace('_', ' ').title()} | {score} / 5.0 |")

    # By category
    by_cat = report.get("by_category", {})
    if by_cat:
        lines.append("\n## By Category\n")
        lines.append("| Category | Count | Avg Score | Pass Rate |")
        lines.append("|----------|-------|-----------|-----------|")
        for cat, stats in by_cat.items():
            lines.append(
                f"| {cat} | {stats['count']} | {stats['avg_weighted_score']} | {stats['pass_rate']}% |"
            )

    # By difficulty
    by_diff = report.get("by_difficulty", {})
    if by_diff:
        lines.append("\n## By Difficulty\n")
        lines.append("| Difficulty | Count | Avg Score | Pass Rate |")
        lines.append("|------------|-------|-----------|-----------|")
        for diff, stats in by_diff.items():
            lines.append(
                f"| {diff} | {stats['count']} | {stats['avg_weighted_score']} | {stats['pass_rate']}% |"
            )

    # Cost analysis
    cost = report.get("cost_analysis", {})
    if cost:
        lines.append("\n## Cost Analysis\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total cost | ${cost['total_cost_usd']:.4f} |")
        lines.append(
            f"| Avg cost per question | ${cost['avg_cost_per_question_usd']:.4f} |"
        )
        lines.append(f"| Questions with cost data | {cost['questions_with_costs']} |")

        cost_by_pipe = cost.get("cost_by_pipeline", {})
        tokens_by_pipe = cost.get("tokens_by_pipeline", {})
        if cost_by_pipe:
            lines.append("\n### Cost by Tool Pipeline\n")
            lines.append("| Pipeline | Total Cost | Avg Cost | Total Tokens |")
            lines.append("|----------|-----------|----------|-------------|")
            for pipe in sorted(cost_by_pipe.keys()):
                tc = cost_by_pipe[pipe]
                ac = cost.get("avg_cost_by_pipeline", {}).get(pipe, 0)
                tt = tokens_by_pipe.get(pipe, {}).get("total_tokens", 0)
                lines.append(f"| {pipe} | ${tc:.4f} | ${ac:.6f} | {tt:,} |")

        tool_totals = cost.get("tool_call_totals", {})
        if tool_totals:
            lines.append("\n### Tool Call Counts\n")
            lines.append("| Tool | Total Calls | Avg per Question |")
            lines.append("|------|------------|-----------------|")
            avg_calls = cost.get("avg_tool_calls_per_question", {})
            for tool_name in sorted(tool_totals.keys()):
                tc = tool_totals[tool_name]
                ac = avg_calls.get(tool_name, 0)
                lines.append(f"| {tool_name} | {tc} | {ac} |")

    # Latency analysis
    latency = report.get("latency_analysis", {})
    if latency:
        lines.append("\n## Latency Analysis\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Avg total | {latency['avg_total_ms']:.0f}ms |")
        lines.append(f"| P50 | {latency['p50_total_ms']:.0f}ms |")
        lines.append(f"| P90 | {latency['p90_total_ms']:.0f}ms |")
        lines.append(f"| P95 | {latency['p95_total_ms']:.0f}ms |")
        lines.append(f"| Max | {latency['max_total_ms']:.0f}ms |")
        lines.append(f"| Questions with timing | {latency['questions_with_timing']} |")

        tb = latency.get("time_breakdown", {})
        if tb:
            lines.append(f"| LLM time | {tb.get('llm_pct', 0):.1f}% |")
            lines.append(f"| I/O time | {tb.get('io_pct', 0):.1f}% |")
            lines.append(f"| Overhead | {tb.get('overhead_pct', 0):.1f}% |")

        avg_by_node = latency.get("avg_by_node", {})
        if avg_by_node:
            lines.append("\n### Bottleneck Nodes\n")
            lines.append("| Node | Avg Time | % of Total | LLM % | I/O % |")
            lines.append("|------|----------|------------|-------|-------|")
            for node_name, data in avg_by_node.items():
                avg_wall = data["avg_wall_time_ms"]
                llm_pct = (
                    round(data["avg_llm_time_ms"] / avg_wall * 100, 1)
                    if avg_wall
                    else 0
                )
                io_pct = (
                    round(data["avg_io_time_ms"] / avg_wall * 100, 1) if avg_wall else 0
                )
                lines.append(
                    f"| {node_name} | {avg_wall:.0f}ms | {data['pct_of_total']:.1f}% "
                    f"| {llm_pct:.1f}% | {io_pct:.1f}% |"
                )

        avg_by_pipeline = latency.get("avg_by_pipeline", {})
        if avg_by_pipeline:
            lines.append("\n### Pipeline Latency\n")
            lines.append(
                "| Pipeline | Avg Time | % of Total | LLM % | I/O % | Appearances |"
            )
            lines.append(
                "|----------|----------|------------|-------|-------|-------------|"
            )
            for pipe_name, data in avg_by_pipeline.items():
                avg_wall = data["avg_wall_time_ms"]
                llm_pct = (
                    round(data["avg_llm_time_ms"] / avg_wall * 100, 1)
                    if avg_wall
                    else 0
                )
                io_pct = (
                    round(data["avg_io_time_ms"] / avg_wall * 100, 1) if avg_wall else 0
                )
                lines.append(
                    f"| {pipe_name} | {avg_wall:.0f}ms | {data['pct_of_total']:.1f}% "
                    f"| {llm_pct:.1f}% | {io_pct:.1f}% | {data['appearances']} |"
                )

    # Budget violations
    bv = report.get("budget_violations", {})
    if bv:
        lines.append("\n## Budget Violations\n")
        lines.append(
            f"**{bv['total_violations']}** questions exceeded thresholds "
            f"({bv['duration_violations']} duration, {bv['cost_violations']} cost)\n"
        )
        lines.append("| Question | Duration | Cost | Reasons |")
        lines.append("|----------|----------|------|---------|")
        for v in bv.get("violations", []):
            dur = (
                f"{v['duration_s']:.1f}s" if v.get("duration_s") is not None else "n/a"
            )
            cost_val = (
                f"${v['cost_usd']:.4f}" if v.get("cost_usd") is not None else "n/a"
            )
            reasons = "; ".join(v["reasons"])
            lines.append(f"| Q{v['question_id']} | {dur} | {cost_val} | {reasons} |")

    # Failed questions
    failed = report.get("failed_questions", [])
    if failed:
        lines.append("\n## Failed Questions\n")
        for q in failed:
            lines.append(f"- **Q{q['id']}**: {q['text']}")
            if q.get("comment"):
                lines.append(f"  - _{q['comment']}_")

    # Per-question details
    per_q = report.get("per_question", [])
    if per_q:
        lines.append("\n## Per-Question Details\n")
        has_cost = report.get("cost_analysis")
        if has_cost:
            lines.append(
                "| ID | Difficulty | Category | Verdict | Score | Judge Mode | Duration | Cost |"
            )
            lines.append(
                "|----|------------|----------|---------|-------|------------|----------|------|"
            )
        else:
            lines.append(
                "| ID | Difficulty | Category | Verdict | Score | Judge Mode | Duration |"
            )
            lines.append(
                "|----|------------|----------|---------|-------|------------|----------|"
            )
        for q in per_q:
            dur = f"{q['duration_s']}s" if q["duration_s"] is not None else "n/a"
            mode = q.get("judge_mode", "n/a")
            row = (
                f"| {q['question_id']} | {q['difficulty']} | {q['category']} "
                f"| {q['verdict']} | {q['weighted_score']} | {mode} | {dur}"
            )
            if has_cost:
                cost_val = q.get("cost_usd")
                cost_str = f"${cost_val:.4f}" if cost_val is not None else "n/a"
                row += f" | {cost_str}"
            row += " |"
            lines.append(row)

    return "\n".join(lines) + "\n"


def save_report(report: dict[str, Any], run_dir: Path) -> tuple[Path, Path]:
    """Save report as JSON and Markdown to the run directory.

    Returns (json_path, md_path).
    """
    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"

    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logging.info(f"Saved JSON report: {json_path}")

    md_content = report_to_markdown(report)
    md_path.write_text(md_content)
    logging.info(f"Saved Markdown report: {md_path}")

    return json_path, md_path
