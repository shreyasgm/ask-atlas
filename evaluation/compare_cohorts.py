#!/usr/bin/env python3
"""Compare evaluation results across predefined question cohorts.

Analyzes a new eval run against the baseline, breaking results into five
cohorts: previously_failed, original_regression, additional_regression,
classification_diversity, and never_previously_run.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/compare_cohorts.py <new_run_id>
    PYTHONPATH=$(pwd) uv run python evaluation/compare_cohorts.py <new_run_id> --baseline <id> --save
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utils import EVALUATION_BASE_DIR

# ---------------------------------------------------------------------------
# Cohort definitions (question IDs from gt_regeneration_plan.md Step 5)
# ---------------------------------------------------------------------------

PREVIOUSLY_FAILED: set[str] = {
    "1",
    "57",
    "85",
    "86",
    "93",
    "94",
    "97",
    "103",
    "113",
    "127",
    "128",
    "129",
    "130",
    "131",
    "133",
    "135",
    "139",
    "147",
    "168",
    "170",
    "171",
    "185",
    "186",
    "208",
    "210",
    "213",
    "217",
    "224",
    "225",
    "226",
    "230",
    "238",
}

ORIGINAL_REGRESSION: set[str] = {
    "2",
    "4",
    "17",
    "25",
    "32",
    "53",
    "58",
    "61",
    "75",
    "98",
    "101",
    "107",
    "121",
    "190",
    "195",
    "214",
    "240",
}

ADDITIONAL_REGRESSION: set[str] = {
    "18",
    "26",
    "31",
    "33",
    "34",
    "40",
    "42",
    "45",
    "59",
    "99",
    "110",
    "151",
    "166",
    "173",
    "202",
    "206",
    "244",
}

CLASSIFICATION_DIVERSITY: set[str] = {
    "247",
    "248",
    "249",
    "250",
    "251",
    "252",
}

NEVER_PREVIOUSLY_RUN: set[str] = {
    "79",
    "88",
    "90",
    "92",
    "95",
    "100",
    "106",
    "112",
    "120",
    "124",
    "138",
    "140",
    "141",
    "145",
    "149",
    "152",
    "153",
    "155",
    "157",
    "160",
    "161",
    "163",
    "164",
    "165",
    "167",
    "175",
    "180",
    "184",
    "193",
    "194",
    "205",
    "207",
    "222",
    "223",
    "227",
}

DEFAULT_BASELINE = "20260227T151832Z"


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------


def _analyze_cohort(
    cohort: set[str],
    pq_baseline: dict[str, dict],
    pq_new: dict[str, dict],
    meta: dict[str, dict],
    *,
    cohort_type: str = "previously_failed",
) -> dict[str, Any]:
    """Analyze a single cohort of questions.

    Args:
        cohort: Set of question IDs in this cohort.
        pq_baseline: Baseline per-question index (qid → entry).
        pq_new: New run per-question index (qid → entry).
        meta: Question metadata (qid → {category, difficulty}).
        cohort_type: One of "previously_failed", "regression", "new_baseline".

    Returns:
        Dict with cohort-level stats and per-question detail.
    """
    details: list[dict[str, Any]] = []
    missing_from_new = 0

    # Counters for previously_failed
    flipped_to_pass = 0
    flipped_to_partial = 0
    still_fail = 0

    # Counters for regression
    held_pass = 0
    regressed = 0

    # Counters for new_baseline
    pass_count = 0
    partial_count = 0
    fail_count = 0

    for qid in sorted(cohort, key=lambda x: int(x)):
        q_new = pq_new.get(qid)
        q_base = pq_baseline.get(qid)

        if q_new is None:
            missing_from_new += 1
            details.append({"question_id": qid, "status": "missing_from_new"})
            continue

        new_verdict = q_new.get("verdict", "n/a")
        new_score = q_new.get("weighted_score", 0)
        q_meta = meta.get(qid, {})

        entry: dict[str, Any] = {
            "question_id": qid,
            "category": q_meta.get("category", q_new.get("category", "")),
            "difficulty": q_meta.get("difficulty", q_new.get("difficulty", "")),
            "new_verdict": new_verdict,
            "new_score": new_score,
        }

        if cohort_type == "previously_failed":
            base_verdict = q_base.get("verdict", "n/a") if q_base else "n/a"
            base_score = q_base.get("weighted_score", 0) if q_base else 0
            entry["baseline_verdict"] = base_verdict
            entry["baseline_score"] = base_score

            if new_verdict == "pass":
                flipped_to_pass += 1
                entry["outcome"] = "flipped_to_pass"
            elif new_verdict == "partial":
                flipped_to_partial += 1
                entry["outcome"] = "flipped_to_partial"
            else:
                still_fail += 1
                entry["outcome"] = "still_fail"

        elif cohort_type == "regression":
            base_verdict = q_base.get("verdict", "n/a") if q_base else "n/a"
            base_score = q_base.get("weighted_score", 0) if q_base else 0
            entry["baseline_verdict"] = base_verdict
            entry["baseline_score"] = base_score

            if new_verdict == "pass":
                held_pass += 1
                entry["outcome"] = "held_pass"
            else:
                regressed += 1
                entry["outcome"] = "regressed"

        elif cohort_type == "new_baseline":
            if new_verdict == "pass":
                pass_count += 1
            elif new_verdict == "partial":
                partial_count += 1
            else:
                fail_count += 1
            entry["outcome"] = new_verdict

        details.append(entry)

    result: dict[str, Any] = {
        "total": len(cohort),
        "evaluated": len(cohort) - missing_from_new,
        "missing_from_new": missing_from_new,
        "details": details,
    }

    if cohort_type == "previously_failed":
        result["flipped_to_pass"] = flipped_to_pass
        result["flipped_to_partial"] = flipped_to_partial
        result["still_fail"] = still_fail
    elif cohort_type == "regression":
        result["held_pass"] = held_pass
        result["regressed"] = regressed
    elif cohort_type == "new_baseline":
        result["pass_count"] = pass_count
        result["partial_count"] = partial_count
        result["fail_count"] = fail_count

    return result


def _aggregate_comparison(
    pq_baseline: dict[str, dict],
    pq_new: dict[str, dict],
) -> dict[str, Any]:
    """Compute overall and category-level pass rate deltas for overlapping questions.

    Args:
        pq_baseline: Baseline per-question index.
        pq_new: New run per-question index.

    Returns:
        Dict with overall pass rates and by-category breakdown.
    """
    overlap = set(pq_baseline.keys()) & set(pq_new.keys())
    if not overlap:
        return {"overlap_count": 0}

    base_pass = sum(1 for qid in overlap if pq_baseline[qid].get("verdict") == "pass")
    new_pass = sum(1 for qid in overlap if pq_new[qid].get("verdict") == "pass")

    n = len(overlap)
    base_rate = round(base_pass / n * 100, 1)
    new_rate = round(new_pass / n * 100, 1)

    # By category
    by_category: dict[str, dict[str, Any]] = {}
    for qid in overlap:
        cat = pq_new[qid].get("category", pq_baseline[qid].get("category", "unknown"))
        if cat not in by_category:
            by_category[cat] = {"base_pass": 0, "new_pass": 0, "count": 0}
        by_category[cat]["count"] += 1
        if pq_baseline[qid].get("verdict") == "pass":
            by_category[cat]["base_pass"] += 1
        if pq_new[qid].get("verdict") == "pass":
            by_category[cat]["new_pass"] += 1

    cat_result: dict[str, dict[str, Any]] = {}
    for cat, data in sorted(by_category.items()):
        c = data["count"]
        cat_result[cat] = {
            "count": c,
            "baseline_pass_rate": round(data["base_pass"] / c * 100, 1),
            "new_pass_rate": round(data["new_pass"] / c * 100, 1),
            "delta": round((data["new_pass"] - data["base_pass"]) / c * 100, 1),
        }

    return {
        "overlap_count": n,
        "baseline_pass_rate": base_rate,
        "new_pass_rate": new_rate,
        "pass_rate_delta": round(new_rate - base_rate, 1),
        "by_category": cat_result,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def compare_cohorts(
    new_run: str,
    baseline_run: str | None = None,
    *,
    runs_dir: Path | None = None,
) -> str:
    """Compare a new eval run against baseline across predefined cohorts.

    Args:
        new_run: Run ID (timestamp) of the new run.
        baseline_run: Run ID of the baseline. Defaults to DEFAULT_BASELINE.
        runs_dir: Override for the runs directory (for testing).

    Returns:
        Formatted Markdown report string.
    """
    if baseline_run is None:
        baseline_run = DEFAULT_BASELINE

    if runs_dir is None:
        runs_dir = EVALUATION_BASE_DIR / "runs"

    # Load reports
    new_report_path = runs_dir / new_run / "report.json"
    base_report_path = runs_dir / baseline_run / "report.json"

    if not new_report_path.exists():
        return f"Error: report.json not found for new run {new_run}"
    if not base_report_path.exists():
        return f"Error: report.json not found for baseline run {baseline_run}"

    with open(new_report_path) as f:
        new_report = json.load(f)
    with open(base_report_path) as f:
        base_report = json.load(f)

    pq_new = {str(q["question_id"]): q for q in new_report.get("per_question", [])}
    pq_base = {str(q["question_id"]): q for q in base_report.get("per_question", [])}

    # Build minimal meta from new run's per-question data
    meta: dict[str, dict] = {}
    for qid, q in pq_new.items():
        meta[qid] = {
            "category": q.get("category", ""),
            "difficulty": q.get("difficulty", ""),
        }

    # Analyze each cohort
    failed_result = _analyze_cohort(
        PREVIOUSLY_FAILED, pq_base, pq_new, meta, cohort_type="previously_failed"
    )
    orig_regression = _analyze_cohort(
        ORIGINAL_REGRESSION, pq_base, pq_new, meta, cohort_type="regression"
    )
    add_regression = _analyze_cohort(
        ADDITIONAL_REGRESSION, pq_base, pq_new, meta, cohort_type="regression"
    )
    class_diversity = _analyze_cohort(
        CLASSIFICATION_DIVERSITY, {}, pq_new, meta, cohort_type="new_baseline"
    )
    never_run = _analyze_cohort(
        NEVER_PREVIOUSLY_RUN, {}, pq_new, meta, cohort_type="new_baseline"
    )

    # Overall comparison (overlapping questions only)
    overall = _aggregate_comparison(pq_base, pq_new)

    # Build Markdown
    lines: list[str] = []
    lines.append(f"# Cohort Comparison: {new_run} vs {baseline_run}\n")

    # Executive summary
    lines.append("## Executive Summary\n")
    lines.append("| Cohort | Metric | Value |")
    lines.append("|--------|--------|-------|")

    total_reg = orig_regression["held_pass"] + add_regression["held_pass"]
    total_reg_count = orig_regression["evaluated"] + add_regression["evaluated"]
    lines.append(
        f"| Previously Failed ({failed_result['total']}) | Flipped to pass "
        f"| {failed_result.get('flipped_to_pass', 0)} |"
    )
    lines.append(
        f"| Previously Failed | Flipped to partial "
        f"| {failed_result.get('flipped_to_partial', 0)} |"
    )
    lines.append(
        f"| Previously Failed | Still fail " f"| {failed_result.get('still_fail', 0)} |"
    )
    lines.append(f"| Regression ({total_reg_count}) | Held pass " f"| {total_reg} |")
    lines.append(
        f"| Regression | Regressed "
        f"| {orig_regression.get('regressed', 0) + add_regression.get('regressed', 0)} |"
    )
    lines.append(
        f"| Classification Diversity ({class_diversity['total']}) | Pass "
        f"| {class_diversity.get('pass_count', 0)} |"
    )
    lines.append(
        f"| Never Previously Run ({never_run['total']}) | Pass "
        f"| {never_run.get('pass_count', 0)} |"
    )

    if overall.get("overlap_count"):
        lines.append(
            f"| **Overall Overlap ({overall['overlap_count']})** | **Pass rate delta** "
            f"| **{overall['pass_rate_delta']:+.1f}pp** "
            f"({overall['baseline_pass_rate']:.1f}% → {overall['new_pass_rate']:.1f}%) |"
        )

    # Per-cohort sections
    _append_cohort_section(
        lines,
        "Previously Failed",
        failed_result,
        cohort_type="previously_failed",
    )
    _append_cohort_section(
        lines,
        "Original Regression",
        orig_regression,
        cohort_type="regression",
    )
    _append_cohort_section(
        lines,
        "Additional Regression",
        add_regression,
        cohort_type="regression",
    )
    _append_cohort_section(
        lines,
        "Classification Diversity",
        class_diversity,
        cohort_type="new_baseline",
    )
    _append_cohort_section(
        lines,
        "Never Previously Run",
        never_run,
        cohort_type="new_baseline",
    )

    # Category-level deltas
    cat_data = overall.get("by_category", {})
    if cat_data:
        lines.append("\n## Category-Level Pass Rate Deltas\n")
        lines.append("| Category | Count | Baseline | New | Delta |")
        lines.append("|----------|-------|----------|-----|-------|")
        for cat, data in sorted(cat_data.items()):
            delta_str = f"{data['delta']:+.1f}pp"
            lines.append(
                f"| {cat} | {data['count']} | {data['baseline_pass_rate']:.1f}% "
                f"| {data['new_pass_rate']:.1f}% | {delta_str} |"
            )

    return "\n".join(lines) + "\n"


def _append_cohort_section(
    lines: list[str],
    title: str,
    result: dict[str, Any],
    *,
    cohort_type: str,
) -> None:
    """Append a per-cohort section to the Markdown output."""
    lines.append(f"\n## {title} ({result['evaluated']}/{result['total']})\n")

    if result["missing_from_new"] > 0:
        lines.append(f"*{result['missing_from_new']} questions not in new run*\n")

    if cohort_type == "previously_failed":
        lines.append(
            f"Flipped to pass: **{result['flipped_to_pass']}** · "
            f"Flipped to partial: **{result['flipped_to_partial']}** · "
            f"Still fail: **{result['still_fail']}**\n"
        )
        header = "| Q | Category | Baseline | New | Outcome |"
        sep = "|---|----------|----------|-----|---------|"
    elif cohort_type == "regression":
        lines.append(
            f"Held pass: **{result['held_pass']}** · "
            f"Regressed: **{result['regressed']}**\n"
        )
        header = "| Q | Category | Baseline | New | Outcome |"
        sep = "|---|----------|----------|-----|---------|"
    else:
        lines.append(
            f"Pass: **{result.get('pass_count', 0)}** · "
            f"Partial: **{result.get('partial_count', 0)}** · "
            f"Fail: **{result.get('fail_count', 0)}**\n"
        )
        header = "| Q | Category | Verdict | Score |"
        sep = "|---|----------|---------|-------|"

    lines.append(header)
    lines.append(sep)

    for d in result.get("details", []):
        if d.get("status") == "missing_from_new":
            continue

        qid = d["question_id"]
        cat = d.get("category", "")

        if cohort_type in ("previously_failed", "regression"):
            bv = d.get("baseline_verdict", "n/a")
            nv = d.get("new_verdict", "n/a")
            outcome = d.get("outcome", "")
            lines.append(f"| Q{qid} | {cat} | {bv} | {nv} | {outcome} |")
        else:
            nv = d.get("new_verdict", "n/a")
            ns = d.get("new_score", 0)
            lines.append(f"| Q{qid} | {cat} | {nv} | {ns} |")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare eval cohorts against baseline"
    )
    parser.add_argument("new_run", help="Run ID (timestamp) of the new run")
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE,
        help=f"Baseline run ID (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save cohort_comparison.md to the new run directory",
    )
    args = parser.parse_args()

    output = compare_cohorts(args.new_run, args.baseline)
    print(output)

    if args.save:
        out_path = EVALUATION_BASE_DIR / "runs" / args.new_run / "cohort_comparison.md"
        out_path.write_text(output)
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
