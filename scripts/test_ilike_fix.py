#!/usr/bin/env python3
"""Adhoc test: verify the ILIKE double-counting fix for services queries.

Runs targeted questions through the SQL pipeline in sql_only mode that
would previously trigger ILIKE on name_short_en (causing double-counting).

Usage:
    PYTHONPATH=$(pwd) uv run python scripts/test_ilike_fix.py
"""

import asyncio
import json
import logging
import sys
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

QUERY_TIMEOUT = 180

# Queries specifically designed to test the ILIKE fix:
# 1. "Business services" — the original bug (code='ict', name='Business')
# 2. Other services categories where name != code
# 3. A goods query that might tempt ILIKE on product names
TEST_QUERIES = [
    {
        "question": "What is the value of Business services exports from the United States in 2020?",
        "check": "should use product_code = 'ict', NOT ILIKE '%Business%'",
        "expected_approx_value": 282e9,  # ~$282B, not $425B
    },
    {
        "question": "How much did the US export in Insurance and finance services in 2020?",
        "check": "should use product_code = 'financial', NOT ILIKE '%Insurance%'",
    },
    {
        "question": "What is the value of Travel and tourism services exports from India in 2020?",
        "check": "should use product_code = 'travel', NOT ILIKE '%Travel%'",
    },
    {
        "question": "What were the top service exports from Germany in 2024?",
        "check": "should query services_unilateral._2 with exact codes, no ILIKE",
    },
    {
        "question": "How much did Brazil export in Transport services in 2020?",
        "check": "should use product_code = 'transport', NOT ILIKE '%Transport%'",
    },
    {
        "question": "What is the total value of all service exports from the UK in 2024?",
        "check": "should use services_unilateral tables, possibly _1 with code='services'",
    },
]


def check_sql_for_ilike_on_name(sql_list: list[str]) -> list[str]:
    """Check if any SQL uses ILIKE/LIKE on name_short_en."""
    issues = []
    for sql in sql_list:
        sql_upper = sql.upper()
        if "NAME_SHORT_EN" in sql_upper and (
            "ILIKE" in sql_upper or "LIKE" in sql_upper
        ):
            issues.append(f"ILIKE/LIKE on name_short_en detected in SQL: {sql[:200]}")
    return issues


async def run_single_query(atlas, query_info: dict) -> dict:
    """Run a single query with a timeout."""
    question = query_info["question"]
    start = time.monotonic()
    try:
        answer = await asyncio.wait_for(
            atlas.aanswer_question(question, agent_mode="sql_only"),
            timeout=QUERY_TIMEOUT,
        )
        elapsed = time.monotonic() - start

        sql_list = []
        total_rows = 0
        if answer.queries:
            for q in answer.queries:
                sql_list.append(q.get("sql", ""))
                total_rows += q.get("row_count", 0)

        # Check for ILIKE on name_short_en
        ilike_issues = check_sql_for_ilike_on_name(sql_list)

        return {
            "question": question,
            "check": query_info["check"],
            "status": "FAIL_ILIKE" if ilike_issues else "SUCCESS",
            "elapsed_s": round(elapsed, 1),
            "sql": sql_list,
            "total_rows": total_rows,
            "num_queries": len(answer.queries) if answer.queries else 0,
            "answer_preview": (answer.answer or "")[:500],
            "ilike_issues": ilike_issues,
        }

    except TimeoutError:
        elapsed = time.monotonic() - start
        return {
            "question": question,
            "check": query_info["check"],
            "status": "TIMEOUT",
            "elapsed_s": round(elapsed, 1),
            "error": f"Timed out after {QUERY_TIMEOUT}s",
        }
    except Exception as e:
        elapsed = time.monotonic() - start
        return {
            "question": question,
            "check": query_info["check"],
            "status": "EXCEPTION",
            "elapsed_s": round(elapsed, 1),
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-500:],
        }


async def main():
    from src.streaming import AtlasTextToSQL

    logger.info("Creating AtlasTextToSQL instance (sql_only mode)...")
    atlas = await AtlasTextToSQL.create_async()

    results = []
    total = len(TEST_QUERIES)
    ilike_failures = 0

    for i, query_info in enumerate(TEST_QUERIES, 1):
        logger.info("[%s/%s] %s", i, total, query_info["question"])
        logger.info("  Expected: %s", query_info["check"])

        result = await run_single_query(atlas, query_info)
        results.append(result)

        status = result["status"]
        t = result["elapsed_s"]

        if status == "SUCCESS":
            logger.info("  -> %s (%ss, %s rows)", status, t, result["total_rows"])
            for sql in result.get("sql", []):
                logger.info("     SQL: %s", sql[:200])
            logger.info("     Answer: %s", result["answer_preview"][:200])
        elif status == "FAIL_ILIKE":
            ilike_failures += 1
            logger.error("  -> FAIL: ILIKE on name_short_en detected!")
            for issue in result["ilike_issues"]:
                logger.error("     %s", issue)
            for sql in result.get("sql", []):
                logger.error("     SQL: %s", sql[:200])
        else:
            logger.error("  -> %s (%ss): %s", status, t, result.get("error", "")[:200])

        logger.info("")

    # Final report
    logger.info("=" * 70)
    logger.info("ILIKE FIX TEST REPORT")
    logger.info("=" * 70)

    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    for status in ["SUCCESS", "FAIL_ILIKE", "TIMEOUT", "EXCEPTION"]:
        items = by_status.get(status, [])
        if items:
            logger.info("  %s: %s", status, len(items))

    success_count = len(by_status.get("SUCCESS", []))
    logger.info(
        "  Pass Rate: %s/%s (%s%%)",
        success_count,
        total,
        f"{success_count / total * 100:.0f}" if total else "0",
    )

    if ilike_failures:
        logger.error(
            "FAILED: %s queries still used ILIKE on name_short_en!", ilike_failures
        )

    # Save results
    out_path = "scripts/test_ilike_fix_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("Results saved to %s", out_path)

    await atlas.aclose()
    return ilike_failures == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
