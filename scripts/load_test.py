#!/usr/bin/env python3
"""End-to-end load test for the Ask Atlas API.

Simulates N concurrent users sending questions via the HTTP API.
Supports both the non-streaming /chat endpoint and the SSE /chat/stream
endpoint. Measures latency, throughput, and error rates.

Usage:
    # Basic: 5 concurrent users, 2 questions each
    uv run python scripts/load_test.py --base-url http://localhost:8000

    # Heavy: 20 concurrent users, streaming mode
    uv run python scripts/load_test.py --base-url http://localhost:8000 \
        --users 20 --questions-per-user 3 --streaming

    # Against production:
    uv run python scripts/load_test.py --base-url https://your-cloud-run-url.run.app \
        --users 10 --questions-per-user 2

    # SQL-only mode:
    uv run python scripts/load_test.py --base-url http://localhost:8000 --mode sql_only
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Representative questions spanning different query complexities
QUESTIONS = [
    "What were Bolivia's top 5 exports in 2020?",
    "How much did the US export to China in 2019?",
    "What is the total export value of Germany in 2021?",
    "Which countries export the most coffee?",
    "What is the trade balance of Japan with South Korea in 2018?",
    "Show me Brazil's export complexity ranking over the last 5 years",
    "What are the top 10 exporters of crude oil?",
    "How has India's export basket changed between 2010 and 2020?",
    "What is the RCA of Thailand in electronics?",
    "Which products does Colombia have comparative advantage in?",
    "What is the total global trade in semiconductors?",
    "Compare exports of Mexico and Canada in 2019",
    "What are the fastest growing exports of Vietnam?",
    "How much does Kenya export in cut flowers?",
    "What is the economic complexity index of South Korea?",
]


@dataclass
class RequestResult:
    """Result of a single API request."""

    user_id: int
    question_index: int
    question: str
    elapsed_ms: float
    status_code: int
    error: str | None
    answer_preview: str | None
    query_count: int
    token_usage: dict | None
    streaming: bool


async def send_chat_request(
    client: httpx.AsyncClient,
    base_url: str,
    question: str,
    user_id: int,
    question_index: int,
    mode: str | None,
) -> RequestResult:
    """Send a non-streaming chat request."""
    thread_id = str(uuid.uuid4())
    body = {"question": question, "thread_id": thread_id}
    if mode:
        body["mode"] = mode

    t_start = time.monotonic()
    try:
        resp = await client.post(
            f"{base_url}/api/chat",
            json=body,
            headers={"X-Session-Id": f"loadtest-{user_id}"},
            timeout=120.0,
        )
        elapsed_ms = (time.monotonic() - t_start) * 1000

        if resp.status_code == 200:
            data = resp.json()
            return RequestResult(
                user_id=user_id,
                question_index=question_index,
                question=question,
                elapsed_ms=elapsed_ms,
                status_code=200,
                error=None,
                answer_preview=data.get("answer", "")[:100],
                query_count=len(data.get("queries") or []),
                token_usage=data.get("token_usage"),
                streaming=False,
            )
        else:
            return RequestResult(
                user_id=user_id,
                question_index=question_index,
                question=question,
                elapsed_ms=elapsed_ms,
                status_code=resp.status_code,
                error=resp.text[:200],
                answer_preview=None,
                query_count=0,
                token_usage=None,
                streaming=False,
            )
    except Exception as e:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        return RequestResult(
            user_id=user_id,
            question_index=question_index,
            question=question,
            elapsed_ms=elapsed_ms,
            status_code=0,
            error=str(e)[:200],
            answer_preview=None,
            query_count=0,
            token_usage=None,
            streaming=False,
        )


async def send_stream_request(
    client: httpx.AsyncClient,
    base_url: str,
    question: str,
    user_id: int,
    question_index: int,
    mode: str | None,
) -> RequestResult:
    """Send a streaming SSE chat request, consume full stream."""
    thread_id = str(uuid.uuid4())
    body = {"question": question, "thread_id": thread_id}
    if mode:
        body["mode"] = mode

    t_start = time.monotonic()
    chunks_received = 0
    answer_text = ""
    last_event_data = {}

    try:
        async with client.stream(
            "POST",
            f"{base_url}/api/chat/stream",
            json=body,
            headers={"X-Session-Id": f"loadtest-{user_id}"},
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                elapsed_ms = (time.monotonic() - t_start) * 1000
                return RequestResult(
                    user_id=user_id,
                    question_index=question_index,
                    question=question,
                    elapsed_ms=elapsed_ms,
                    status_code=resp.status_code,
                    error=f"Stream returned {resp.status_code}",
                    answer_preview=None,
                    query_count=0,
                    token_usage=None,
                    streaming=True,
                )

            event_type = None
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    chunks_received += 1
                    if event_type == "agent_talk":
                        answer_text += data_str
                    elif event_type == "done":
                        try:
                            last_event_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            pass

        elapsed_ms = (time.monotonic() - t_start) * 1000
        return RequestResult(
            user_id=user_id,
            question_index=question_index,
            question=question,
            elapsed_ms=elapsed_ms,
            status_code=200,
            error=None,
            answer_preview=answer_text[:100] if answer_text else None,
            query_count=last_event_data.get("total_queries", 0),
            token_usage=last_event_data.get("token_usage"),
            streaming=True,
        )
    except Exception as e:
        elapsed_ms = (time.monotonic() - t_start) * 1000
        return RequestResult(
            user_id=user_id,
            question_index=question_index,
            question=question,
            elapsed_ms=elapsed_ms,
            status_code=0,
            error=str(e)[:200],
            answer_preview=None,
            query_count=0,
            token_usage=None,
            streaming=True,
        )


async def simulate_user(
    client: httpx.AsyncClient,
    base_url: str,
    user_id: int,
    questions_per_user: int,
    streaming: bool,
    mode: str | None,
    stagger_ms: int,
) -> list[RequestResult]:
    """Simulate a single user sending sequential questions."""
    # Stagger start times to avoid thundering herd
    await asyncio.sleep((user_id * stagger_ms) / 1000)

    results = []
    for qi in range(questions_per_user):
        question = QUESTIONS[(user_id * questions_per_user + qi) % len(QUESTIONS)]
        send_fn = send_stream_request if streaming else send_chat_request
        result = await send_fn(client, base_url, question, user_id, qi, mode)
        results.append(result)
        logger.info(
            "  User %2d Q%s: %6.0fms %s %s %s",
            user_id,
            qi,
            result.elapsed_ms,
            "OK" if result.error is None else "ERR",
            "[stream]" if streaming else "[chat]",
            question[:50],
        )
    return results


async def check_health(client: httpx.AsyncClient, base_url: str) -> bool:
    """Check if the server is healthy before starting load test."""
    try:
        resp = await client.get(f"{base_url}/api/health", timeout=5.0)
        return resp.status_code == 200
    except Exception:
        return False


async def fetch_pool_stats(client: httpx.AsyncClient, base_url: str) -> dict | None:
    """Fetch pool stats from the debug endpoint."""
    try:
        resp = await client.get(f"{base_url}/api/debug/pool", timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


async def main(
    base_url: str,
    users: int,
    questions_per_user: int,
    streaming: bool,
    mode: str | None,
    stagger_ms: int,
):
    logger.info("Ask Atlas End-to-End Load Test")
    logger.info("  Target:          %s", base_url)
    logger.info("  Users:           %s", users)
    logger.info("  Questions/user:  %s", questions_per_user)
    logger.info("  Total requests:  %s", users * questions_per_user)
    logger.info("  Mode:            %s", mode or "auto")
    logger.info("  Streaming:       %s", streaming)
    logger.info("  Stagger:         %sms between user starts", stagger_ms)
    logger.info("")

    async with httpx.AsyncClient() as client:
        # Health check
        logger.info("Checking server health...")
        if not await check_health(client, base_url):
            logger.error("Server at %s is not responding. Is it running?", base_url)
            sys.exit(1)
        logger.info("  Server is healthy.")
        logger.info("")

        # Pool stats before
        pool_before = await fetch_pool_stats(client, base_url)
        if pool_before:
            logger.info("Pool stats (before):")
            logger.info("  %s", json.dumps(pool_before, indent=2))
            logger.info("")

        # Launch concurrent users
        logger.info("Running load test...")
        overall_start = time.monotonic()

        user_tasks = [
            simulate_user(
                client, base_url, uid, questions_per_user, streaming, mode, stagger_ms
            )
            for uid in range(users)
        ]
        user_results = await asyncio.gather(*user_tasks)
        all_results = [r for user_batch in user_results for r in user_batch]

        overall_elapsed = (time.monotonic() - overall_start) * 1000

        # Pool stats after
        pool_after = await fetch_pool_stats(client, base_url)

        # Report
        logger.info("")
        logger.info("%s", "=" * 70)
        logger.info("RESULTS")
        logger.info("%s", "=" * 70)

        successes = [r for r in all_results if r.error is None]
        failures = [r for r in all_results if r.error is not None]

        logger.info("  Total requests:   %s", len(all_results))
        logger.info("  Successes:        %s", len(successes))
        logger.info("  Failures:         %s", len(failures))
        logger.info("  Overall time:     %sms", f"{overall_elapsed:.0f}")
        if successes:
            logger.info(
                "  Throughput:       %s req/sec",
                f"{len(successes) / (overall_elapsed / 1000):.2f}",
            )

        if successes:
            timings = [r.elapsed_ms for r in successes]
            logger.info("")
            logger.info("  Latency (ms) — successful requests:")
            logger.info("    avg:  %s", f"{statistics.mean(timings):,.0f}")
            logger.info("    p50:  %s", f"{statistics.median(timings):,.0f}")
            if len(timings) >= 20:
                logger.info(
                    "    p95:  %s", f"{sorted(timings)[int(len(timings) * 0.95)]:,.0f}"
                )
            logger.info("    max:  %s", f"{max(timings):,.0f}")
            logger.info("    min:  %s", f"{min(timings):,.0f}")

        if failures:
            logger.info("")
            logger.error("  Failures:")
            for r in failures[:10]:
                logger.error(
                    "    User %s Q%s: status=%s error=%s",
                    r.user_id,
                    r.question_index,
                    r.status_code,
                    r.error[:80],
                )

        # Token usage summary
        total_tokens = 0
        total_cost = 0.0
        for r in successes:
            if r.token_usage:
                total_tokens += r.token_usage.get("total_tokens", 0)
                total_cost += r.token_usage.get("total_cost", 0.0)
        if total_tokens:
            logger.info("")
            logger.info("  Token usage:")
            logger.info("    Total tokens: %s", f"{total_tokens:,}")
            logger.info("    Total cost:   $%s", f"{total_cost:.4f}")

        # Pool stats comparison
        if pool_after:
            logger.info("")
            logger.info("  Pool stats (after):")
            logger.info("    %s", json.dumps(pool_after, indent=2))

        logger.info("")
        logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ask Atlas end-to-end load test")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base URL of the API server",
    )
    parser.add_argument(
        "--users", type=int, default=5, help="Number of concurrent users"
    )
    parser.add_argument(
        "--questions-per-user",
        type=int,
        default=2,
        help="Questions per user",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Use SSE streaming endpoint instead of /chat",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "sql_only", "graphql_only"],
        default=None,
        help="Force a specific pipeline mode",
    )
    parser.add_argument(
        "--stagger-ms",
        type=int,
        default=500,
        help="Milliseconds between user start times (0 = thundering herd)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )

    asyncio.run(
        main(
            args.base_url,
            args.users,
            args.questions_per_user,
            args.streaming,
            args.mode,
            args.stagger_ms,
        )
    )
