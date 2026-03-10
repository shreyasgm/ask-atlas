#!/usr/bin/env python3
"""Smoke-test the SQL pipeline with a diverse set of questions.

Runs 21 questions through AtlasTextToSQL in sql_only mode, covering
regional aggregates, RCA counting, CTEs, subqueries, cross-schema JOINs,
window functions, services, and edge cases. Records status, timing,
generated SQL, and answer previews.

Results are saved incrementally so a crash on one query doesn't lose prior results.

Usage:
    PYTHONPATH=$(pwd) uv run python scripts/smoke_test_sql_pipeline.py
"""

import asyncio
import json
import logging
import sys
import time
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

# Per-query timeout in seconds
QUERY_TIMEOUT = 120

# Test queries: mix of eval questions + imaginative stress-test queries
TEST_QUERIES = [
    # --- From eval set: previously failing due to false positives ---
    # Regional aggregates (Q230-238 pattern)
    "What is the total export value of Sub-Saharan Africa according to the Atlas?",
    "What is the 5-year export growth rate for the European Union?",
    "Which countries belong to the European Union group according to the Atlas?",
    # RCA counting (Q121, Q124 pattern)
    "How many products does Kenya export with a revealed comparative advantage (RCA > 1)?",
    "How many products does Turkiye export with a revealed comparative advantage (RCA > 1)?",
    # --- Basic queries (should work cleanly) ---
    "What is the total value of exports for Brazil in 2018?",
    "What were the top 3 exported products from India in 2020?",
    "What are the main export destinations for Japan in 2021?",
    # --- Queries that exercise CTE aliases ---
    "What is the most recent year of data available for Germany's exports?",
    "Which country had the highest ECI in 2020?",
    # --- Queries that exercise aggregation aliases ---
    "List the top 10 countries by total export value in 2024, ranked from highest to lowest.",
    # --- Queries that exercise subqueries ---
    "What percentage of global exports does China account for in 2024?",
    # --- Cross-schema JOINs (hs92 + classification) ---
    "What is the export value of crude petroleum from Saudi Arabia in 2020?",
    "What are the top 5 products that Brazil exports with the highest RCA?",
    # --- Window functions ---
    "Rank the top 10 exporters globally in 2024 by total export value.",
    # --- Services schema ---
    "What is the value of service exports for the United Kingdom in 2024?",
    # --- Multi-schema (goods + services) ---
    "What percentage of Switzerland's total exports are services in 2024?",
    # --- Complex analytical queries ---
    "How has Mexico's export diversification changed between 2010 and 2020?",
    "Compare coffee exports between Colombia and Vietnam in 2024.",
    "What is Germany's market share in global automotive exports?",
    # --- Edge cases ---
    "What is the Economic Complexity Index of Rwanda in 2024?",
    "What products does Ethiopia export with RCA > 2?",
]

OUTPUT_PATH = Path("scripts/smoke_test_sql_pipeline_results.json")


def save_results(results: list[dict]) -> None:
    """Save results to disk incrementally."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, default=str)


async def run_single_query(atlas, question: str) -> dict:
    """Run a single query with a timeout."""
    start = time.monotonic()
    try:
        answer = await asyncio.wait_for(
            atlas.aanswer_question(question, agent_mode="sql_only"),
            timeout=QUERY_TIMEOUT,
        )
        elapsed = time.monotonic() - start

        status = "SUCCESS"
        sql_list = []
        total_rows = 0
        validation_error = ""

        if answer.queries:
            for q in answer.queries:
                sql_list.append(q.get("sql", ""))
                total_rows += q.get("row_count", 0)

        answer_text = answer.answer or ""
        if "validation failed" in answer_text.lower():
            status = "VALIDATION_FAIL"
            validation_error = answer_text
        elif not answer.queries and "error" in answer_text.lower():
            status = "ERROR"

        return {
            "question": question,
            "status": status,
            "elapsed_s": round(elapsed, 1),
            "sql": sql_list,
            "total_rows": total_rows,
            "num_queries": len(answer.queries) if answer.queries else 0,
            "answer_preview": answer_text[:300] if answer_text else "",
            "validation_error": validation_error[:300] if validation_error else "",
        }

    except TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "question": question,
            "status": "TIMEOUT",
            "elapsed_s": round(elapsed, 1),
            "error": f"Timed out after {QUERY_TIMEOUT}s",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        tb = traceback.format_exc()
        return {
            "question": question,
            "status": "EXCEPTION",
            "elapsed_s": round(elapsed, 1),
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb[-500:],
        }


async def run_test():
    """Run all test queries and report results."""
    from src.streaming import AtlasTextToSQL

    logger.info("Creating AtlasTextToSQL instance (sql_only mode)...")
    atlas = await AtlasTextToSQL.create_async()

    results = []
    total = len(TEST_QUERIES)

    for i, question in enumerate(TEST_QUERIES, 1):
        logger.info("[%s/%s] %s", i, total, question)

        result = await run_single_query(atlas, question)
        results.append(result)

        # Print one-line summary
        s = result["status"]
        t = result["elapsed_s"]
        if s == "SUCCESS":
            logger.info(
                "  -> %s (%ss, %s rows, %s queries)",
                s,
                t,
                result["total_rows"],
                result["num_queries"],
            )
            for sql in result.get("sql", []):
                logger.info("     SQL: %s", sql[:140])
        elif s == "VALIDATION_FAIL":
            logger.warning("  -> %s (%ss): %s", s, t, result["validation_error"][:150])
        else:
            logger.error(
                "  -> %s (%ss): %s",
                s,
                t,
                result.get("error", result.get("answer_preview", ""))[:150],
            )

        # Save after every query
        save_results(results)

    # Final report
    logger.info("=" * 70)
    logger.info("FINAL REPORT")
    logger.info("=" * 70)

    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    for status in ["SUCCESS", "VALIDATION_FAIL", "ERROR", "TIMEOUT", "EXCEPTION"]:
        items = by_status.get(status, [])
        if items or status == "SUCCESS":
            logger.info("  %s: %s", status, len(items))

    success_count = len(by_status.get("SUCCESS", []))
    logger.info(
        "  Success Rate: %s/%s (%s%%)",
        success_count,
        total,
        f"{success_count / total * 100:.0f}",
    )

    val_fails = by_status.get("VALIDATION_FAIL", [])
    if val_fails:
        logger.warning("VALIDATION FAILURES (likely false positives):")
        for r in val_fails:
            logger.warning("  - %s", r["question"])
            logger.warning("    %s", r["validation_error"][:200])

    logger.info("Results saved to %s", OUTPUT_PATH)

    await atlas.aclose()
    return len(val_fails) == 0


if __name__ == "__main__":
    success = asyncio.run(run_test())
    sys.exit(0 if success else 1)
