#!/usr/bin/env python3
"""Run the Atlas agent against evaluation questions and capture results.

Uses AtlasTextToSQL.create_async() → aanswer_question() for each question.
After each answer, extracts pipeline_sql from the checkpointed graph state.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/run_agent_evals.py
    PYTHONPATH=$(pwd) uv run python evaluation/run_agent_evals.py --questions 1 2 6 --concurrency 2
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import time
import uuid
from pathlib import Path
from typing import Any

from utils import (
    BASE_DIR,
    EVALUATION_BASE_DIR,
    load_json_file,
    save_json_file,
    get_timestamp,
    logging,
)
from src.text_to_sql import AtlasTextToSQL


def _load_questions_index() -> dict[str, dict]:
    """Load question metadata from eval_questions.json, keyed by string ID.

    This is the single source of truth for question text, category, and difficulty.
    """
    eval_data = load_json_file(EVALUATION_BASE_DIR / "eval_questions.json")
    categories = {cat["id"]: cat["name"] for cat in eval_data["categories"]}
    index: dict[str, dict] = {}
    for q in eval_data["questions"]:
        qid = str(q["id"])
        index[qid] = {
            "text": q["text"],
            "category": categories.get(q["category_id"], q["category_id"]),
            "difficulty": q["difficulty"],
            "expected_behavior": q.get("expected_behavior"),
        }
    return index


def _discover_question_ids() -> list[str]:
    """Return sorted question IDs from eval_questions.json."""
    index = _load_questions_index()
    return sorted(index.keys(), key=int)


async def run_single_question(
    atlas: AtlasTextToSQL,
    question_id: str,
    question_meta: dict,
    run_dir: Path,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Run the agent on a single question and save results.

    Args:
        atlas: The AtlasTextToSQL agent instance.
        question_id: Numeric question ID (as string).
        question_meta: Metadata from eval_questions.json (text, category, difficulty).
        run_dir: Directory for this run's output.
        semaphore: Concurrency limiter.

    Returns a result dict with question_id, answer, sql, duration_s, status, error.
    """
    async with semaphore:
        question_text = question_meta["text"]

        result: dict[str, Any] = {
            "question_id": question_id,
            "question_text": question_text,
            "category": question_meta.get("category", ""),
            "difficulty": question_meta.get("difficulty", ""),
            "answer": None,
            "sql": None,
            "duration_s": None,
            "status": "error",
            "error": None,
            "timestamp": get_timestamp(),
        }

        thread_id = f"eval_{question_id}_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}

        logging.info(f"Question {question_id}: starting — {question_text[:80]}")
        t0 = time.monotonic()

        try:
            # Stream through agent to get final answer
            answer = None
            async for step in atlas.agent.astream(
                atlas._turn_input(question_text),
                stream_mode="values",
                config=config,
            ):
                message = step["messages"][-1]
            answer = atlas._extract_text(message.content)
            result["answer"] = answer

            # Extract pipeline_sql from checkpointed state
            try:
                state = await atlas.agent.aget_state(config)
                result["sql"] = state.values.get("pipeline_sql", "")
            except Exception:
                result["sql"] = ""

            result["status"] = "success"

        except Exception as e:
            result["error"] = str(e)
            logging.error(f"Question {question_id}: agent error — {e}")

        result["duration_s"] = round(time.monotonic() - t0, 2)

        # Save per-question result
        question_run_dir = run_dir / question_id
        question_run_dir.mkdir(parents=True, exist_ok=True)
        save_json_file(question_run_dir / "result.json", result)

        status_emoji = "OK" if result["status"] == "success" else "FAIL"
        logging.info(
            f"Question {question_id}: {status_emoji} "
            f"({result['duration_s']}s, {len(result.get('answer') or '')} chars)"
        )
        return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run agent evaluations")
    parser.add_argument(
        "--questions",
        nargs="+",
        type=str,
        help="Specific question IDs to run (e.g. --questions 1 2 6)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent agent runs (default: 3)",
    )
    return parser.parse_args()


async def run_agent_evals(
    question_ids: list[str] | None = None,
    concurrency: int = 3,
) -> tuple[Path, list[dict[str, Any]]]:
    """Run the agent on evaluation questions.

    Args:
        question_ids: Specific question IDs to run, or None for all.
        concurrency: Maximum concurrent agent runs.

    Returns:
        Tuple of (run_dir, list_of_results).
    """
    questions_index = _load_questions_index()
    all_ids = sorted(questions_index.keys(), key=int)

    if question_ids:
        ids_to_run = [qid for qid in question_ids if qid in questions_index]
        missing = set(question_ids) - set(questions_index)
        if missing:
            logging.warning(f"Question IDs not found, skipping: {missing}")
    else:
        ids_to_run = all_ids

    if not ids_to_run:
        logging.error("No questions to run.")
        return Path(), []

    # Create timestamped run directory
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = EVALUATION_BASE_DIR / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    logging.info(
        f"Starting agent evals: {len(ids_to_run)} questions, "
        f"concurrency={concurrency}, run_dir={run_dir}"
    )

    # Capture agent model info from settings
    from src.config import get_settings

    settings = get_settings()
    agent_model = settings.frontier_model
    agent_provider = settings.frontier_model_provider

    semaphore = asyncio.Semaphore(concurrency)

    async with await AtlasTextToSQL.create_async(
        table_descriptions_json=BASE_DIR / "db_table_descriptions.json",
        table_structure_json=BASE_DIR / "db_table_structure.json",
        queries_json=BASE_DIR / "src/example_queries/queries.json",
        example_queries_dir=BASE_DIR / "src/example_queries",
    ) as atlas:
        tasks = [
            run_single_question(atlas, qid, questions_index[qid], run_dir, semaphore)
            for qid in ids_to_run
        ]
        results = await asyncio.gather(*tasks)

    results = list(results)

    # Save summary
    succeeded = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] != "success")
    summary = {
        "timestamp": timestamp,
        "questions_run": len(ids_to_run),
        "succeeded": succeeded,
        "failed": failed,
        "agent_model": agent_model,
        "agent_provider": agent_provider,
        "results": results,
    }
    save_json_file(run_dir / "summary.json", summary)

    logging.info(
        f"Agent evals complete. Succeeded: {succeeded}, Failed: {failed}, "
        f"Total: {len(ids_to_run)}"
    )

    return run_dir, results


async def main() -> None:
    args = _parse_args()
    await run_agent_evals(
        question_ids=args.questions,
        concurrency=args.concurrency,
    )


if __name__ == "__main__":
    asyncio.run(main())
