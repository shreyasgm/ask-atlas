#!/usr/bin/env python3
"""Compare two evaluation runs side-by-side.

A read-only tool — no LLM calls, just JSON diffing.

Usage:
    uv run python evaluation/compare_runs.py 20260221T210400Z 20260222T210714Z
    uv run python evaluation/compare_runs.py --list
"""

from __future__ import annotations

import argparse
import json

from utils import EVALUATION_BASE_DIR


def _load_report(run_id: str) -> dict | None:
    """Load the report.json for a given run timestamp."""
    report_path = EVALUATION_BASE_DIR / "runs" / run_id / "report.json"
    if not report_path.exists():
        return None
    with open(report_path) as f:
        return json.load(f)


def _load_summary(run_id: str) -> dict | None:
    """Load the summary.json for a given run timestamp."""
    summary_path = EVALUATION_BASE_DIR / "runs" / run_id / "summary.json"
    if not summary_path.exists():
        return None
    with open(summary_path) as f:
        return json.load(f)


def _list_runs() -> list[str]:
    """List all available run timestamps, sorted chronologically."""
    runs_dir = EVALUATION_BASE_DIR / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        d.name for d in runs_dir.iterdir() if d.is_dir() and d.name[0].isdigit()
    )


def _per_question_index(report: dict) -> dict[str, dict]:
    """Index per-question entries by question_id."""
    return {str(q["question_id"]): q for q in report.get("per_question", [])}


def compare_runs(run_a: str, run_b: str) -> str:
    """Compare two runs and return a formatted Markdown report."""
    report_a = _load_report(run_a)
    report_b = _load_report(run_b)
    summary_a = _load_summary(run_a)
    summary_b = _load_summary(run_b)

    if not report_a:
        return f"Error: report.json not found for run {run_a}"
    if not report_b:
        return f"Error: report.json not found for run {run_b}"

    lines: list[str] = []

    lines.append(f"# Run Comparison: {run_a} vs {run_b}\n")

    # Model info
    lines.append("## Models\n")
    lines.append("| | Run A | Run B |")
    lines.append("|---|-------|-------|")
    agent_a = summary_a.get("agent_model", "n/a") if summary_a else "n/a"
    agent_b = summary_b.get("agent_model", "n/a") if summary_b else "n/a"
    provider_a = summary_a.get("agent_provider", "n/a") if summary_a else "n/a"
    provider_b = summary_b.get("agent_provider", "n/a") if summary_b else "n/a"
    judge_a = report_a.get("judge_model", "n/a")
    judge_b = report_b.get("judge_model", "n/a")
    lines.append(
        f"| Agent model | {agent_a} ({provider_a}) | {agent_b} ({provider_b}) |"
    )
    lines.append(f"| Judge model | {judge_a} | {judge_b} |")

    # Aggregate comparison
    agg_a = report_a.get("aggregate", {})
    agg_b = report_b.get("aggregate", {})

    lines.append("\n## Aggregate Scores\n")
    lines.append("| Metric | Run A | Run B | Delta |")
    lines.append("|--------|-------|-------|-------|")

    for key, label in [
        ("count", "Questions"),
        ("avg_weighted_score", "Avg Score"),
        ("pass_rate", "Pass Rate (%)"),
        ("pass_count", "Pass"),
        ("partial_count", "Partial"),
        ("fail_count", "Fail"),
    ]:
        va = agg_a.get(key, 0)
        vb = agg_b.get(key, 0)
        if isinstance(va, float) and isinstance(vb, float):
            delta = round(vb - va, 3)
            delta_str = f"+{delta}" if delta > 0 else str(delta)
        elif isinstance(va, int) and isinstance(vb, int):
            delta = vb - va
            delta_str = f"+{delta}" if delta > 0 else str(delta)
        else:
            delta_str = "—"
        lines.append(f"| {label} | {va} | {vb} | {delta_str} |")

    # Per-question comparison
    pq_a = _per_question_index(report_a)
    pq_b = _per_question_index(report_b)
    all_qids = sorted(set(pq_a.keys()) | set(pq_b.keys()), key=int)

    regressions = []
    improvements = []
    new_questions = []
    removed_questions = []

    for qid in all_qids:
        qa = pq_a.get(qid)
        qb = pq_b.get(qid)

        if qa and not qb:
            removed_questions.append(qid)
            continue
        if qb and not qa:
            new_questions.append(qid)
            continue

        verdict_a = qa.get("verdict", "n/a")
        verdict_b = qb.get("verdict", "n/a")
        score_a = qa.get("weighted_score", 0)
        score_b = qb.get("weighted_score", 0)
        score_delta = score_b - score_a

        # Regression: pass→fail or pass→partial or partial→fail or score drop >1.0
        verdict_order = {"pass": 3, "partial": 2, "fail": 1, "n/a": 0}
        if (
            verdict_order.get(verdict_b, 0) < verdict_order.get(verdict_a, 0)
            or score_delta < -1.0
        ):
            regressions.append(
                {
                    "id": qid,
                    "text": qb.get("question_text", "")[:80],
                    "verdict_a": verdict_a,
                    "verdict_b": verdict_b,
                    "score_a": score_a,
                    "score_b": score_b,
                    "delta": round(score_delta, 3),
                }
            )
        elif (
            verdict_order.get(verdict_b, 0) > verdict_order.get(verdict_a, 0)
            or score_delta > 1.0
        ):
            improvements.append(
                {
                    "id": qid,
                    "text": qb.get("question_text", "")[:80],
                    "verdict_a": verdict_a,
                    "verdict_b": verdict_b,
                    "score_a": score_a,
                    "score_b": score_b,
                    "delta": round(score_delta, 3),
                }
            )

    # Regressions
    if regressions:
        lines.append(f"\n## Regressions ({len(regressions)})\n")
        lines.append("| ID | Question | A→B | Score A | Score B | Delta |")
        lines.append("|----|----------|-----|---------|---------|-------|")
        for r in regressions:
            lines.append(
                f"| Q{r['id']} | {r['text']} | {r['verdict_a']}→{r['verdict_b']} "
                f"| {r['score_a']} | {r['score_b']} | {r['delta']} |"
            )
    else:
        lines.append("\n## Regressions\n\nNone detected.\n")

    # Improvements
    if improvements:
        lines.append(f"\n## Improvements ({len(improvements)})\n")
        lines.append("| ID | Question | A→B | Score A | Score B | Delta |")
        lines.append("|----|----------|-----|---------|---------|-------|")
        for r in improvements:
            lines.append(
                f"| Q{r['id']} | {r['text']} | {r['verdict_a']}→{r['verdict_b']} "
                f"| {r['score_a']} | {r['score_b']} | {r['delta']} |"
            )
    else:
        lines.append("\n## Improvements\n\nNone detected.\n")

    # New / removed questions
    if new_questions:
        lines.append(f"\n## New Questions in Run B ({len(new_questions)})\n")
        for qid in new_questions:
            q = pq_b[qid]
            lines.append(
                f"- Q{qid}: {q.get('question_text', '')[:80]} ({q.get('verdict', 'n/a')})"
            )

    if removed_questions:
        lines.append(f"\n## Questions Removed in Run B ({len(removed_questions)})\n")
        for qid in removed_questions:
            q = pq_a[qid]
            lines.append(
                f"- Q{qid}: {q.get('question_text', '')[:80]} ({q.get('verdict', 'n/a')})"
            )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two evaluation runs")
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available runs",
    )
    parser.add_argument(
        "runs",
        nargs="*",
        help="Two run timestamps to compare (e.g. 20260221T210400Z 20260222T210714Z)",
    )
    args = parser.parse_args()

    if args.list:
        runs = _list_runs()
        if not runs:
            print("No runs found.")
            return
        print("Available runs:")
        for run_id in runs:
            summary = _load_summary(run_id)
            report = _load_report(run_id)
            qs = summary.get("questions_run", "?") if summary else "?"
            score = (
                report.get("aggregate", {}).get("avg_weighted_score", "?")
                if report
                else "?"
            )
            model = summary.get("agent_model", "?") if summary else "?"
            print(f"  {run_id}  ({qs} questions, avg={score}, model={model})")
        return

    if len(args.runs) != 2:
        parser.error("Provide exactly two run timestamps to compare, or use --list")
        return

    output = compare_runs(args.runs[0], args.runs[1])
    print(output)


if __name__ == "__main__":
    main()
