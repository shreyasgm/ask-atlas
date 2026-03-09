"""Promote approved feedback candidates into the eval question set.

Usage:
    uv run python evaluation/promote_feedback.py --ids 42 43 55
    uv run python evaluation/promote_feedback.py --all
    uv run python evaluation/promote_feedback.py --ids 42 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

EVALUATION_DIR = Path(__file__).resolve().parent
EVAL_QUESTIONS_PATH = EVALUATION_DIR / "eval_questions.json"
CANDIDATES_PATH = EVALUATION_DIR / "feedback_candidates.json"
RESULTS_DIR = EVALUATION_DIR / "results"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_eval_question(candidate: dict) -> dict:
    """Build an eval_questions.json entry from a candidate."""
    return {
        "id": candidate["suggested_id"],
        "category_id": candidate["suggested_category_id"],
        "difficulty": candidate["suggested_difficulty"],
        "text": candidate["suggested_question"],
        "expected_behavior": candidate["suggested_expected_behavior"],
        "source": "user_feedback",
        "feedback_id": candidate["feedback_id"],
    }


def build_ground_truth(candidate: dict) -> dict:
    """Build a ground truth results.json scaffold for a promoted candidate."""
    return {
        "question_id": str(candidate["suggested_id"]),
        "execution_timestamp": datetime.now(UTC).isoformat(),
        "source": "user_feedback",
        "feedback_id": candidate["feedback_id"],
        "notes": (
            f"Promoted from user feedback. {candidate['suggested_expected_behavior']} "
            "Full pipeline data available in feedback export."
        ),
        "results": {"data": []},
    }


def select_candidates(
    candidates: list[dict],
    feedback_ids: list[int] | None,
    promote_all: bool,
) -> tuple[list[dict], list[dict]]:
    """Split candidates into (to_promote, skipped).

    Skipped includes duplicates and (when using --ids) non-matching entries.
    """
    to_promote: list[dict] = []
    skipped: list[dict] = []

    for c in candidates:
        if not promote_all and (
            feedback_ids is not None and c["feedback_id"] not in feedback_ids
        ):
            continue  # not selected
        if c.get("duplicate_of") is not None:
            skipped.append(c)
            continue
        to_promote.append(c)

    return to_promote, skipped


def validate_no_id_conflict(
    to_promote: list[dict], existing_ids: set[int]
) -> list[str]:
    """Return a list of warning messages for ID conflicts."""
    warnings = []
    for c in to_promote:
        if c["suggested_id"] in existing_ids:
            warnings.append(
                f"ID {c['suggested_id']} (feedback {c['feedback_id']}) already exists — skipping."
            )
    return warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Promote feedback candidates to eval set"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", nargs="+", type=int, help="Feedback IDs to promote")
    group.add_argument(
        "--all",
        action="store_true",
        dest="promote_all",
        help="Promote all non-duplicate candidates",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without modifying files",
    )
    args = parser.parse_args(argv)

    if not CANDIDATES_PATH.exists():
        logger.error(
            "No candidates file found at %s. Run feedback_to_eval.py first.",
            CANDIDATES_PATH,
        )
        sys.exit(1)

    candidates_data = json.loads(CANDIDATES_PATH.read_text())
    candidates = candidates_data.get("candidates", [])

    eval_data = json.loads(EVAL_QUESTIONS_PATH.read_text())
    existing_ids = {q["id"] for q in eval_data.get("questions", [])}

    to_promote, skipped = select_candidates(candidates, args.ids, args.promote_all)

    for s in skipped:
        logger.info(
            "  Skipping feedback %s: duplicate of question %s",
            s["feedback_id"],
            s["duplicate_of"],
        )

    # Filter out ID conflicts
    conflicts = validate_no_id_conflict(to_promote, existing_ids)
    for w in conflicts:
        logger.warning("  %s", w)
    conflict_ids = {c["suggested_id"] for c in to_promote} & existing_ids
    to_promote = [c for c in to_promote if c["suggested_id"] not in conflict_ids]

    if not to_promote:
        logger.info("Nothing to promote.")
        return

    logger.info("Will promote %s candidate(s):", len(to_promote))
    for c in to_promote:
        logger.info("  [%s] %s", c["suggested_id"], c["suggested_question"][:80])

    if args.dry_run:
        logger.info("(dry run — no files modified)")
        return

    # 1. Add to eval_questions.json
    for c in to_promote:
        eval_data["questions"].append(build_eval_question(c))
    EVAL_QUESTIONS_PATH.write_text(json.dumps(eval_data, indent=2) + "\n")
    logger.info("Updated %s", EVAL_QUESTIONS_PATH)

    # 2. Create ground truth scaffolds
    for c in to_promote:
        gt_dir = RESULTS_DIR / str(c["suggested_id"]) / "ground_truth"
        gt_dir.mkdir(parents=True, exist_ok=True)
        gt_path = gt_dir / "results.json"
        gt_path.write_text(json.dumps(build_ground_truth(c), indent=2) + "\n")
        logger.info("  Created %s", gt_path)

    # 3. Remove promoted entries from candidates file
    promoted_feedback_ids = {c["feedback_id"] for c in to_promote}
    remaining = [c for c in candidates if c["feedback_id"] not in promoted_feedback_ids]
    candidates_data["candidates"] = remaining
    candidates_data["candidate_count"] = len(remaining)
    CANDIDATES_PATH.write_text(json.dumps(candidates_data, indent=2) + "\n")
    logger.info(
        "  Removed %s promoted entry(ies) from %s", len(to_promote), CANDIDATES_PATH
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
