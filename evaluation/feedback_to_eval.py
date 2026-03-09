"""Generate candidate eval entries from negative user feedback.

Usage:
    uv run python evaluation/feedback_to_eval.py
    uv run python evaluation/feedback_to_eval.py --api-url http://host:port
    uv run python evaluation/feedback_to_eval.py --since 2026-03-01
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

EVALUATION_DIR = Path(__file__).resolve().parent
EVAL_QUESTIONS_PATH = EVALUATION_DIR / "eval_questions.json"
CANDIDATES_PATH = EVALUATION_DIR / "feedback_candidates.json"

# ---------------------------------------------------------------------------
# Pure helpers (testable without network)
# ---------------------------------------------------------------------------

# Keywords → category mapping.  Order matters: first match wins.
_CATEGORY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("economic_complexity", ["complexity", "eci", "pci", "sophisticated"]),
    ("diversification", ["diversif", "concentrated", "hhi"]),
    ("growth_performance", ["growth", "grow", "grew", "declined", "change over time"]),
    ("trade_position", ["partner", "market share", "bilateral", "destination"]),
    ("sectoral_composition", ["sector", "share", "percentage", "composition"]),
    ("total_export_values", ["total export", "export value", "how much"]),
    ("explore_bilateral_trade", ["bilateral", "trade between"]),
    ("explore_product_complexity", ["product complexity"]),
    ("explore_feasibility", ["feasibility", "strategic"]),
    ("out_of_scope", ["gdp", "population", "inflation", "unemployment"]),
    ("edge_cases", ["service", "subnational", "city"]),
]


def suggest_category(question_text: str, categories: list[str]) -> str:
    """Suggest a category_id for *question_text* via keyword matching.

    Returns the first matching category whose keywords appear in the question,
    falling back to ``"edge_cases"`` when nothing matches.
    """
    lower = question_text.lower()
    for cat_id, keywords in _CATEGORY_KEYWORDS:
        if cat_id not in categories:
            continue
        for kw in keywords:
            if kw in lower:
                return cat_id
    return "edge_cases"


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def find_duplicate(
    question_text: str, existing_questions: list[dict], threshold: float = 0.8
) -> int | None:
    """Return the id of an existing question with Jaccard word-set similarity > *threshold*."""
    words_new = set(question_text.lower().split())
    for q in existing_questions:
        words_existing = set(q["text"].lower().split())
        if _jaccard(words_new, words_existing) > threshold:
            return q["id"]
    return None


def extract_pipeline_summary(pipeline_data: dict | None) -> dict | None:
    """Condense raw pipeline data into a compact summary dict."""
    if pipeline_data is None:
        return None
    queries = pipeline_data.get("queries", [])
    return {
        "sql_queries": [q.get("sql", "") for q in queries if q.get("sql")],
        "graphql_calls": len(pipeline_data.get("graphql_summaries", [])),
        "entities": pipeline_data.get("entities"),
        "total_rows": pipeline_data.get("total_rows", 0),
        "atlas_links": pipeline_data.get("atlas_links", []),
    }


def build_expected_behavior(
    user_comment: str | None,
    agent_answer: str | None,
    pipeline_summary: dict | None,
) -> str:
    """Build an expected_behavior string combining feedback + pipeline info."""
    parts: list[str] = []
    if user_comment:
        parts.append(f"User reported: {user_comment}")
    if agent_answer:
        truncated = agent_answer[:200] + ("..." if len(agent_answer) > 200 else "")
        parts.append(f"Agent responded: {truncated}")
    if pipeline_summary:
        n_sql = len(pipeline_summary.get("sql_queries", []))
        rows = pipeline_summary.get("total_rows", 0)
        parts.append(f"Pipeline used {n_sql} SQL query(ies) returning {rows} row(s).")
    return " ".join(parts) if parts else "No details available."


def build_candidate(
    feedback_entry: dict,
    categories: list[str],
    existing_questions: list[dict],
    next_id: int,
) -> dict:
    """Build a single candidate dict from a feedback export entry."""
    context = feedback_entry.get("context") or {}
    flagged = context.get("flagged_turn") or {}
    question_text = flagged.get("user_question", "")
    agent_answer = flagged.get("assistant_response", "")
    pipeline_raw = context.get("pipeline")
    pipeline_summary = extract_pipeline_summary(pipeline_raw)

    return {
        "feedback_id": feedback_entry.get("id"),
        "suggested_id": next_id,
        "suggested_question": question_text,
        "user_comment": feedback_entry.get("comment"),
        "suggested_category_id": suggest_category(question_text, categories),
        "suggested_difficulty": "medium",
        "suggested_expected_behavior": build_expected_behavior(
            feedback_entry.get("comment"), agent_answer, pipeline_summary
        ),
        "agent_answer": agent_answer,
        "pipeline_summary": pipeline_summary,
        "duplicate_of": find_duplicate(question_text, existing_questions),
        "feedback_created_at": feedback_entry.get("created_at"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_eval_questions() -> tuple[list[str], list[dict], int]:
    """Return (category_ids, questions, next_available_id)."""
    data = json.loads(EVAL_QUESTIONS_PATH.read_text())
    categories = [c["id"] for c in data.get("categories", [])]
    questions = data.get("questions", [])
    max_id = max((q["id"] for q in questions), default=0)
    return categories, questions, max_id + 1


def _fetch_negative_feedback(api_url: str) -> list[dict]:
    url = f"{api_url}/api/feedback/export?rating=down&limit=1000"
    req = Request(url)
    with urlopen(req) as resp:  # noqa: S310
        return json.loads(resp.read())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate eval candidates from negative feedback"
    )
    parser.add_argument(
        "--api-url", default="http://localhost:8000", help="Backend API base URL"
    )
    parser.add_argument(
        "--since", help="Only include feedback created after this date (YYYY-MM-DD)"
    )
    args = parser.parse_args(argv)

    categories, existing_questions, next_id = _load_eval_questions()
    entries = _fetch_negative_feedback(args.api_url)

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
        entries = [
            e
            for e in entries
            if datetime.fromisoformat(e["created_at"]).replace(tzinfo=UTC) >= since
        ]

    candidates = []
    for entry in entries:
        candidate = build_candidate(entry, categories, existing_questions, next_id)
        candidates.append(candidate)
        next_id += 1

    output = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "user_feedback_export",
        "candidate_count": len(candidates),
        "next_available_id": next_id,
        "candidates": candidates,
    }

    CANDIDATES_PATH.write_text(json.dumps(output, indent=2) + "\n")
    logger.info("Wrote %s candidate(s) to %s", len(candidates), CANDIDATES_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
