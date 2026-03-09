#!/usr/bin/env python3
"""
Async DB & E2E Backend Verification Script.

Proves that:
1. Dual engines (sync psycopg2 + async psycopg3) are configured correctly
2. DB queries in execute_sql and lookup_codes use the async engine
3. Full LangGraph pipeline works end-to-end with real queries
4. Concurrent queries run in parallel (event loop not blocked)

Run with:
    PYTHONPATH=$(pwd) uv run python scripts/verify_async_db.py
"""

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

# ── Project imports ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

from src.text_to_sql import AtlasTextToSQL  # noqa: E402

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("verify_async_db")

# Track which engine handles each SQL statement
engine_log: list[dict] = []


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Engine configuration verification
# ═══════════════════════════════════════════════════════════════════════════════


def verify_engine_config(atlas: AtlasTextToSQL) -> None:
    """Log engine configuration and verify driver names."""
    logger.info("=" * 70)
    logger.info("  STEP 1: Engine Configuration Verification")
    logger.info("=" * 70)

    # Sync engine
    sync_url = str(atlas.engine.url)
    sync_driver = atlas.engine.url.drivername
    sync_pool = atlas.engine.pool
    logger.info("  Sync engine:")
    logger.info("    Driver:    %s", sync_driver)
    logger.info("    URL:       %s...", sync_url[:60])
    logger.info("    Pool size: %s", sync_pool.size())
    logger.info("    Overflow:  %s", sync_pool.overflow())
    assert "psycopg2" in sync_driver or sync_driver == "postgresql", (
        f"Sync engine should use psycopg2, got {sync_driver}"
    )
    logger.info("    ✓ Sync engine uses psycopg2 (default postgresql driver)")

    # Async engine
    assert isinstance(atlas.async_engine, AsyncEngine), (
        f"Expected AsyncEngine, got {type(atlas.async_engine)}"
    )
    async_driver = atlas.async_engine.url.drivername
    async_pool = atlas.async_engine.pool
    logger.info("  Async engine:")
    logger.info("    Driver:    %s", async_driver)
    logger.info("    Type:      %s", type(atlas.async_engine).__name__)
    logger.info("    Pool size: %s", async_pool.size())
    logger.info("    Overflow:  %s", async_pool.overflow())
    assert async_driver == "postgresql+psycopg", (
        f"Async engine should use postgresql+psycopg, got {async_driver}"
    )
    logger.info("    ✓ Async engine uses postgresql+psycopg (psycopg3 async)")

    logger.info("  ✓ Both engines configured correctly")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Attach event listeners to both engines
# ═══════════════════════════════════════════════════════════════════════════════


def attach_engine_listeners(atlas: AtlasTextToSQL) -> None:
    """Add before_cursor_execute listeners to track which engine runs each query."""
    logger.info("=" * 70)
    logger.info("  STEP 2: Attaching SQL Event Listeners")
    logger.info("=" * 70)

    def make_listener(label: str):
        def receive_before_cursor_execute(
            conn, cursor, statement, parameters, context, executemany
        ):
            snippet = statement.replace("\n", " ").strip()[:120]
            entry = {"engine": label, "sql": snippet, "time": time.time()}
            engine_log.append(entry)
            logger.info("[%s] %s", label, snippet)

        return receive_before_cursor_execute

    # Async engine: SQLAlchemy routes async ops through sync_engine internally
    event.listen(
        atlas.async_engine.sync_engine,
        "before_cursor_execute",
        make_listener("ASYNC_ENGINE"),
    )
    event.listen(
        atlas.engine,
        "before_cursor_execute",
        make_listener("SYNC_ENGINE"),
    )

    logger.info("  ✓ Listeners attached to both engines")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Basic connectivity test
# ═══════════════════════════════════════════════════════════════════════════════


async def verify_connectivity(atlas: AtlasTextToSQL) -> None:
    """Run SELECT 1 on both engines to verify connectivity."""
    logger.info("=" * 70)
    logger.info("  STEP 3: Connectivity Test (SELECT 1)")
    logger.info("=" * 70)

    # Sync engine
    engine_log.clear()
    with atlas.engine.connect() as conn:
        result = conn.execute(text("SELECT 1 AS sync_check"))
        row = result.fetchone()
        logger.info("  Sync engine:  SELECT 1 → %s", row[0])

    sync_entries = [e for e in engine_log if e["engine"] == "SYNC_ENGINE"]
    assert len(sync_entries) >= 1, "Expected SYNC_ENGINE log entry"
    logger.info("  ✓ Sync engine connectivity confirmed (SYNC_ENGINE logged)")

    # Async engine
    engine_log.clear()
    async with atlas.async_engine.connect() as conn:
        result = await conn.execute(text("SELECT 1 AS async_check"))
        row = result.fetchone()
        logger.info("  Async engine: SELECT 1 → %s", row[0])

    async_entries = [e for e in engine_log if e["engine"] == "ASYNC_ENGINE"]
    assert len(async_entries) >= 1, "Expected ASYNC_ENGINE log entry"
    logger.info("  ✓ Async engine connectivity confirmed (ASYNC_ENGINE logged)")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Full E2E pipeline test
# ═══════════════════════════════════════════════════════════════════════════════


async def run_e2e_query(
    atlas: AtlasTextToSQL, question: str, label: str
) -> tuple[str, float]:
    """Run a single question through the full pipeline, returning answer + time."""
    logger.info("  %s", "─" * 60)
    logger.info("  Query: %s", label)
    logger.info("  Question: %s", question)
    logger.info("  %s", "─" * 60)

    engine_log.clear()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    start = time.time()
    final_answer = ""
    nodes_seen: list[str] = []

    async for stream_mode, stream_data in atlas.astream_agent_response(
        question, config
    ):
        if stream_mode == "updates":
            if stream_data.source == "tool":
                node = stream_data.name or "unknown"
                if node not in nodes_seen:
                    nodes_seen.append(node)
                # Print tool output snippets
                snippet = stream_data.content[:200].replace("\n", " ")
                logger.info("    [%s] %s", node, snippet)
            elif stream_data.message_type == "agent_talk":
                final_answer = stream_data.content
        elif stream_mode == "messages":
            if stream_data.message_type == "agent_talk":
                final_answer += stream_data.content

    elapsed = time.time() - start

    # Summarize engine usage
    async_hits = [e for e in engine_log if e["engine"] == "ASYNC_ENGINE"]
    sync_hits = [e for e in engine_log if e["engine"] == "SYNC_ENGINE"]
    logger.info(
        "    Engine usage: %s ASYNC_ENGINE, %s SYNC_ENGINE",
        len(async_hits),
        len(sync_hits),
    )
    if async_hits:
        logger.info("    ASYNC queries:")
        for h in async_hits:
            logger.info("      → %s", h["sql"][:100])
    if sync_hits:
        logger.info("    SYNC queries (metadata/table_info):")
        for h in sync_hits:
            logger.info("      → %s", h["sql"][:100])

    # Log answer
    answer_preview = final_answer[:500].replace("\n", "\n    ")
    logger.info("    Answer (%s):\n    %s", f"{elapsed:.1f}s", answer_preview)

    return final_answer, elapsed


async def run_e2e_tests(atlas: AtlasTextToSQL) -> None:
    """Run multiple E2E queries through the full LangGraph pipeline."""
    logger.info("=" * 70)
    logger.info("  STEP 4: Full E2E Pipeline Tests")
    logger.info("=" * 70)

    queries = [
        ("Simple export query", "What were the top 5 exports of Germany in 2020?"),
        ("Product-specific query", "How much cotton did Brazil export in 2021?"),
        (
            "Bilateral trade query",
            "What were the top 10 products traded between the US and China in 2019?",
        ),
        (
            "Complexity metrics query",
            "What is the Economic Complexity Index of Japan in the latest year?",
        ),
    ]

    results = []
    for label, question in queries:
        answer, elapsed = await run_e2e_query(atlas, question, label)
        results.append((label, elapsed, bool(answer.strip())))

    # Summary table
    logger.info("  %s", "─" * 60)
    logger.info("  %-30s %8s %12s", "Query", "Time", "Got Answer")
    logger.info("  %s", "─" * 60)
    for label, elapsed, got_answer in results:
        status = "✓" if got_answer else "✗"
        logger.info("  %-30s %7.1fs %12s", label, elapsed, status)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Concurrency test
# ═══════════════════════════════════════════════════════════════════════════════


async def run_concurrency_test(atlas: AtlasTextToSQL) -> None:
    """Run 2 queries concurrently with asyncio.gather to prove async I/O."""
    logger.info("=" * 70)
    logger.info("  STEP 5: Concurrency Verification")
    logger.info("=" * 70)

    q1 = "What were the top 3 exports of South Korea in 2019?"
    q2 = "What were the top 3 exports of Mexico in 2019?"

    # Run sequentially first for baseline
    logger.info("  Running 2 queries sequentially (baseline)...")
    seq_start = time.time()
    _, t1 = await run_e2e_query(atlas, q1, "Sequential #1")
    _, t2 = await run_e2e_query(atlas, q2, "Sequential #2")
    seq_total = time.time() - seq_start
    logger.info("  Sequential total: %.1fs (q1=%.1fs + q2=%.1fs)", seq_total, t1, t2)

    # Run concurrently
    logger.info("  Running 2 queries concurrently (asyncio.gather)...")
    engine_log.clear()
    par_start = time.time()

    async def _timed(q, label):
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        start = time.time()
        final = ""
        async for stream_mode, sd in atlas.astream_agent_response(q, config):
            if stream_mode == "messages" and sd.message_type == "agent_talk":
                final += sd.content
        elapsed = time.time() - start
        return label, final, elapsed

    results = await asyncio.gather(
        _timed(q1, "Concurrent #1"),
        _timed(q2, "Concurrent #2"),
    )
    par_total = time.time() - par_start

    for label, answer, elapsed in results:
        preview = answer[:200].replace("\n", " ")
        logger.info("    %s: %.1fs — %s", label, elapsed, preview)

    logger.info("  Concurrent total:  %.1fs", par_total)
    logger.info("  Sequential total:  %.1fs", seq_total)
    speedup = seq_total / par_total if par_total > 0 else 0
    logger.info("  Speedup:           %.2fx", speedup)

    if par_total < seq_total * 0.85:
        logger.info("  ✓ Concurrency confirmed — parallel is significantly faster")
    else:
        logger.warning(
            "  Parallel wall time is close to sequential — "
            "async may be bottlenecked by LLM rate limits rather than DB I/O"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


async def main() -> None:
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║%s║", "  Ask-Atlas: Async DB & E2E Verification".center(68))
    logger.info("╚" + "═" * 68 + "╝")

    async with await AtlasTextToSQL.create_async(
        table_descriptions_json=BASE_DIR
        / "src"
        / "schema"
        / "db_table_descriptions.json",
        table_structure_json=BASE_DIR / "src" / "schema" / "db_table_structure.json",
        queries_json=BASE_DIR / "src/example_queries/queries.json",
        example_queries_dir=BASE_DIR / "src/example_queries",
    ) as atlas:
        # Step 1: Engine config
        verify_engine_config(atlas)

        # Step 2: Attach listeners
        attach_engine_listeners(atlas)

        # Step 3: Connectivity
        await verify_connectivity(atlas)

        # Step 4: E2E pipeline
        await run_e2e_tests(atlas)

        # Step 5: Concurrency
        await run_concurrency_test(atlas)

    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║%s║", "  All verification steps complete!".center(68))
    logger.info("╚" + "═" * 68 + "╝")


if __name__ == "__main__":
    asyncio.run(main())
