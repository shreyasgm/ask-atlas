#!/usr/bin/env python3
"""Trace the SQL pipeline node-by-node for a set of queries.

Streams every graph step and logs validation errors, retries, SQL changes,
and agent decisions so we can see exactly why queries loop.

Results saved incrementally to scripts/trace_sql_pipeline_results.json.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

# Per-query timeout — aggressive to avoid burning time
QUERY_TIMEOUT = 90

# Queries chosen to provoke retries and looping behavior
TRACE_QUERIES = [
    # These timed out in the stress test — likely looping
    "Which countries belong to the European Union group according to the Atlas?",
    "Which country had the highest ECI in 2020?",
    "List the top 10 countries by total export value in 2022, ranked from highest to lowest.",
    # These succeeded but slowly (multiple queries) — worth tracing
    "How many products does Kenya export with a revealed comparative advantage (RCA > 1)?",
    # A clean fast one for comparison
    "What is the total value of exports for Brazil in 2018?",
]

OUTPUT_PATH = Path("scripts/trace_sql_pipeline_results.json")


def _msg_summary(msg) -> str:
    """One-line summary of a message."""
    if isinstance(msg, HumanMessage):
        return f"Human: {msg.content[:100]}"
    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            tools = [tc["name"] for tc in msg.tool_calls]
            return f"AI: [tool_calls: {', '.join(tools)}]"
        return f"AI: {(msg.content or '')[:120]}"
    if isinstance(msg, ToolMessage):
        preview = (msg.content or "")[:100]
        return f"Tool({msg.name}): {preview}"
    return f"{type(msg).__name__}: {str(msg)[:80]}"


async def trace_single_query(atlas, question: str) -> dict:
    """Stream one query through the graph, logging every node transition."""
    thread_id = f"trace_{uuid.uuid4().hex[:8]}"
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 80,  # tighter limit to avoid runaway loops
    }
    turn_input = atlas._turn_input(question, agent_mode="sql_only")

    trace_log: list[dict] = []
    prev_sql = None
    prev_retry = 0
    prev_error = ""
    prev_queries_executed = 0
    step_count = 0
    final_answer = ""

    t0 = time.monotonic()

    try:
        async for step in atlas.agent.astream(
            turn_input,
            stream_mode="updates",  # gives us {node_name: state_update} per step
            config=config,
        ):
            step_count += 1
            elapsed = round(time.monotonic() - t0, 1)

            for node_name, update in step.items():
                entry: dict = {
                    "step": step_count,
                    "node": node_name,
                    "elapsed_s": elapsed,
                }

                # Track SQL changes
                new_sql = update.get("pipeline_sql")
                if new_sql and new_sql != prev_sql:
                    entry["sql"] = new_sql
                    prev_sql = new_sql

                # Track validation / execution errors
                new_error = update.get("last_error", "")
                if new_error and new_error != prev_error:
                    entry["error"] = new_error
                    prev_error = new_error

                # Track retry count changes
                new_retry = update.get("retry_count")
                if new_retry is not None and new_retry != prev_retry:
                    entry["retry_count"] = new_retry
                    prev_retry = new_retry

                # Track sql_history entries
                sql_hist = update.get("pipeline_sql_history")
                if sql_hist:
                    entry["sql_history"] = sql_hist

                # Track queries_executed changes
                new_qe = update.get("queries_executed")
                if new_qe is not None and new_qe != prev_queries_executed:
                    entry["queries_executed"] = new_qe
                    prev_queries_executed = new_qe

                # Track messages (agent decisions, tool results)
                msgs = update.get("messages")
                if msgs:
                    if isinstance(msgs, list):
                        entry["messages"] = [_msg_summary(m) for m in msgs]
                    else:
                        entry["messages"] = [_msg_summary(msgs)]

                    # Capture final answer
                    for m in msgs if isinstance(msgs, list) else [msgs]:
                        if isinstance(m, AIMessage) and not m.tool_calls and m.content:
                            final_answer = m.content

                trace_log.append(entry)

                # Log live
                if "error" in entry:
                    logger.error(
                        "  [%5.1fs] %s  ERROR: %s",
                        elapsed,
                        node_name,
                        entry["error"][:100],
                    )
                elif "sql" in entry:
                    logger.info(
                        "  [%5.1fs] %s  SQL: %s...",
                        elapsed,
                        node_name,
                        entry["sql"][:80],
                    )
                elif "messages" in entry:
                    for m in entry["messages"]:
                        logger.info("  [%5.1fs] %s  %s", elapsed, node_name, m[:100])
                else:
                    logger.info("  [%5.1fs] %s", elapsed, node_name)

    except TimeoutError:
        elapsed = round(time.monotonic() - t0, 1)
        trace_log.append(
            {"step": step_count + 1, "node": "TIMEOUT", "elapsed_s": elapsed}
        )
        logger.warning("  [%5.1fs] TIMEOUT", elapsed)
    except Exception as e:
        elapsed = round(time.monotonic() - t0, 1)
        trace_log.append(
            {
                "step": step_count + 1,
                "node": "EXCEPTION",
                "elapsed_s": elapsed,
                "error": f"{type(e).__name__}: {e}",
            }
        )
        logger.error("  [%5.1fs] EXCEPTION: %s", elapsed, e)

    total_elapsed = round(time.monotonic() - t0, 1)

    # Count retries and tool calls from trace
    validation_errors = [
        e
        for e in trace_log
        if e.get("error") and "validation" in e.get("error", "").lower()
    ]
    execution_errors = [
        e
        for e in trace_log
        if e.get("error")
        and "validation" not in e.get("error", "").lower()
        and e.get("node") != "TIMEOUT"
    ]
    sql_generations = [e for e in trace_log if e.get("node") == "generate_sql"]
    agent_calls = [e for e in trace_log if e.get("node") == "agent"]

    return {
        "question": question,
        "total_elapsed_s": total_elapsed,
        "total_steps": step_count,
        "agent_calls": len(agent_calls),
        "sql_generations": len(sql_generations),
        "validation_errors": len(validation_errors),
        "execution_errors": len(execution_errors),
        "final_answer_preview": final_answer[:200] if final_answer else "",
        "trace": trace_log,
    }


async def main():
    from src.streaming import AtlasTextToSQL

    logger.info("Creating AtlasTextToSQL instance...")
    atlas = await AtlasTextToSQL.create_async()

    results = []
    total = len(TRACE_QUERIES)

    # Run queries concurrently in pairs
    async def run_one(i: int, question: str) -> dict:
        logger.info("=" * 70)
        logger.info("[%s/%s] %s", i, total, question)
        logger.info("=" * 70)
        try:
            return await asyncio.wait_for(
                trace_single_query(atlas, question),
                timeout=QUERY_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("  GLOBAL TIMEOUT after %ss", QUERY_TIMEOUT)
            return {
                "question": question,
                "total_elapsed_s": QUERY_TIMEOUT,
                "total_steps": 0,
                "status": "TIMEOUT",
                "trace": [],
            }

    # Run concurrently in batches of 2
    batch_size = 2
    for batch_start in range(0, total, batch_size):
        batch = TRACE_QUERIES[batch_start : batch_start + batch_size]
        tasks = [run_one(batch_start + j + 1, q) for j, q in enumerate(batch)]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        for j, r in enumerate(batch_results):
            if isinstance(r, Exception):
                results.append(
                    {
                        "question": batch[j],
                        "status": "EXCEPTION",
                        "error": str(r),
                        "trace": [],
                    }
                )
            else:
                results.append(r)
        # Save after each batch
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(results, f, indent=2, default=str)

    # Log summary
    logger.info("=" * 70)
    logger.info("TRACE SUMMARY")
    logger.info("=" * 70)

    for r in results:
        q = r["question"][:60]
        t = r.get("total_elapsed_s", "?")
        steps = r.get("total_steps", "?")
        sql_gens = r.get("sql_generations", "?")
        val_errs = r.get("validation_errors", "?")
        exec_errs = r.get("execution_errors", "?")
        agent_calls = r.get("agent_calls", "?")
        logger.info(
            "  %-60s | %5ss | steps=%s agent=%s sql_gen=%s val_err=%s exec_err=%s",
            q,
            t,
            steps,
            agent_calls,
            sql_gens,
            val_errs,
            exec_errs,
        )

    logger.info("Full traces saved to %s", OUTPUT_PATH)
    await atlas.aclose()


if __name__ == "__main__":
    asyncio.run(main())
