#!/usr/bin/env python3
"""DB-only load test for AsyncSQLDatabaseWithSchemas.

Fires N concurrent SQL queries directly at the async engine to stress-test
the connection pool, measure query latency, and verify true async concurrency.

Usage:
    # Against Docker test DB (port 5433):
    ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
        uv run python scripts/load_test_db.py

    # Against production Atlas DB:
    ATLAS_DB_URL=<production-url> uv run python scripts/load_test_db.py --concurrency 20

    # Custom queries:
    uv run python scripts/load_test_db.py --concurrency 10 --rounds 5
"""

import argparse
import asyncio
import logging
import statistics
import sys
import time

from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure project root is on sys.path
sys.path.insert(0, ".")

from src.config import get_settings
from src.db_pool_health import attach_pool_listeners, metrics
from src.sql_multiple_schemas import AsyncSQLDatabaseWithSchemas

logger = logging.getLogger(__name__)

# Representative SQL queries of varying complexity against the Atlas DB.
# Column names: country_id, partner_id, product_id (not location_code etc.)
QUERIES = [
    # Fast: simple lookup
    "SELECT iso3_code, name_en FROM classification.location_country WHERE iso3_code = 'USA'",
    # Medium: aggregation over country-product data
    "SELECT country_id, SUM(export_value) as total FROM hs92.country_product_year_4 WHERE year = 2020 GROUP BY country_id ORDER BY total DESC LIMIT 10",
    # Medium: join country_year with classification
    "SELECT c.name_en, cy.export_value, cy.year FROM hs92.country_year cy JOIN classification.location_country c ON cy.country_id = c.country_id WHERE cy.year = 2019 LIMIT 20",
    # Slow: large scan — bilateral trade with aggregation
    "SELECT country_id, partner_id, SUM(export_value) as total FROM hs92.country_country_product_year_4 WHERE year = 2018 GROUP BY country_id, partner_id ORDER BY total DESC LIMIT 50",
    # Fast: count
    "SELECT COUNT(*) FROM classification.product_hs92",
    # Medium: distinct values
    "SELECT DISTINCT year FROM hs92.country_year ORDER BY year DESC LIMIT 10",
    # Medium: product-level aggregation
    "SELECT product_id, SUM(export_value) as total FROM hs92.product_year_4 WHERE year = 2020 GROUP BY product_id ORDER BY total DESC LIMIT 20",
    # Slow: multi-table join with filter
    "SELECT c.name_en, p.code, cpy.export_value FROM hs92.country_product_year_4 cpy JOIN classification.location_country c ON cpy.country_id = c.country_id JOIN classification.product_hs92 p ON cpy.product_id = p.product_id WHERE cpy.year = 2019 AND c.iso3_code = 'BOL' ORDER BY cpy.export_value DESC LIMIT 20",
]


async def run_single_query(
    db: AsyncSQLDatabaseWithSchemas, query: str, query_id: int
) -> dict:
    """Execute a single query and return timing info."""
    t_start = time.monotonic()
    try:
        result = await db._aexecute(query)
        elapsed_ms = (time.monotonic() - t_start) * 1000
        return {
            "query_id": query_id,
            "elapsed_ms": elapsed_ms,
            "rows": len(result),
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        return {
            "query_id": query_id,
            "elapsed_ms": elapsed_ms,
            "rows": 0,
            "error": str(e),
        }


async def run_concurrent_batch(
    db: AsyncSQLDatabaseWithSchemas,
    concurrency: int,
    round_num: int,
) -> list[dict]:
    """Fire `concurrency` queries simultaneously and collect results."""
    tasks = []
    for i in range(concurrency):
        query = QUERIES[i % len(QUERIES)]
        query_id = round_num * concurrency + i
        tasks.append(run_single_query(db, query, query_id))

    batch_start = time.monotonic()
    results = await asyncio.gather(*tasks)
    batch_elapsed = (time.monotonic() - batch_start) * 1000

    logger.info(
        "  Round %s: %s queries in %sms",
        round_num + 1,
        concurrency,
        f"{batch_elapsed:.0f}",
    )
    return list(results)


async def main(concurrency: int, rounds: int, pool_size: int, max_overflow: int):
    settings = get_settings()
    if not settings.atlas_db_url:
        logger.error("ATLAS_DB_URL not set. Export it or add to .env")
        sys.exit(1)

    logger.info("DB Load Test")
    logger.info("  Concurrency: %s", concurrency)
    logger.info("  Rounds: %s", rounds)
    logger.info("  Pool: size=%s, max_overflow=%s", pool_size, max_overflow)
    logger.info("  Total queries: %s", concurrency * rounds)
    logger.info("")

    # Create async engine with configurable pool
    async_url = make_url(settings.atlas_db_url).set(drivername="postgresql+psycopg")
    engine = create_async_engine(
        async_url,
        execution_options={"postgresql_readonly": True},
        connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=90000",
        },
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )
    attach_pool_listeners(engine, label="async-loadtest")

    logger.info("Reflecting metadata...")
    db = await AsyncSQLDatabaseWithSchemas.create(
        engine, schemas=["hs92", "classification"]
    )
    logger.info("  Tables: %s", len(db.get_usable_table_names()))
    logger.info("")

    # Run load test
    all_results = []
    logger.info("Running queries...")
    overall_start = time.monotonic()

    for round_num in range(rounds):
        batch_results = await run_concurrent_batch(db, concurrency, round_num)
        all_results.extend(batch_results)

    overall_elapsed = (time.monotonic() - overall_start) * 1000

    # Report
    logger.info("")
    logger.info("%s", "=" * 60)
    logger.info("RESULTS")
    logger.info("%s", "=" * 60)

    timings = [r["elapsed_ms"] for r in all_results]
    errors = [r for r in all_results if r["error"]]

    logger.info("  Total queries:    %s", len(all_results))
    logger.info("  Errors:           %s", len(errors))
    logger.info("  Overall time:     %sms", f"{overall_elapsed:.0f}")
    logger.info(
        "  Throughput:       %s queries/sec",
        f"{len(all_results) / (overall_elapsed / 1000):.1f}",
    )
    logger.info("")
    logger.info("  Latency (ms):")
    logger.info("    avg:  %s", f"{statistics.mean(timings):.0f}")
    logger.info("    p50:  %s", f"{statistics.median(timings):.0f}")
    logger.info("    p95:  %s", f"{sorted(timings)[int(len(timings) * 0.95)]:.0f}")
    logger.info("    p99:  %s", f"{sorted(timings)[int(len(timings) * 0.99)]:.0f}")
    logger.info("    max:  %s", f"{max(timings):.0f}")
    logger.info("    min:  %s", f"{min(timings):.0f}")

    if errors:
        logger.info("")
        logger.error("  Errors:")
        for e in errors[:5]:
            logger.error("    Query %s: %s", e["query_id"], e["error"][:100])

    # Pool metrics from the metrics store
    logger.info("")
    logger.info("  Pool metrics (from db_pool_health):")
    qs = metrics.query_latency_summary()
    cs = metrics.connection_hold_summary()
    logger.info("    Query latency:    %s", qs)
    logger.info("    Connection hold:  %s", cs)
    slow = metrics.recent_slow_queries(threshold_ms=1000)
    if slow:
        logger.info("    Slow queries (>1s): %s", len(slow))
        for sq in slow:
            logger.info(
                "      %sms: %s", f"{sq['elapsed_ms']:.0f}", sq["sql_preview"][:80]
            )

    # Concurrency analysis: check if queries actually overlapped
    logger.info("")
    logger.info("  Concurrency analysis:")
    # If truly concurrent, batch time should be close to max single query time
    # not sum of all queries
    for round_num in range(min(rounds, 3)):
        batch = all_results[round_num * concurrency : (round_num + 1) * concurrency]
        batch_timings = [r["elapsed_ms"] for r in batch]
        sum_serial = sum(batch_timings)
        max_parallel = max(batch_timings)
        speedup = sum_serial / max_parallel if max_parallel > 0 else 0
        logger.info(
            "    Round %s: serial=%sms  max_single=%sms  speedup=%sx",
            round_num + 1,
            f"{sum_serial:.0f}",
            f"{max_parallel:.0f}",
            f"{speedup:.1f}",
        )

    await engine.dispose()
    logger.info("")
    logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DB-only load test")
    parser.add_argument(
        "--concurrency", type=int, default=10, help="Concurrent queries per round"
    )
    parser.add_argument("--rounds", type=int, default=5, help="Number of rounds")
    parser.add_argument("--pool-size", type=int, default=5, help="Connection pool size")
    parser.add_argument(
        "--max-overflow", type=int, default=10, help="Max pool overflow"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )

    asyncio.run(main(args.concurrency, args.rounds, args.pool_size, args.max_overflow))
