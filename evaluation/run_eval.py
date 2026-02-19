#!/usr/bin/env python3
"""Single entry-point orchestrator for the Ask-Atlas evaluation pipeline.

Pipeline: load questions → run agent → load ground truth → judge answers → report.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --questions 1 2 6
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --concurrency 2 --judge-model gpt-5.2
    PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --skip-judge
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from utils import (
    EVALUATION_BASE_DIR,
    load_json_file,
    logging,
)
from run_agent_evals import run_agent_evals
from judge import judge_answer
from report import generate_report, save_report


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
        }
    return meta


def _load_ground_truth(question_id: str) -> list[dict] | None:
    """Load ground truth data for a question, or None if unavailable."""
    gt_path = EVALUATION_BASE_DIR / "results" / question_id / "ground_truth" / "results.json"
    if not gt_path.exists():
        return None
    try:
        gt = load_json_file(gt_path)
        data = gt.get("results", {}).get("data", [])
        return data if data else None
    except Exception:
        return None


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

        # If question has expected_behavior and no ground truth, treat as refusal
        if expected_behavior and ground_truth is None:
            gt_for_judge = None
        else:
            gt_for_judge = ground_truth

        async with semaphore:
            try:
                logging.info(f"Question {qid}: judging...")
                verdict = await judge_answer(
                    question=meta.get("text", result.get("question_text", "")),
                    agent_answer=result["answer"],
                    ground_truth_data=gt_for_judge,
                    expected_behavior=expected_behavior,
                    model=judge_model,
                    provider=judge_provider,
                )
                judge_results[qid] = verdict
                logging.info(
                    f"Question {qid}: verdict={verdict.get('verdict', 'n/a')} "
                    f"score={verdict.get('weighted_score', 'n/a')}"
                )
            except Exception as e:
                logging.error(f"Question {qid}: judge error — {e}")

    tasks = [_judge_one(r) for r in run_results]
    await asyncio.gather(*tasks)
    return judge_results


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
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent agent runs (default: 3)",
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
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()

    logging.info("=" * 60)
    logging.info("Ask-Atlas Evaluation Pipeline")
    logging.info("=" * 60)

    # Step 1: Load question metadata
    questions_meta = _load_questions_meta()
    logging.info(f"Loaded metadata for {len(questions_meta)} questions")

    # Step 2: Run agent
    run_dir, run_results = await run_agent_evals(
        question_ids=args.questions,
        concurrency=args.concurrency,
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
    report = generate_report(run_results, judge_results, questions_meta)
    json_path, md_path = save_report(report, run_dir)

    # Print summary
    agg = report["aggregate"]
    logging.info("=" * 60)
    logging.info("RESULTS SUMMARY")
    logging.info(f"  Questions evaluated: {agg['count']}")
    logging.info(f"  Avg weighted score:  {agg['avg_weighted_score']} / 5.0")
    logging.info(f"  Pass rate:           {agg['pass_rate']}%")
    logging.info(f"  Pass/Partial/Fail:   {agg['pass_count']}/{agg['partial_count']}/{agg['fail_count']}")
    logging.info(f"  Report:              {md_path}")
    logging.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
