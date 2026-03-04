"""Unit tests for evaluation/feedback_to_eval.py and evaluation/promote_feedback.py.

Tests cover pure transformation functions only — no network or file I/O.
"""

from __future__ import annotations

import json
from pathlib import Path


from evaluation.feedback_to_eval import (
    build_candidate,
    build_expected_behavior,
    extract_pipeline_summary,
    find_duplicate,
    suggest_category,
)
from evaluation.promote_feedback import (
    build_eval_question,
    build_ground_truth,
    select_candidates,
    validate_no_id_conflict,
)

# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

CATEGORIES = [
    "total_export_values",
    "sectoral_composition",
    "trade_position",
    "growth_performance",
    "economic_complexity",
    "diversification",
    "edge_cases",
    "out_of_scope",
]

EXISTING_QUESTIONS = [
    {"id": 1, "text": "What is the total value of exports for Brazil in 2018?"},
    {"id": 2, "text": "What is the export value of crude oil from Nigeria in 2020?"},
]


def _make_feedback_entry(
    *,
    feedback_id: int = 42,
    question: str = "How much did Brazil export in 2019?",
    answer: str = "Brazil exported $225B in 2019.",
    comment: str | None = "Wrong numbers",
    pipeline: dict | None = None,
) -> dict:
    context = {
        "flagged_turn": {
            "turn_index": 0,
            "user_question": question,
            "assistant_response": answer,
        },
        "pipeline": pipeline,
    }
    return {
        "id": feedback_id,
        "thread_id": "t1",
        "turn_index": 0,
        "rating": "down",
        "comment": comment,
        "context": context,
        "created_at": "2026-03-04T12:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# feedback_to_eval: suggest_category
# ---------------------------------------------------------------------------


class TestSuggestCategory:
    def test_matches_export_value(self) -> None:
        assert (
            suggest_category("What is the total export value of Brazil?", CATEGORIES)
            == "total_export_values"
        )

    def test_matches_complexity(self) -> None:
        assert (
            suggest_category("What is the ECI of Japan?", CATEGORIES)
            == "economic_complexity"
        )

    def test_matches_growth(self) -> None:
        assert (
            suggest_category("How did exports grow for Kenya?", CATEGORIES)
            == "growth_performance"
        )

    def test_matches_diversification(self) -> None:
        assert (
            suggest_category("How diversified is Chile?", CATEGORIES)
            == "diversification"
        )

    def test_matches_out_of_scope(self) -> None:
        assert (
            suggest_category("What is the GDP of France?", CATEGORIES) == "out_of_scope"
        )

    def test_fallback_to_edge_cases(self) -> None:
        assert suggest_category("random gibberish foobar", CATEGORIES) == "edge_cases"

    def test_only_matches_available_categories(self) -> None:
        limited = ["edge_cases"]
        assert suggest_category("Total export value?", limited) == "edge_cases"


# ---------------------------------------------------------------------------
# feedback_to_eval: find_duplicate
# ---------------------------------------------------------------------------


class TestFindDuplicate:
    def test_exact_match(self) -> None:
        assert (
            find_duplicate(
                "What is the total value of exports for Brazil in 2018?",
                EXISTING_QUESTIONS,
            )
            == 1
        )

    def test_near_match(self) -> None:
        result = find_duplicate(
            "What is the total value of exports for Brazil in 2019?", EXISTING_QUESTIONS
        )
        # Differs by one word (2018 vs 2019) — high similarity
        assert result == 1

    def test_no_match(self) -> None:
        assert (
            find_duplicate("How diversified is Chile's economy?", EXISTING_QUESTIONS)
            is None
        )

    def test_empty_existing(self) -> None:
        assert find_duplicate("anything", []) is None


# ---------------------------------------------------------------------------
# feedback_to_eval: extract_pipeline_summary
# ---------------------------------------------------------------------------


class TestExtractPipelineSummary:
    def test_basic_extraction(self) -> None:
        pipeline = {
            "entities": {"countries": ["BRA"]},
            "queries": [{"sql": "SELECT 1", "row_count": 5}],
            "total_rows": 5,
            "atlas_links": [{"url": "http://atlas.cid.harvard.edu"}],
            "graphql_summaries": [{"query": "..."}],
        }
        result = extract_pipeline_summary(pipeline)
        assert result is not None
        assert result["sql_queries"] == ["SELECT 1"]
        assert result["graphql_calls"] == 1
        assert result["total_rows"] == 5
        assert result["entities"] == {"countries": ["BRA"]}

    def test_none_returns_none(self) -> None:
        assert extract_pipeline_summary(None) is None

    def test_empty_dict_returns_defaults(self) -> None:
        result = extract_pipeline_summary({})
        assert result is not None
        assert result["sql_queries"] == []
        assert result["graphql_calls"] == 0
        assert result["total_rows"] == 0


# ---------------------------------------------------------------------------
# feedback_to_eval: build_expected_behavior
# ---------------------------------------------------------------------------


class TestBuildExpectedBehavior:
    def test_with_all_fields(self) -> None:
        result = build_expected_behavior(
            "Wrong data",
            "Brazil exported $200B",
            {"sql_queries": ["SELECT 1"], "total_rows": 5},
        )
        assert "User reported: Wrong data" in result
        assert "Agent responded: Brazil exported $200B" in result
        assert "1 SQL query(ies)" in result
        assert "5 row(s)" in result

    def test_truncates_long_answer(self) -> None:
        long_answer = "x" * 300
        result = build_expected_behavior(None, long_answer, None)
        assert "..." in result
        assert len(result) < 300

    def test_no_details(self) -> None:
        assert build_expected_behavior(None, None, None) == "No details available."


# ---------------------------------------------------------------------------
# feedback_to_eval: build_candidate
# ---------------------------------------------------------------------------


class TestBuildCandidate:
    def test_basic_candidate(self) -> None:
        entry = _make_feedback_entry(
            pipeline={
                "entities": {"countries": ["BRA"]},
                "queries": [{"sql": "SELECT 1"}],
                "total_rows": 3,
                "graphql_summaries": [],
                "atlas_links": [],
            }
        )
        result = build_candidate(entry, CATEGORIES, EXISTING_QUESTIONS, 253)
        assert result["feedback_id"] == 42
        assert result["suggested_id"] == 253
        assert result["suggested_question"] == "How much did Brazil export in 2019?"
        assert result["user_comment"] == "Wrong numbers"
        assert result["suggested_difficulty"] == "medium"
        assert result["pipeline_summary"] is not None
        assert "User reported: Wrong numbers" in result["suggested_expected_behavior"]

    def test_candidate_detects_duplicate(self) -> None:
        entry = _make_feedback_entry(
            question="What is the total value of exports for Brazil in 2018?"
        )
        result = build_candidate(entry, CATEGORIES, EXISTING_QUESTIONS, 253)
        assert result["duplicate_of"] == 1

    def test_candidate_no_context(self) -> None:
        entry = {
            "id": 99,
            "comment": None,
            "context": None,
            "created_at": "2026-03-04T12:00:00+00:00",
        }
        result = build_candidate(entry, CATEGORIES, [], 300)
        assert result["suggested_question"] == ""
        assert result["pipeline_summary"] is None


# ---------------------------------------------------------------------------
# promote_feedback: build_eval_question
# ---------------------------------------------------------------------------


class TestBuildEvalQuestion:
    def test_structure(self) -> None:
        candidate = {
            "suggested_id": 253,
            "suggested_category_id": "total_export_values",
            "suggested_difficulty": "medium",
            "suggested_question": "How much did Brazil export?",
            "suggested_expected_behavior": "User reported: wrong.",
            "feedback_id": 42,
        }
        q = build_eval_question(candidate)
        assert q["id"] == 253
        assert q["category_id"] == "total_export_values"
        assert q["text"] == "How much did Brazil export?"
        assert q["source"] == "user_feedback"
        assert q["feedback_id"] == 42
        assert q["expected_behavior"] == "User reported: wrong."


# ---------------------------------------------------------------------------
# promote_feedback: build_ground_truth
# ---------------------------------------------------------------------------


class TestBuildGroundTruth:
    def test_scaffold(self) -> None:
        candidate = {
            "suggested_id": 253,
            "feedback_id": 42,
            "suggested_expected_behavior": "User reported: wrong.",
        }
        gt = build_ground_truth(candidate)
        assert gt["question_id"] == "253"
        assert gt["source"] == "user_feedback"
        assert gt["feedback_id"] == 42
        assert gt["results"] == {"data": []}
        assert "Promoted from user feedback" in gt["notes"]


# ---------------------------------------------------------------------------
# promote_feedback: select_candidates
# ---------------------------------------------------------------------------


class TestSelectCandidates:
    def test_select_by_ids(self) -> None:
        candidates = [
            {"feedback_id": 1, "duplicate_of": None},
            {"feedback_id": 2, "duplicate_of": None},
            {"feedback_id": 3, "duplicate_of": None},
        ]
        promoted, skipped = select_candidates(candidates, [1, 3], False)
        assert len(promoted) == 2
        assert {c["feedback_id"] for c in promoted} == {1, 3}
        assert skipped == []

    def test_select_all_skips_duplicates(self) -> None:
        candidates = [
            {"feedback_id": 1, "duplicate_of": None},
            {"feedback_id": 2, "duplicate_of": 5},
            {"feedback_id": 3, "duplicate_of": None},
        ]
        promoted, skipped = select_candidates(candidates, None, True)
        assert len(promoted) == 2
        assert len(skipped) == 1
        assert skipped[0]["feedback_id"] == 2

    def test_select_by_ids_skips_duplicates(self) -> None:
        candidates = [
            {"feedback_id": 1, "duplicate_of": 5},
        ]
        promoted, skipped = select_candidates(candidates, [1], False)
        assert promoted == []
        assert len(skipped) == 1


# ---------------------------------------------------------------------------
# promote_feedback: validate_no_id_conflict
# ---------------------------------------------------------------------------


class TestValidateNoIdConflict:
    def test_no_conflicts(self) -> None:
        to_promote = [{"suggested_id": 253, "feedback_id": 42}]
        assert validate_no_id_conflict(to_promote, {1, 2, 3}) == []

    def test_detects_conflict(self) -> None:
        to_promote = [{"suggested_id": 1, "feedback_id": 42}]
        warnings = validate_no_id_conflict(to_promote, {1, 2, 3})
        assert len(warnings) == 1
        assert "already exists" in warnings[0]


# ---------------------------------------------------------------------------
# promote_feedback: end-to-end with tmp files
# ---------------------------------------------------------------------------


class TestPromoteEndToEnd:
    """Test the promote CLI writes correct files."""

    def test_promote_writes_files(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from evaluation.promote_feedback import main

        # Set up eval_questions.json
        eval_data = {
            "categories": [
                {"id": "edge_cases", "name": "Edge Cases", "description": "..."}
            ],
            "questions": [
                {
                    "id": 1,
                    "text": "Existing question",
                    "category_id": "edge_cases",
                    "difficulty": "easy",
                }
            ],
        }
        eq_path = tmp_path / "eval_questions.json"
        eq_path.write_text(json.dumps(eval_data))

        # Set up candidates
        candidates_data = {
            "candidates": [
                {
                    "feedback_id": 42,
                    "suggested_id": 253,
                    "suggested_question": "New question?",
                    "suggested_category_id": "edge_cases",
                    "suggested_difficulty": "medium",
                    "suggested_expected_behavior": "User reported: wrong.",
                    "duplicate_of": None,
                },
            ],
            "candidate_count": 1,
        }
        cand_path = tmp_path / "feedback_candidates.json"
        cand_path.write_text(json.dumps(candidates_data))

        results_dir = tmp_path / "results"

        with (
            patch("evaluation.promote_feedback.EVAL_QUESTIONS_PATH", eq_path),
            patch("evaluation.promote_feedback.CANDIDATES_PATH", cand_path),
            patch("evaluation.promote_feedback.RESULTS_DIR", results_dir),
        ):
            main(["--all"])

        # Verify eval_questions.json updated
        updated = json.loads(eq_path.read_text())
        assert len(updated["questions"]) == 2
        new_q = updated["questions"][-1]
        assert new_q["id"] == 253
        assert new_q["source"] == "user_feedback"

        # Verify ground truth scaffold
        gt_path = results_dir / "253" / "ground_truth" / "results.json"
        assert gt_path.exists()
        gt = json.loads(gt_path.read_text())
        assert gt["question_id"] == "253"
        assert gt["results"] == {"data": []}

        # Verify candidate removed
        remaining = json.loads(cand_path.read_text())
        assert remaining["candidate_count"] == 0
        assert remaining["candidates"] == []

    def test_dry_run_does_not_modify(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from evaluation.promote_feedback import main

        eval_data = {
            "categories": [],
            "questions": [],
        }
        eq_path = tmp_path / "eval_questions.json"
        eq_path.write_text(json.dumps(eval_data))

        candidates_data = {
            "candidates": [
                {
                    "feedback_id": 42,
                    "suggested_id": 253,
                    "suggested_question": "Question?",
                    "suggested_category_id": "edge_cases",
                    "suggested_difficulty": "medium",
                    "suggested_expected_behavior": "...",
                    "duplicate_of": None,
                },
            ],
            "candidate_count": 1,
        }
        cand_path = tmp_path / "feedback_candidates.json"
        cand_path.write_text(json.dumps(candidates_data))

        with (
            patch("evaluation.promote_feedback.EVAL_QUESTIONS_PATH", eq_path),
            patch("evaluation.promote_feedback.CANDIDATES_PATH", cand_path),
            patch("evaluation.promote_feedback.RESULTS_DIR", tmp_path / "results"),
        ):
            main(["--all", "--dry-run"])

        # Nothing should have changed
        assert len(json.loads(eq_path.read_text())["questions"]) == 0
        assert json.loads(cand_path.read_text())["candidate_count"] == 1
