"""Unit tests for evaluation/compare_cohorts.py.

Tests:
- _analyze_cohort(): flip counting, regression detection, new baseline, missing questions
- compare_cohorts(): end-to-end with mock report.json files
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure evaluation/ is importable
_EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from compare_cohorts import (  # noqa: E402
    _analyze_cohort,
    _aggregate_comparison,
    compare_cohorts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pq(qid: str, verdict: str, score: float, category: str = "A") -> dict:
    """Build a minimal per-question entry."""
    return {
        "question_id": qid,
        "question_text": f"Question {qid}",
        "category": category,
        "difficulty": "medium",
        "verdict": verdict,
        "weighted_score": score,
    }


def _pq_index(entries: list[dict]) -> dict[str, dict]:
    """Index per-question entries by question_id."""
    return {e["question_id"]: e for e in entries}


# ---------------------------------------------------------------------------
# Tests: _analyze_cohort
# ---------------------------------------------------------------------------


class TestAnalyzeCohort:
    def test_previously_failed_flip_to_pass(self):
        """Questions that were fail→pass should be counted as flipped."""
        cohort = {"1", "2", "3"}
        baseline = _pq_index(
            [
                _make_pq("1", "fail", 1.0),
                _make_pq("2", "fail", 1.5),
                _make_pq("3", "fail", 2.0),
            ]
        )
        new = _pq_index(
            [
                _make_pq("1", "pass", 4.5),
                _make_pq("2", "partial", 3.0),
                _make_pq("3", "fail", 1.5),
            ]
        )
        meta = {
            "1": {"category": "A", "difficulty": "easy"},
            "2": {"category": "B", "difficulty": "medium"},
            "3": {"category": "A", "difficulty": "hard"},
        }

        result = _analyze_cohort(
            cohort, baseline, new, meta, cohort_type="previously_failed"
        )

        assert result["total"] == 3
        assert result["flipped_to_pass"] == 1
        assert result["flipped_to_partial"] == 1
        assert result["still_fail"] == 1

    def test_regression_cohort_all_stay_pass(self):
        """Regression questions that stay pass should be counted as held."""
        cohort = {"10", "11", "12"}
        baseline = _pq_index(
            [
                _make_pq("10", "pass", 4.5),
                _make_pq("11", "pass", 4.0),
                _make_pq("12", "pass", 5.0),
            ]
        )
        new = _pq_index(
            [
                _make_pq("10", "pass", 4.5),
                _make_pq("11", "pass", 4.2),
                _make_pq("12", "pass", 4.8),
            ]
        )
        meta = {
            "10": {"category": "A", "difficulty": "easy"},
            "11": {"category": "B", "difficulty": "medium"},
            "12": {"category": "A", "difficulty": "hard"},
        }

        result = _analyze_cohort(cohort, baseline, new, meta, cohort_type="regression")

        assert result["total"] == 3
        assert result["held_pass"] == 3
        assert result["regressed"] == 0

    def test_regression_cohort_detects_regression(self):
        """Regression questions that drop from pass should be flagged."""
        cohort = {"10", "11"}
        baseline = _pq_index(
            [
                _make_pq("10", "pass", 4.5),
                _make_pq("11", "pass", 4.0),
            ]
        )
        new = _pq_index(
            [
                _make_pq("10", "fail", 1.0),
                _make_pq("11", "pass", 4.2),
            ]
        )
        meta = {
            "10": {"category": "A", "difficulty": "easy"},
            "11": {"category": "B", "difficulty": "medium"},
        }

        result = _analyze_cohort(cohort, baseline, new, meta, cohort_type="regression")

        assert result["regressed"] == 1
        assert result["held_pass"] == 1

    def test_new_baseline_cohort(self):
        """New baseline questions should report pass rate without comparison."""
        cohort = {"247", "248", "249"}
        baseline: dict[str, dict] = {}  # no baseline data
        new = _pq_index(
            [
                _make_pq("247", "pass", 4.5),
                _make_pq("248", "fail", 1.0),
                _make_pq("249", "pass", 4.0),
            ]
        )
        meta = {
            "247": {"category": "Classification", "difficulty": "hard"},
            "248": {"category": "Classification", "difficulty": "hard"},
            "249": {"category": "Classification", "difficulty": "medium"},
        }

        result = _analyze_cohort(
            cohort, baseline, new, meta, cohort_type="new_baseline"
        )

        assert result["total"] == 3
        assert result["pass_count"] == 2
        assert result["fail_count"] == 1

    def test_missing_questions_handled(self):
        """Questions not present in either run should be counted as missing."""
        cohort = {"1", "2", "999"}
        baseline = _pq_index(
            [
                _make_pq("1", "fail", 1.0),
            ]
        )
        new = _pq_index(
            [
                _make_pq("1", "pass", 4.5),
                _make_pq("2", "pass", 4.0),
            ]
        )
        meta = {
            "1": {"category": "A", "difficulty": "easy"},
            "2": {"category": "B", "difficulty": "medium"},
        }

        result = _analyze_cohort(
            cohort, baseline, new, meta, cohort_type="previously_failed"
        )

        # Q999 is missing from both runs, Q2 missing from baseline
        assert result["missing_from_new"] == 1  # Q999 not in new run
        assert result["total"] == 3


# ---------------------------------------------------------------------------
# Tests: _aggregate_comparison
# ---------------------------------------------------------------------------


class TestAggregateComparison:
    def test_overall_pass_rate_delta(self):
        """Should compute pass rate delta across overlapping questions."""
        baseline = _pq_index(
            [
                _make_pq("1", "pass", 4.5, "A"),
                _make_pq("2", "fail", 1.0, "B"),
                _make_pq("3", "pass", 4.0, "A"),
                _make_pq("4", "fail", 1.5, "B"),
            ]
        )
        new = _pq_index(
            [
                _make_pq("1", "pass", 4.5, "A"),
                _make_pq("2", "pass", 4.0, "B"),
                _make_pq("3", "fail", 1.0, "A"),
                _make_pq("4", "pass", 4.5, "B"),
            ]
        )

        result = _aggregate_comparison(baseline, new)

        assert result["overlap_count"] == 4
        # Baseline: 2/4 = 50%, New: 3/4 = 75%, delta = +25
        assert result["baseline_pass_rate"] == pytest.approx(50.0, abs=0.1)
        assert result["new_pass_rate"] == pytest.approx(75.0, abs=0.1)
        assert result["pass_rate_delta"] == pytest.approx(25.0, abs=0.1)

    def test_category_deltas(self):
        """Should compute per-category pass rate deltas."""
        baseline = _pq_index(
            [
                _make_pq("1", "pass", 4.5, "Trade"),
                _make_pq("2", "fail", 1.0, "Trade"),
                _make_pq("3", "pass", 4.0, "Complexity"),
            ]
        )
        new = _pq_index(
            [
                _make_pq("1", "pass", 4.5, "Trade"),
                _make_pq("2", "pass", 4.0, "Trade"),
                _make_pq("3", "pass", 4.0, "Complexity"),
            ]
        )

        result = _aggregate_comparison(baseline, new)

        assert "by_category" in result
        trade = result["by_category"]["Trade"]
        assert trade["baseline_pass_rate"] == pytest.approx(50.0, abs=0.1)
        assert trade["new_pass_rate"] == pytest.approx(100.0, abs=0.1)


# ---------------------------------------------------------------------------
# Tests: compare_cohorts (integration with mock files)
# ---------------------------------------------------------------------------


class TestCompareCohorts:
    def _create_run_dir(
        self, tmp_path: Path, run_id: str, per_question: list[dict]
    ) -> Path:
        """Create a mock run directory with report.json."""
        run_dir = tmp_path / "evaluation" / "runs" / run_id
        run_dir.mkdir(parents=True)

        report = {
            "aggregate": {
                "count": len(per_question),
                "pass_rate": (
                    sum(1 for q in per_question if q["verdict"] == "pass")
                    / len(per_question)
                    * 100
                    if per_question
                    else 0
                ),
            },
            "per_question": per_question,
        }
        (run_dir / "report.json").write_text(json.dumps(report, indent=2))
        return run_dir

    def test_compare_cohorts_produces_markdown(self, tmp_path):
        """End-to-end: compare_cohorts should return a non-empty Markdown string."""
        baseline_pq = [
            _make_pq("1", "fail", 1.0, "Trade"),
            _make_pq("2", "pass", 4.5, "Complexity"),
        ]
        new_pq = [
            _make_pq("1", "pass", 4.5, "Trade"),
            _make_pq("2", "pass", 4.5, "Complexity"),
        ]
        self._create_run_dir(tmp_path, "baseline_run", baseline_pq)
        self._create_run_dir(tmp_path, "new_run", new_pq)

        runs_dir = tmp_path / "evaluation" / "runs"
        result = compare_cohorts("new_run", "baseline_run", runs_dir=runs_dir)

        assert isinstance(result, str)
        assert len(result) > 0
        assert "Cohort" in result or "cohort" in result.lower()

    def test_compare_cohorts_missing_run(self, tmp_path):
        """Should return error message for missing run directory."""
        runs_dir = tmp_path / "evaluation" / "runs"
        runs_dir.mkdir(parents=True)

        result = compare_cohorts("nonexistent", "also_missing", runs_dir=runs_dir)

        assert "Error" in result or "not found" in result.lower()
