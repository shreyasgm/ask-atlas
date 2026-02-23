#!/usr/bin/env python3
"""
Execute manually verified SQL queries and generate ground truth results.

Run after queries have been manually reviewed and corrected in
evaluation/questions/{id}/queries/*.sql.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/generate_ground_truth.py
    PYTHONPATH=$(pwd) uv run python evaluation/generate_ground_truth.py --questions 1 2 6
"""

import argparse
import asyncio
import decimal
from typing import Any

import asyncpg

from utils import (
    EVALUATION_BASE_DIR,
    save_json_file,
    get_timestamp,
    logging,
)
from src.config import get_settings

settings = get_settings()


async def execute_sql_query(
    pool: asyncpg.Pool,
    query: str,
    query_file_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute a single SQL query via the shared connection pool."""
    start_time = get_timestamp()
    execution_log: dict[str, Any] = {
        "query_file": query_file_name,
        "start_time": start_time,
        "end_time": None,
        "status": None,
        "rows_returned": 0,
        "error_log": [],
    }

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query)

        results = []
        for row in rows:
            row_dict = dict(row)
            for key, value in row_dict.items():
                if isinstance(value, decimal.Decimal):
                    row_dict[key] = float(value)
            results.append(row_dict)

        execution_log["status"] = "success"
        execution_log["rows_returned"] = len(results)

    except Exception as e:
        execution_log["status"] = "failure"
        execution_log["error_log"].append(str(e))
        logging.error(f"Error executing query {query_file_name}: {e}")
        results = []

    finally:
        execution_log["end_time"] = get_timestamp()

    return results, execution_log


async def process_question_ground_truth(
    pool: asyncpg.Pool,
    question_id: str,
) -> bool:
    """Process ground truth generation for a single question. Returns True on success."""
    try:
        queries_dir = EVALUATION_BASE_DIR / "questions" / question_id / "queries"
        ground_truth_dir = (
            EVALUATION_BASE_DIR / "results" / question_id / "ground_truth"
        )
        ground_truth_dir.mkdir(parents=True, exist_ok=True)

        sql_files = sorted(queries_dir.glob("*.sql"))
        if not sql_files:
            logging.warning(f"No SQL files found for question {question_id}")
            return False

        combined_results: list[dict[str, Any]] = []
        execution_logs: list[dict[str, Any]] = []

        for sql_file in sql_files:
            query = sql_file.read_text()
            results, exec_log = await execute_sql_query(pool, query, sql_file.name)
            combined_results.extend(results)
            execution_logs.append(exec_log)

        ground_truth = {
            "question_id": question_id,
            "execution_timestamp": get_timestamp(),
            "results": {"data": combined_results},
            "execution_stats": {
                "queries_executed": len(sql_files),
                "total_rows": len(combined_results),
            },
        }

        save_json_file(ground_truth_dir / "results.json", ground_truth)
        save_json_file(
            ground_truth_dir / "execution_log.json",
            {"steps": execution_logs, "error_log": []},
        )

        any_failure = any(log["status"] == "failure" for log in execution_logs)
        if any_failure:
            logging.warning(f"Question {question_id}: some queries failed")
            return False

        logging.info(
            f"Question {question_id}: ground truth generated "
            f"({len(combined_results)} rows from {len(sql_files)} queries)"
        )
        return True

    except Exception as e:
        logging.error(f"Error processing ground truth for {question_id}: {e}")
        return False


def _discover_question_ids() -> list[str]:
    """Return sorted numeric question IDs from evaluation/questions/."""
    questions_dir = EVALUATION_BASE_DIR / "questions"
    ids = []
    for p in questions_dir.iterdir():
        if p.is_dir() and p.name.isdigit():
            ids.append(p.name)
    return sorted(ids, key=int)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate ground truth results")
    parser.add_argument(
        "--questions",
        nargs="+",
        type=str,
        help="Specific question IDs to regenerate (e.g. --questions 1 2 6)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    logging.info("Starting ground truth generation...")

    all_ids = _discover_question_ids()
    if args.questions:
        question_ids = [qid for qid in args.questions if qid in all_ids]
        missing = set(args.questions) - set(all_ids)
        if missing:
            logging.warning(f"Question IDs not found, skipping: {missing}")
    else:
        question_ids = all_ids

    if not question_ids:
        logging.error("No questions to process.")
        return

    logging.info(f"Processing {len(question_ids)} questions: {question_ids}")

    pool = await asyncpg.create_pool(settings.atlas_db_url, min_size=2, max_size=8)
    try:
        results = await asyncio.gather(
            *(process_question_ground_truth(pool, qid) for qid in question_ids)
        )
    finally:
        await pool.close()

    succeeded = sum(1 for r in results if r)
    failed = sum(1 for r in results if not r)
    logging.info(
        f"Ground truth generation complete. "
        f"Succeeded: {succeeded}, Failed: {failed}, Total: {len(question_ids)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
