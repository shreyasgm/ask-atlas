#!/usr/bin/env python3
"""Single entry-point orchestrator for the Ask-Atlas evaluation pipeline.

Pipeline: load questions → run agent → load ground truth → judge answers → report.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --questions 1 2 6
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --concurrency 2 --judge-model gpt-5.2
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --skip-judge
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from typing import Any

from utils import (
    EVALUATION_BASE_DIR,
    load_json_file,
    logging,
)
from run_agent_evals import run_agent_evals
from judge import judge_answer
from report import generate_report, save_report
from html_report import generate_html_report

# Curated smoke-test subset: 1 easy, 2 medium, 2 hard across different categories.
# These are chosen for fast signal across diverse question types.
SMOKE_QUESTION_IDS = ["1", "6", "25", "97", "195"]


def _load_questions_meta() -> dict[str, dict]:
    """Load question metadata from eval_questions.json, keyed by string ID."""
    eval_data = load_json_file(EVALUATION_BASE_DIR / "eval_questions.json")
    categories = {cat["id"]: cat["name"] for cat in eval_data["categories"]}
    meta = {}
    for q in eval_data["questions"]:
        qid = str(q["id"])
        meta[qid] = {
            "text": q["text"],
            "category": categories.get(q["category_id"], q["category_id"]),
            "difficulty": q["difficulty"],
            "expected_behavior": q.get("expected_behavior"),
            "expected_api_target": q.get("expected_api_target"),
            "expected_classification": q.get("expected_classification"),
        }
    return meta


def _load_ground_truth(question_id: str) -> list[dict] | None:
    """Load ground truth data for a question, or None if unavailable."""
    gt_path = (
        EVALUATION_BASE_DIR / "results" / question_id / "ground_truth" / "results.json"
    )
    if not gt_path.exists():
        return None
    try:
        gt = load_json_file(gt_path)
        data = gt.get("results", {}).get("data", [])
        return data if data else None
    except Exception:
        return None


def _select_balanced(questions_meta: dict[str, dict], n: int) -> list[str]:
    """Select N questions with balanced coverage across categories and difficulties.

    Algorithm:
    1. Group questions by category, then by difficulty within each category.
    2. Build an interleaved queue per category cycling easy→medium→hard.
    3. Round-robin across all categories, taking one question per round.
    4. Continue until N questions selected or all exhausted.

    Args:
        questions_meta: Dict mapping question_id → {category, difficulty, ...}.
        n: Number of questions to select.

    Returns:
        List of question ID strings.
    """
    DIFFICULTY_ORDER = ["easy", "medium", "hard"]

    # Group by category → difficulty → list of qids
    cat_diff: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for qid, meta in questions_meta.items():
        cat = meta.get("category", "unknown")
        diff = meta.get("difficulty", "unknown")
        cat_diff[cat][diff].append(qid)

    # Sort qids within each group for determinism
    for cat in cat_diff:
        for diff in cat_diff[cat]:
            cat_diff[cat][diff].sort(key=int)

    # Build interleaved queue per category: cycle easy→medium→hard
    cat_queues: dict[str, list[str]] = {}
    for cat in sorted(cat_diff.keys()):
        queue: list[str] = []
        diff_lists = {d: list(cat_diff[cat][d]) for d in DIFFICULTY_ORDER}
        # Round-robin across difficulties
        while any(diff_lists[d] for d in DIFFICULTY_ORDER):
            for d in DIFFICULTY_ORDER:
                if diff_lists[d]:
                    queue.append(diff_lists[d].pop(0))
        cat_queues[cat] = queue

    # Round-robin across categories
    selected: list[str] = []
    categories = sorted(cat_queues.keys())
    while len(selected) < n and any(cat_queues[c] for c in categories):
        for cat in categories:
            if len(selected) >= n:
                break
            if cat_queues[cat]:
                selected.append(cat_queues[cat].pop(0))

    return selected


async def _judge_all(
    run_results: list[dict[str, Any]],
    questions_meta: dict[str, dict],
    judge_model: str,
    judge_provider: str,
    concurrency: int,
) -> dict[str, dict]:
    """Run the LLM judge on all successful agent results.

    Returns dict mapping question_id → judge verdict.
    """
    semaphore = asyncio.Semaphore(concurrency)
    judge_results: dict[str, dict] = {}

    async def _judge_one(result: dict[str, Any]) -> None:
        qid = str(result["question_id"])
        if result["status"] != "success" or not result.get("answer"):
            logging.warning(f"Question {qid}: skipping judge (agent failed)")
            return

        meta = questions_meta.get(qid, {})
        expected_behavior = meta.get("expected_behavior")
        ground_truth = _load_ground_truth(qid)

        # Build classification note for country page questions
        classification_note = None
        if meta.get("expected_classification"):
            classification_note = (
                "Note: The ground truth was collected from the Atlas Country Pages API using "
                "HS 1992 (HS92) product classification. The Country Pages API only supports HS92. "
                "If the agent's answer uses different product names or codes (e.g., from HS 2012), "
                "this may explain discrepancies in product-specific data."
            )

        async with semaphore:
            try:
                mode = (
                    "ground_truth"
                    if ground_truth is not None
                    else "refusal" if expected_behavior is not None else "plausibility"
                )
                logging.info(f"Question {qid}: judging ({mode})...")
                tools_used = result.get("tools_used")
                verdict = await judge_answer(
                    question=meta.get("text", result.get("question_text", "")),
                    agent_answer=result["answer"],
                    ground_truth_data=ground_truth,
                    expected_behavior=expected_behavior,
                    model=judge_model,
                    provider=judge_provider,
                    tools_used=tools_used,
                    classification_note=classification_note,
                )
                judge_results[qid] = verdict
                logging.info(
                    f"Question {qid}: verdict={verdict.get('verdict', 'n/a')} "
                    f"score={verdict.get('weighted_score', 'n/a')} "
                    f"mode={verdict.get('judge_mode', mode)}"
                )
            except Exception as e:
                logging.error(f"Question {qid}: judge error — {e}")

    tasks = [_judge_one(r) for r in run_results]
    await asyncio.gather(*tasks)
    return judge_results


def _append_history(
    run_dir_name: str,
    report: dict[str, Any],
    agent_model: str,
    agent_provider: str,
    judge_model: str,
    judge_provider: str,
) -> None:
    """Append a one-line JSON summary to runs/history.jsonl."""
    agg = report["aggregate"]
    # Latency metrics from run_stats
    run_stats = report.get("run_stats", {})
    latency = report.get("latency_analysis", {})
    cost_analysis = report.get("cost_analysis", {})
    budget_violations = report.get("budget_violations", {})

    entry = {
        "timestamp": run_dir_name,
        "questions_run": agg["count"],
        "avg_score": agg["avg_weighted_score"],
        "pass_rate": agg["pass_rate"],
        "pass": agg["pass_count"],
        "partial": agg["partial_count"],
        "fail": agg["fail_count"],
        "agent_model": agent_model,
        "agent_provider": agent_provider,
        "judge_model": judge_model,
        "judge_provider": judge_provider,
        "avg_duration_s": run_stats.get("avg_question_duration_s"),
        "p90_duration_ms": latency.get("p90_total_ms"),
        "avg_cost_usd": cost_analysis.get("avg_cost_per_question_usd"),
        "budget_violations": budget_violations.get("total_violations", 0),
    }
    history_path = EVALUATION_BASE_DIR / "runs" / "history.jsonl"
    with open(history_path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    logging.info(f"Appended run summary to {history_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run full Ask-Atlas evaluation pipeline"
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        type=str,
        help="Specific question IDs (e.g. --questions 1 2 6)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=f"Run only the smoke-test subset ({len(SMOKE_QUESTION_IDS)} curated questions)",
    )
    parser.add_argument(
        "--balanced",
        type=int,
        metavar="N",
        help="Auto-select N questions with balanced coverage across categories and difficulties",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent agent runs (default: 10)",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default="gpt-5-mini",
        help="LLM model for the judge (default: gpt-5-mini)",
    )
    parser.add_argument(
        "--judge-provider",
        type=str,
        default="openai",
        help="LLM provider for the judge (default: openai)",
    )
    parser.add_argument(
        "--skip-judge",
        action="store_true",
        help="Skip the LLM judge step (agent run + report only)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["auto", "sql_only", "graphql_sql", "graphql_only"],
        default=None,
        help="Agent mode override for all questions (default: use configured mode)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    logging.info("=" * 60)
    logging.info("Ask-Atlas Evaluation Pipeline")
    logging.info("=" * 60)

    # Step 1: Load question metadata
    questions_meta = _load_questions_meta()
    logging.info(f"Loaded metadata for {len(questions_meta)} questions")

    # Resolve question IDs (mutually exclusive: --questions, --smoke, --balanced)
    specified = sum(1 for x in [args.questions, args.smoke, args.balanced] if x)
    if specified > 1:
        logging.error("--questions, --smoke, and --balanced are mutually exclusive")
        return

    question_ids = args.questions
    if args.smoke:
        question_ids = SMOKE_QUESTION_IDS
        logging.info(f"Smoke-test mode: running {len(question_ids)} curated questions")
    elif args.balanced:
        question_ids = _select_balanced(questions_meta, args.balanced)
        logging.info(
            f"Balanced mode: selected {len(question_ids)} questions "
            f"across {len({questions_meta[q]['category'] for q in question_ids})} categories"
        )

    # Step 2: Run agent
    run_dir, run_results = await run_agent_evals(
        question_ids=question_ids,
        concurrency=args.concurrency,
        agent_mode=args.mode,
    )

    if not run_results:
        logging.error("No agent results produced. Aborting.")
        return

    # Step 3: Judge answers
    judge_results: dict[str, dict] = {}
    if not args.skip_judge:
        judge_results = await _judge_all(
            run_results=run_results,
            questions_meta=questions_meta,
            judge_model=args.judge_model,
            judge_provider=args.judge_provider,
            concurrency=args.concurrency,
        )
    else:
        logging.info("Skipping judge step (--skip-judge)")

    # Step 4: Generate report
    report = generate_report(
        run_results,
        judge_results,
        questions_meta,
        judge_model=args.judge_model,
        judge_provider=args.judge_provider,
    )
    json_path, md_path = save_report(report, run_dir)

    # Step 4b: Generate interactive HTML report
    html_path = generate_html_report(run_dir)

    # Step 5: Append to history.jsonl
    if not args.skip_judge:
        # Read agent model info from summary.json
        summary_path = run_dir / "summary.json"
        summary = load_json_file(summary_path) if summary_path.exists() else {}
        _append_history(
            run_dir_name=run_dir.name,
            report=report,
            agent_model=summary.get("agent_model", "unknown"),
            agent_provider=summary.get("agent_provider", "unknown"),
            judge_model=args.judge_model,
            judge_provider=args.judge_provider,
        )

    # Print summary
    agg = report["aggregate"]
    logging.info("=" * 60)
    logging.info("RESULTS SUMMARY")
    logging.info(f"  Questions evaluated: {agg['count']}")
    logging.info(f"  Avg weighted score:  {agg['avg_weighted_score']} / 5.0")
    logging.info(f"  Pass rate:           {agg['pass_rate']}%")
    logging.info(
        f"  Pass/Partial/Fail:   {agg['pass_count']}/{agg['partial_count']}/{agg['fail_count']}"
    )
    logging.info(f"  Report (md):         {md_path}")
    logging.info(f"  Report (html):       {html_path}")
    logging.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
