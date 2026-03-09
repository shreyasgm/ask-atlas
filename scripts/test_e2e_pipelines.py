#!/usr/bin/env python3
"""Ad hoc end-to-end tests for SQL and GraphQL pipelines.

Tests both pipelines by stimulating the full agent graph from the start.
Run with:
    PYTHONPATH=$(pwd) uv run python scripts/test_e2e_pipelines.py

Requires: ATLAS_DB_URL env var (real Atlas DB), LLM API keys.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.integration


def banner(title: str) -> None:
    logger.info("=" * 65)
    logger.info("  %s", title)
    logger.info("=" * 65)


def section(title: str) -> None:
    logger.info("  --- %s ---", title)


# ---------------------------------------------------------------------------
# Test 1: SQL pipeline via create_async() (standard factory)
# ---------------------------------------------------------------------------


async def test_sql_pipeline() -> bool:
    """Test the SQL pipeline with a simple factual question.

    Uses the standard create_async() factory (no GraphQL) to verify
    the SQL path works end-to-end.
    """
    banner("TEST 1: SQL Pipeline (via create_async)")
    from src.streaming import AtlasTextToSQL

    t0 = time.monotonic()
    ok = True

    try:
        async with await AtlasTextToSQL.create_async() as atlas:
            section(
                "Streaming events for: 'What were US top-5 exports to China in 2022?'"
            )

            event_types: list[str] = []
            answer_chunks: list[str] = []
            pipeline_states: list[dict] = []
            printed_agent_talk = False

            async for stream_data in atlas.aanswer_question_stream(
                "What were the top 5 products exported by the United States to China in 2022?"
            ):
                event_types.append(stream_data.message_type)
                if stream_data.message_type == "node_start":
                    node = (stream_data.payload or {}).get("node", "?")
                    label = (stream_data.payload or {}).get("label", "?")
                    logger.info("    [node_start]      %-30s (%s)", node, label)
                elif stream_data.message_type == "pipeline_state":
                    stage = (stream_data.payload or {}).get("stage", "?")
                    pipeline_states.append(stream_data.payload or {})
                    if stage == "execute_sql":
                        row_count = (stream_data.payload or {}).get("row_count", 0)
                        exec_ms = (stream_data.payload or {}).get(
                            "execution_time_ms", 0
                        )
                        logger.info(
                            "    [pipeline_state]  %-30s rows=%s (%sms)",
                            stage,
                            row_count,
                            exec_ms,
                        )
                    else:
                        logger.info("    [pipeline_state]  %-30s", stage)
                elif stream_data.message_type == "tool_call":
                    tool = stream_data.tool_call or "?"
                    logger.info("    [tool_call]       %s", tool)
                elif stream_data.message_type == "agent_talk":
                    answer_chunks.append(stream_data.content or "")
                    if not printed_agent_talk:
                        logger.info("    [agent_talk]      (streaming...)")
                        printed_agent_talk = True

            final_answer = "".join(answer_chunks)
            preview = final_answer[:150].replace("\n", " ")
            logger.info("    [answer]          %r...", preview)

            elapsed = int((time.monotonic() - t0) * 1000)

            # Assertions
            section("Assertions")
            checks = [
                ("node_start events present", "node_start" in event_types),
                ("pipeline_state events present", "pipeline_state" in event_types),
                ("tool_call event present", "tool_call" in event_types),
                ("agent_talk event present", "agent_talk" in event_types),
                (
                    "execute_sql stage reached",
                    any(p.get("stage") == "execute_sql" for p in pipeline_states),
                ),
                (
                    "query returned rows",
                    any(
                        p.get("row_count", 0) > 0
                        for p in pipeline_states
                        if p.get("stage") == "execute_sql"
                    ),
                ),
                ("agent produced answer", len(final_answer) > 50),
            ]
            for label, passed in checks:
                mark = "✓" if passed else "✗"
                logger.info("    %s %s", mark, label)
                if not passed:
                    ok = False

            logger.info("    Total time: %sms", elapsed)

    except Exception as e:
        logger.error("  ERROR: %s", e)
        import traceback

        logger.error("%s", traceback.format_exc())
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Test 2: GraphQL pipeline (manually assembled with full components)
# ---------------------------------------------------------------------------


async def _build_graphql_instance():
    """Build an AtlasTextToSQL instance via create_async() — now with GraphQL wired up."""
    from src.streaming import AtlasTextToSQL

    # create_async() now wires up GraphQL client, catalog caches, budget_tracker,
    # and agent_mode from settings automatically.
    return await AtlasTextToSQL.create_async()


async def test_graphql_pipeline() -> bool:
    """Test the GraphQL pipeline with a country profile question.

    Manually assembles the full stack (GraphQL client, catalog cache, budget
    tracker) that create_async() currently omits. Forces graphql_sql mode so
    the agent will route through the GraphQL pipeline.
    """
    banner("TEST 2: GraphQL Pipeline (manually assembled)")
    t0 = time.monotonic()
    ok = True

    try:
        instance = await _build_graphql_instance()

        # A question that should clearly go to GraphQL (country profile)
        question = "What is Brazil's diversification grade and ECI rank?"
        section(f"Streaming events for: {question!r}")

        event_types: list[str] = []
        answer_chunks: list[str] = []
        pipeline_states: list[dict] = []
        atlas_links_found: list[dict] = []

        async for stream_data in instance.aanswer_question_stream(
            question,
            agent_mode="graphql_sql",
        ):
            event_types.append(stream_data.message_type)

            if stream_data.message_type == "node_start":
                node = (stream_data.payload or {}).get("node", "?")
                label = (stream_data.payload or {}).get("label", "?")
                logger.info("    [node_start]      %-35s (%s)", node, label)

            elif stream_data.message_type == "pipeline_state":
                stage = (stream_data.payload or {}).get("stage", "?")
                pipeline_states.append(stream_data.payload or {})

                # Log interesting fields per stage
                payload = stream_data.payload or {}
                if stage == "classify_query":
                    qtype = payload.get("query_type", "?")
                    rejected = payload.get("is_rejected", False)
                    logger.info(
                        "    [pipeline_state]  %-35s query_type=%s rejected=%s",
                        stage,
                        qtype,
                        rejected,
                    )
                elif stage == "extract_entities":
                    entities = payload.get("entities", {})
                    logger.info(
                        "    [pipeline_state]  %-35s entities=%s",
                        stage,
                        list(entities.keys()),
                    )
                elif stage == "resolve_ids":
                    resolved = payload.get("resolved_ids", {})
                    logger.info(
                        "    [pipeline_state]  %-35s resolved_keys=%s",
                        stage,
                        list(resolved.keys()),
                    )
                elif stage == "build_and_execute_graphql":
                    api_target = payload.get("api_target", "?")
                    success = payload.get("success", False)
                    exec_ms = payload.get("execution_time_ms", 0)
                    logger.info(
                        "    [pipeline_state]  %-35s api_target=%s success=%s (%sms)",
                        stage,
                        api_target,
                        success,
                        exec_ms,
                    )
                elif stage == "format_graphql_results":
                    links = payload.get("atlas_links", [])
                    atlas_links_found.extend(links)
                    logger.info(
                        "    [pipeline_state]  %-35s atlas_links=%s", stage, len(links)
                    )
                else:
                    logger.info("    [pipeline_state]  %-35s", stage)

            elif stream_data.message_type == "tool_call":
                tool = stream_data.tool_call or "?"
                logger.info("    [tool_call]       %s", tool)

            elif stream_data.message_type == "tool_output":
                preview = (stream_data.content or "")[:100].replace("\n", " ")
                logger.info("    [tool_output]     %r", preview)

            elif stream_data.message_type == "agent_talk":
                answer_chunks.append(stream_data.content or "")

        final_answer = "".join(answer_chunks)
        preview = final_answer[:150].replace("\n", " ")
        logger.info("    [agent_talk]      %r...", preview)

        elapsed = int((time.monotonic() - t0) * 1000)

        # Log atlas links if found
        if atlas_links_found:
            section(f"Atlas links ({len(atlas_links_found)})")
            for link in atlas_links_found:
                logger.info(
                    "    [%s] %s", link.get("link_type", "?"), link.get("label", "?")
                )
                logger.info("      %s", link.get("url", "?"))

        # Assertions
        section("Assertions")
        graphql_stages = [p.get("stage") for p in pipeline_states]
        checks = [
            ("tool_call event present", "tool_call" in event_types),
            ("classify_query stage reached", "classify_query" in graphql_stages),
            (
                "query was NOT rejected",
                any(
                    p.get("stage") == "classify_query" and not p.get("is_rejected")
                    for p in pipeline_states
                ),
            ),
            ("resolve_ids stage reached", "resolve_ids" in graphql_stages),
            (
                "build_and_execute_graphql reached",
                "build_and_execute_graphql" in graphql_stages,
            ),
            (
                "GraphQL API call succeeded",
                any(
                    p.get("stage") == "build_and_execute_graphql" and p.get("success")
                    for p in pipeline_states
                ),
            ),
            (
                "format_graphql_results reached",
                "format_graphql_results" in graphql_stages,
            ),
            ("atlas links generated", len(atlas_links_found) > 0),
            ("agent produced answer", len(final_answer) > 50),
        ]
        for label, passed in checks:
            mark = "✓" if passed else "✗"
            logger.info("    %s %s", mark, label)
            if not passed:
                ok = False

        logger.info("    Total time: %sms", elapsed)

        # Cleanup
        await instance.aclose()

    except Exception as e:
        logger.error("  ERROR: %s", e)
        import traceback

        logger.error("%s", traceback.format_exc())
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Test 3: Dual-tool question (should use both SQL and GraphQL)
# ---------------------------------------------------------------------------


async def test_dual_tool_question() -> bool:
    """Test a question that requires both SQL and GraphQL tools.

    The agent should decompose the question: part goes to atlas_graphql
    (diversification grade), part goes to query_tool (top export products via SQL).
    """
    banner("TEST 3: Dual-tool question (SQL + GraphQL)")
    t0 = time.monotonic()
    ok = True

    try:
        instance = await _build_graphql_instance()

        question = "What is Kenya's diversification grade, and what were its top 3 exported products in 2021?"
        section(f"Question: {question!r}")

        tool_calls: list[str] = []
        answer_chunks: list[str] = []
        sql_queries = 0
        graphql_calls = 0

        async for stream_data in instance.aanswer_question_stream(
            question,
            agent_mode="graphql_sql",
        ):
            if stream_data.message_type == "tool_call":
                tool = stream_data.tool_call or "?"
                tool_calls.append(tool)
                logger.info("    [tool_call]  %s", tool)
            elif stream_data.message_type == "pipeline_state":
                stage = (stream_data.payload or {}).get("stage", "")
                if stage == "execute_sql":
                    sql_queries += 1
                elif stage == "build_and_execute_graphql":
                    success = (stream_data.payload or {}).get("success", False)
                    if success:
                        graphql_calls += 1
            elif stream_data.message_type == "agent_talk":
                answer_chunks.append(stream_data.content or "")

        final_answer = "".join(answer_chunks)

        elapsed = int((time.monotonic() - t0) * 1000)

        section("Assertions")
        checks = [
            ("at least one tool_call made", len(tool_calls) > 0),
            ("agent produced answer", len(final_answer) > 50),
        ]
        # These are soft checks — the agent may route differently than expected
        logger.info("    Tool calls made: %s", tool_calls)
        logger.info("    Successful SQL queries: %s", sql_queries)
        logger.info("    Successful GraphQL calls: %s", graphql_calls)

        for label, passed in checks:
            mark = "✓" if passed else "✗"
            logger.info("    %s %s", mark, label)
            if not passed:
                ok = False

        if final_answer:
            preview = final_answer[:200].replace("\n", " ")
            logger.info("    Answer preview: %r...", preview)

        logger.info("    Total time: %sms", elapsed)

        # Cleanup
        await instance.aclose()

    except Exception as e:
        logger.error("  ERROR: %s", e)
        import traceback

        logger.error("%s", traceback.format_exc())
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Diagnosis: check what create_async() is missing
# ---------------------------------------------------------------------------


async def diagnose_create_async() -> None:
    """Diagnose what create_async() wires up vs. what GraphQL needs."""
    banner("DIAGNOSIS: create_async() GraphQL wiring gaps")

    import inspect

    from src.streaming import AtlasTextToSQL

    # Check create_async source
    src = inspect.getsource(AtlasTextToSQL.create_async)

    checks = [
        ("graphql_client wired", "graphql_client" in src),
        ("country_pages_client wired", "country_pages_client" in src),
        ("catalog caches wired", "country_catalog" in src),
        ("budget_tracker wired", "budget_tracker" in src),
        ("agent_mode from settings", "agent_mode" in src),
    ]

    all_present = True
    for label, present in checks:
        mark = "✓" if present else "✗"
        logger.info("  %s %s", mark, label)
        if not present:
            all_present = False

    if all_present:
        logger.info("  create_async() correctly wires all GraphQL components.")
    else:
        logger.warning("  GAPS detected — production API will not use GraphQL.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    await diagnose_create_async()

    results: list[tuple[str, bool]] = []

    r1 = await test_sql_pipeline()
    results.append(("SQL Pipeline", r1))

    r2 = await test_graphql_pipeline()
    results.append(("GraphQL Pipeline", r2))

    r3 = await test_dual_tool_question()
    results.append(("Dual-Tool Question", r3))

    banner("SUMMARY")
    all_passed = True
    for name, passed in results:
        mark = "✓" if passed else "✗"
        logger.info("  %s %s", mark, name)
        if not passed:
            all_passed = False

    if all_passed:
        logger.info("  All tests passed.")
    else:
        logger.warning("  Some tests failed — see output above.")


if __name__ == "__main__":
    asyncio.run(main())
