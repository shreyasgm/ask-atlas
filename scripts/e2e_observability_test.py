#!/usr/bin/env python3
"""Ad-hoc end-to-end observability test for the Atlas backend.

Runs representative questions through each happy path (SQL, GraphQL,
Docs, mixed) and reports:
  - Which graph nodes fired, in what order
  - Wall-clock time per node (approx, from stream events)
  - Total latency per question
  - Tool calls made by the agent
  - Final answer snippet

Usage:
    PYTHONPATH=$(pwd) uv run python scripts/e2e_observability_test.py
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.streaming import (  # noqa: E402
    AtlasTextToSQL,
    PIPELINE_SEQUENCE,
    GRAPHQL_PIPELINE_SEQUENCE,
    DOCS_PIPELINE_SEQUENCE,
)

# ── ANSI colors ──────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
MAGENTA = "\033[35m"
RESET = "\033[0m"


# ── Test cases ───────────────────────────────────────────────────────


@dataclass
class TestCase:
    name: str
    question: str
    agent_mode: str | None  # None = use default (auto)
    expected_pipeline: str  # "sql", "graphql", "docs", "mixed"
    description: str = ""


TEST_CASES = [
    # 1. SQL pipeline — straightforward trade data query
    TestCase(
        name="SQL: Kenya coffee exports",
        question="What was Kenya's total coffee export value in 2022?",
        agent_mode="sql_only",
        expected_pipeline="sql",
        description="Forces SQL-only mode; should fire the full SQL pipeline.",
    ),
    # 2. GraphQL pipeline — country profile (Country Pages API)
    TestCase(
        name="GraphQL: Kenya country profile",
        question="What is Kenya's economic complexity ranking and diversification grade?",
        agent_mode="graphql_only",
        expected_pipeline="graphql",
        description="Forces GraphQL-only; expects country_profile classification → Country Pages API.",
    ),
    # 3. GraphQL pipeline — treemap products (Explore API)
    TestCase(
        name="GraphQL: Brazil export composition",
        question="What are Brazil's top exported products in 2022?",
        agent_mode="graphql_only",
        expected_pipeline="graphql",
        description="Forces GraphQL-only; expects treemap_products classification → Explore API.",
    ),
    # 4. Docs pipeline — methodology question
    TestCase(
        name="Docs: ECI methodology",
        question="What is the Economic Complexity Index and how is it calculated?",
        agent_mode=None,  # auto — agent should route to docs_tool
        expected_pipeline="docs",
        description="Methodology question; agent should pick docs_tool in any mode.",
    ),
    # 5. Auto mode — agent decides (likely GraphQL for profile + SQL for specific data)
    TestCase(
        name="Auto: Japan economic overview",
        question="Give me an overview of Japan's export economy.",
        agent_mode=None,
        expected_pipeline="mixed",
        description="Auto mode; agent freely chooses tools. Likely GraphQL country_profile.",
    ),
]


# ── Observability collector ──────────────────────────────────────────


@dataclass
class NodeTrace:
    name: str
    label: str
    start_time: float
    end_time: float | None = None
    payload: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000


@dataclass
class TestResult:
    test_case: TestCase
    tool_calls: list[str] = field(default_factory=list)
    node_traces: list[NodeTrace] = field(default_factory=list)
    final_answer: str = ""
    total_time_ms: float = 0.0
    error: str | None = None

    @property
    def pipeline_used(self) -> str:
        nodes = {t.name for t in self.node_traces}
        has_sql = bool(nodes & set(PIPELINE_SEQUENCE))
        has_gql = bool(nodes & set(GRAPHQL_PIPELINE_SEQUENCE))
        has_docs = bool(nodes & set(DOCS_PIPELINE_SEQUENCE))
        parts = []
        if has_sql:
            parts.append("SQL")
        if has_gql:
            parts.append("GraphQL")
        if has_docs:
            parts.append("Docs")
        return " + ".join(parts) or "None"


# ── Runner ───────────────────────────────────────────────────────────


async def run_test(agent: AtlasTextToSQL, tc: TestCase) -> TestResult:
    """Run a single test case with full stream observability."""
    result = TestResult(test_case=tc)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    # Track open node traces (node_start seen but no pipeline_state yet)
    open_nodes: dict[str, NodeTrace] = {}
    answer_chunks: list[str] = []

    t0 = time.perf_counter()
    try:
        async for _mode, sd in agent.astream_agent_response(
            tc.question,
            config,
            agent_mode=tc.agent_mode,
        ):
            now = time.perf_counter()

            if sd.message_type == "tool_call" and sd.tool_call:
                result.tool_calls.append(sd.tool_call)

            elif sd.message_type == "node_start" and sd.payload:
                node = sd.payload["node"]
                label = sd.payload.get("label", node)
                trace = NodeTrace(name=node, label=label, start_time=now)
                open_nodes[node] = trace
                result.node_traces.append(trace)

            elif sd.message_type == "pipeline_state" and sd.payload:
                stage = sd.payload.get("stage", "")
                if stage in open_nodes:
                    open_nodes[stage].end_time = now
                    open_nodes[stage].payload = sd.payload
                    del open_nodes[stage]

            elif sd.message_type == "agent_talk" and sd.content:
                answer_chunks.append(sd.content)

    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    t1 = time.perf_counter()
    result.total_time_ms = (t1 - t0) * 1000
    result.final_answer = "".join(answer_chunks)
    return result


def print_result(result: TestResult, idx: int) -> None:
    """Pretty-print a test result with node timing."""
    tc = result.test_case
    status = f"{GREEN}PASS{RESET}" if not result.error else f"{RED}FAIL{RESET}"
    print(f"\n{'='*80}")
    print(f"{BOLD}Test {idx}: {tc.name}{RESET}  [{status}]")
    print(f"{DIM}{tc.description}{RESET}")
    print(f"  Question:  {tc.question}")
    print(f"  Mode:      {tc.agent_mode or 'auto'}")
    print(f"  Expected:  {tc.expected_pipeline} pipeline")
    print(f"  Actual:    {result.pipeline_used} pipeline")
    print(f"  Total:     {BOLD}{result.total_time_ms:,.0f} ms{RESET}")

    if result.error:
        print(f"  {RED}Error: {result.error}{RESET}")

    # Tool calls
    if result.tool_calls:
        print(f"\n  {CYAN}Tool calls ({len(result.tool_calls)}):{RESET}")
        for i, tc_name in enumerate(result.tool_calls, 1):
            print(f"    {i}. {tc_name}")

    # Node traces
    if result.node_traces:
        print(f"\n  {YELLOW}Node trace ({len(result.node_traces)} nodes):{RESET}")
        for trace in result.node_traces:
            dur = trace.duration_ms
            dur_str = f"{dur:>8,.0f} ms" if dur > 0 else "       N/A"
            # Color-code by duration
            if dur > 2000:
                color = RED
            elif dur > 500:
                color = YELLOW
            else:
                color = GREEN
            bar_len = min(int(dur / 100), 40) if dur > 0 else 0
            bar = "█" * bar_len

            # Extra info from payload
            extra = ""
            p = trace.payload
            if trace.name == "classify_query" and p.get("query_type"):
                extra = f"  → type={p['query_type']}"
                if p.get("is_rejected"):
                    extra += f" (REJECTED: {p.get('rejection_reason', '?')})"
            elif trace.name == "extract_entities" and p.get("entities"):
                ent = p["entities"]
                parts = []
                if ent.get("country"):
                    parts.append(f"country={ent['country']}")
                if ent.get("product"):
                    parts.append(f"product={ent['product']}")
                if ent.get("year"):
                    parts.append(f"year={ent['year']}")
                extra = f"  → {', '.join(parts)}" if parts else ""
            elif trace.name == "resolve_ids" and p.get("resolved_ids"):
                ids = p["resolved_ids"]
                parts = []
                if ids.get("country_id"):
                    parts.append(f"country_id={ids['country_id']}")
                if ids.get("product_id"):
                    parts.append(f"product_id={ids['product_id']}")
                extra = f"  → {', '.join(parts)}" if parts else ""
            elif trace.name == "build_and_execute_graphql":
                extra_parts = []
                if p.get("api_target"):
                    extra_parts.append(f"api={p['api_target']}")
                if p.get("execution_time_ms"):
                    extra_parts.append(f"api_time={p['execution_time_ms']}ms")
                if "success" in p:
                    extra_parts.append(f"ok={p['success']}")
                if p.get("last_error"):
                    extra_parts.append(f"err={p['last_error'][:60]}")
                extra = f"  → {', '.join(extra_parts)}" if extra_parts else ""
            elif trace.name == "execute_sql":
                extra_parts = []
                if p.get("row_count") is not None:
                    extra_parts.append(f"rows={p['row_count']}")
                if p.get("execution_time_ms"):
                    extra_parts.append(f"db_time={p['execution_time_ms']}ms")
                if p.get("tables"):
                    extra_parts.append(f"tables={p['tables']}")
                extra = f"  → {', '.join(extra_parts)}" if extra_parts else ""
            elif trace.name == "generate_sql" and p.get("sql"):
                sql_preview = p["sql"][:80].replace("\n", " ")
                extra = f"  → {sql_preview}..."
            elif trace.name == "select_and_synthesize" and p.get("selected_files"):
                extra = f"  → files={p['selected_files']}"

            print(
                f"    {color}{dur_str}{RESET}  {trace.label:<26} {color}{bar}{RESET}{DIM}{extra}{RESET}"
            )

    # Answer snippet
    if result.final_answer:
        snippet = result.final_answer[:300].replace("\n", " ")
        if len(result.final_answer) > 300:
            snippet += "..."
        print(f"\n  {MAGENTA}Answer preview:{RESET}")
        print(f"    {DIM}{snippet}{RESET}")

    print()


async def main() -> None:
    print(f"\n{BOLD}{'='*80}{RESET}")
    print(f"{BOLD}  Atlas Backend E2E Observability Test{RESET}")
    print(f"{BOLD}{'='*80}{RESET}")
    print(f"\n  Running {len(TEST_CASES)} test cases against the live backend...")
    print("  Each test creates a fresh thread and streams the full response.\n")

    # Create the shared agent instance
    print(
        f"  {DIM}Initializing AtlasTextToSQL (connecting to DB, loading caches)...{RESET}"
    )
    t_init = time.perf_counter()
    async with await AtlasTextToSQL.create_async() as agent:
        init_ms = (time.perf_counter() - t_init) * 1000
        print(f"  {GREEN}Agent initialized in {init_ms:,.0f} ms{RESET}\n")

        results: list[TestResult] = []
        for i, tc in enumerate(TEST_CASES, 1):
            print(
                f"  {DIM}Running test {i}/{len(TEST_CASES)}: {tc.name}...{RESET}",
                flush=True,
            )
            result = await run_test(agent, tc)
            results.append(result)
            print_result(result, i)

    # ── Summary ──
    print(f"\n{'='*80}")
    print(f"{BOLD}  SUMMARY{RESET}")
    print(f"{'='*80}")
    total_time = sum(r.total_time_ms for r in results)
    passed = sum(1 for r in results if not r.error)
    failed = sum(1 for r in results if r.error)
    print(f"  Total tests:  {len(results)}")
    print(f"  Passed:       {GREEN}{passed}{RESET}")
    if failed:
        print(f"  Failed:       {RED}{failed}{RESET}")
    print(f"  Total time:   {total_time:,.0f} ms ({total_time/1000:.1f}s)")
    print()

    # Timing table
    print(f"  {'Test':<40} {'Time':>10} {'Pipeline':<20} {'Tools':>6}")
    print(f"  {'─'*40} {'─'*10} {'─'*20} {'─'*6}")
    for r in results:
        status = "✓" if not r.error else "✗"
        print(
            f"  {status} {r.test_case.name:<38} "
            f"{r.total_time_ms:>8,.0f}ms "
            f"{r.pipeline_used:<20} "
            f"{len(r.tool_calls):>5}"
        )
    print()


if __name__ == "__main__":
    asyncio.run(main())
