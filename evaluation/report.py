#!/usr/bin/env python3
"""Generate JSON and Markdown evaluation reports.

Takes agent run results and judge verdicts and produces:
- A JSON report with all details
- A Markdown report for human consumption
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from utils import get_timestamp, logging


def _aggregate_scores(verdicts: list[dict]) -> dict[str, Any]:
    """Compute aggregate statistics over a list of judge verdicts."""
    if not verdicts:
        return {"count": 0, "avg_weighted_score": 0, "pass_rate": 0}

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


def generate_report(
    run_results: list[dict[str, Any]],
    judge_results: dict[str, dict],
    questions_meta: dict[str, dict],
) -> dict[str, Any]:
    """Build the full report data structure.

    Args:
        run_results: List of per-question agent run results.
        judge_results: Dict mapping question_id → judge verdict dict.
        questions_meta: Dict mapping question_id → {category, difficulty, text, ...}.

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
            "judge_comment": verdict.get("overall_comment", verdict.get("reasoning", "")),
        }
        per_question.append(entry)

        if verdict:
            all_verdicts.append(verdict)
            by_category[entry["category"]].append(verdict)
            by_difficulty[entry["difficulty"]].append(verdict)

    report = {
        "timestamp": get_timestamp(),
        "aggregate": _aggregate_scores(all_verdicts),
        "dimension_averages": _dimension_averages(all_verdicts),
        "by_category": {
            cat: _aggregate_scores(vds) for cat, vds in sorted(by_category.items())
        },
        "by_difficulty": {
            diff: _aggregate_scores(vds) for diff, vds in sorted(by_difficulty.items())
        },
        "failed_questions": [
            {"id": q["question_id"], "text": q["question_text"], "comment": q["judge_comment"]}
            for q in per_question
            if q["verdict"] == "fail"
        ],
        "per_question": per_question,
    }

    return report


def report_to_markdown(report: dict[str, Any]) -> str:
    """Convert report dict to a readable Markdown string."""
    lines: list[str] = []
    agg = report["aggregate"]

    lines.append("# Evaluation Report")
    lines.append(f"\n_Generated: {report['timestamp']}_\n")

    # Aggregate summary
    lines.append("## Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Questions evaluated | {agg['count']} |")
    lines.append(f"| Avg weighted score | {agg['avg_weighted_score']} / 5.0 |")
    lines.append(f"| Pass rate | {agg['pass_rate']}% |")
    lines.append(f"| Pass / Partial / Fail | {agg['pass_count']} / {agg['partial_count']} / {agg['fail_count']} |")

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
        lines.append("| ID | Difficulty | Category | Verdict | Score | Judge Mode | Duration |")
        lines.append("|----|------------|----------|---------|-------|------------|----------|")
        for q in per_q:
            dur = f"{q['duration_s']}s" if q["duration_s"] is not None else "n/a"
            mode = q.get("judge_mode", "n/a")
            lines.append(
                f"| {q['question_id']} | {q['difficulty']} | {q['category']} "
                f"| {q['verdict']} | {q['weighted_score']} | {mode} | {dur} |"
            )

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
