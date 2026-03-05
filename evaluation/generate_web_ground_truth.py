#!/usr/bin/env python3
"""Generate web-research ground truth for eval questions lacking SQL ground truth.

Uses LLM with web search (and web fetch for Anthropic) to research answers,
then saves results as middle-tier ground truth that the judge can reference.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/generate_web_ground_truth.py
    PYTHONPATH=$(pwd) uv run python evaluation/generate_web_ground_truth.py --provider anthropic
    PYTHONPATH=$(pwd) uv run python evaluation/generate_web_ground_truth.py --questions 253 254 255
    PYTHONPATH=$(pwd) uv run python evaluation/generate_web_ground_truth.py --dry-run
    PYTHONPATH=$(pwd) uv run python evaluation/generate_web_ground_truth.py --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from utils import EVALUATION_BASE_DIR, load_json_file, save_json_file

# Load .env from project root so API keys are available
load_dotenv(EVALUATION_BASE_DIR.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODELS = {
    "openai": "gpt-5.2",
    "anthropic": "claude-sonnet-4-6",
}

_RESEARCH_SYSTEM_PROMPT = """\
You are a trade and economics research analyst with access to web search.
Your task is to research the following question about international trade data
and provide a factual, data-driven answer.

Instructions:
- Search the web for authoritative data on the topic.
- Prioritize official sources: World Bank, UN Comtrade, UNCTAD, Atlas of Economic Complexity \
(atlas.hks.harvard.edu), Observatory of Economic Complexity (OEC), WTO, IMF.
- Provide specific data points: country names, product names, dollar values, percentages, \
years, rankings, and trends.
- When citing numbers, include the year they refer to.
- If multiple sources give different figures, note the discrepancy.
- Self-assess your confidence: high (multiple authoritative sources agree), medium (some data \
found but incomplete), or low (limited sources or extrapolated).
- Flag uncertainty explicitly rather than guessing. Say "I could not find data on X" rather \
than making up numbers.
- At the end, list your source URLs.

Format your answer as:
1. A factual summary paragraph with specific data points.
2. A confidence assessment (high/medium/low) with brief justification.
3. A numbered list of source URLs.
"""


# ---------------------------------------------------------------------------
# Provider-specific research functions
# ---------------------------------------------------------------------------


async def _research_openai(question: str, model: str) -> dict:
    """Research a question using OpenAI Responses API with web_search."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    response = await client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=(f"{_RESEARCH_SYSTEM_PROMPT}\n\n" f"Question to research:\n{question}"),
    )
    answer = response.output_text

    # Extract source URLs from annotations if available
    sources: list[str] = []
    for item in response.output:
        if hasattr(item, "content"):
            for block in item.content:
                if hasattr(block, "annotations"):
                    for ann in block.annotations:
                        if hasattr(ann, "url") and ann.url not in sources:
                            sources.append(ann.url)

    return {"answer": answer, "sources": sources}


async def _research_anthropic(question: str, model: str) -> dict:
    """Research a question using Anthropic Messages API with web_search + web_fetch."""
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic()
    message = await client.messages.create(
        model=model,
        max_tokens=8192,
        tools=[
            {"name": "web_search", "type": "web_search_20250305"},
            {"name": "web_fetch", "type": "web_fetch_20260209"},
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"{_RESEARCH_SYSTEM_PROMPT}\n\n"
                    f"Question to research:\n{question}"
                ),
            }
        ],
    )

    # Extract text blocks
    text_parts = []
    sources: list[str] = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "web_search_tool_result":
            for result in block.content:
                if hasattr(result, "url") and result.url not in sources:
                    sources.append(result.url)

    answer = "\n".join(text_parts)
    return {"answer": answer, "sources": sources}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _load_questions() -> list[dict]:
    """Load all questions from eval_questions.json."""
    data = load_json_file(EVALUATION_BASE_DIR / "eval_questions.json")
    return data["questions"]


def _has_sql_ground_truth(question_id: str) -> bool:
    """Check if a question already has SQL ground truth (results.json)."""
    gt_path = (
        EVALUATION_BASE_DIR / "results" / question_id / "ground_truth" / "results.json"
    )
    return gt_path.exists()


def _has_expected_behavior(question: dict) -> bool:
    """Check if a question has an expected_behavior field (refusal questions)."""
    return bool(question.get("expected_behavior"))


def _get_web_research_path(question_id: str) -> Path:
    return (
        EVALUATION_BASE_DIR
        / "results"
        / question_id
        / "ground_truth"
        / "web_research.json"
    )


def _extract_confidence(answer: str) -> str:
    """Extract confidence level from the answer text."""
    lower = answer.lower()
    # Look for explicit confidence markers
    for level in ["high", "medium", "low"]:
        if (
            f"confidence: {level}" in lower
            or f"confidence assessment: {level}" in lower
        ):
            return level
    # Fallback heuristics
    if "high confidence" in lower:
        return "high"
    if "low confidence" in lower:
        return "low"
    return "medium"


async def _research_one(
    question: dict,
    provider: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict | None:
    """Research a single question and save the result."""
    qid = str(question["id"])
    text = question["text"]

    async with semaphore:
        log.info(f"Q{qid}: Researching — {text[:80]}...")
        try:
            if provider == "openai":
                result = await _research_openai(text, model)
            else:
                result = await _research_anthropic(text, model)

            confidence = _extract_confidence(result["answer"])

            output = {
                "question_id": qid,
                "provider": provider,
                "model": model,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "research_answer": result["answer"],
                "sources": result["sources"],
                "confidence": confidence,
            }

            # Save to disk
            out_path = _get_web_research_path(qid)
            save_json_file(out_path, output)
            log.info(
                f"Q{qid}: Done (confidence={confidence}, "
                f"{len(result['sources'])} sources) → {out_path}"
            )
            return output

        except Exception as e:
            log.error(f"Q{qid}: Research failed — {e}")
            return None


def _select_questions(
    all_questions: list[dict],
    question_ids: list[str] | None = None,
    force: bool = False,
) -> list[dict]:
    """Select questions that need web research.

    Filters to questions without SQL ground truth and without expected_behavior.
    If question_ids is provided, further filters to those IDs.
    If force is True, includes questions that already have web_research.json.
    """
    candidates = []
    for q in all_questions:
        qid = str(q["id"])

        # If specific IDs requested, filter to those
        if question_ids and qid not in question_ids:
            continue

        # Skip questions with SQL ground truth
        if _has_sql_ground_truth(qid):
            continue

        # Skip refusal questions
        if _has_expected_behavior(q):
            continue

        # Skip if already has web research (unless --force)
        if not force and _get_web_research_path(qid).exists():
            continue

        candidates.append(q)

    return candidates


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate web-research ground truth for eval questions"
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic"],
        default="openai",
        help="LLM provider (default: openai)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name (default: provider-specific default)",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        type=str,
        help="Specific question IDs to process",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent research tasks (default: 5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print questions that would be processed without running research",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-research questions that already have web_research.json",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    provider = args.provider
    model = args.model or _DEFAULT_MODELS[provider]

    log.info("=" * 60)
    log.info("Web Research Ground Truth Generator")
    log.info(f"  Provider: {provider} | Model: {model}")
    log.info("=" * 60)

    all_questions = _load_questions()
    candidates = _select_questions(all_questions, args.questions, args.force)

    if not candidates:
        log.info("No questions need web research. Done.")
        return

    log.info(f"Found {len(candidates)} questions to research:")
    for q in candidates:
        log.info(f"  Q{q['id']}: {q['text'][:80]}...")

    if args.dry_run:
        log.info("Dry run — exiting without making API calls.")
        return

    semaphore = asyncio.Semaphore(args.concurrency)
    tasks = [_research_one(q, provider, model, semaphore) for q in candidates]
    results = await asyncio.gather(*tasks)

    succeeded = sum(1 for r in results if r is not None)
    failed = len(results) - succeeded
    log.info("=" * 60)
    log.info(f"Done: {succeeded} succeeded, {failed} failed out of {len(candidates)}")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
