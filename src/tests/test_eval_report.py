"""Unit tests for evaluation report features.

Tests:
- _select_balanced(): category/difficulty coverage, determinism, edge cases
- judge_details field in generate_report() output
- generate_html_report(): valid HTML with embedded JSON from fixture data
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

# Ensure evaluation/ is importable
_EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from run_eval import _select_balanced  # noqa: E402
from report import generate_report, report_to_markdown  # noqa: E402
from html_report import generate_html_report  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def questions_meta() -> dict[str, dict]:
    """Synthetic questions_meta spanning 4 categories × 3 difficulties."""
    meta = {}
    qid = 1
    for cat in ["Trade Values", "Growth", "Complexity", "Services"]:
        for diff in ["easy", "medium", "hard"]:
            for _ in range(3):  # 3 per cell
                meta[str(qid)] = {
                    "text": f"Question {qid} about {cat}",
                    "category": cat,
                    "difficulty": diff,
                }
                qid += 1
    return meta  # 4 cats × 3 diffs × 3 = 36 questions


@pytest.fixture()
def sample_run_results() -> list[dict]:
    return [
        {
            "question_id": "1",
            "question_text": "What is Brazil's total exports?",
            "category": "Trade Values",
            "difficulty": "easy",
            "status": "success",
            "duration_s": 12.5,
            "answer": "Brazil exported $223B in 2018.",
        },
        {
            "question_id": "2",
            "question_text": "Nigeria largest crude oil partner?",
            "category": "Trade Partners",
            "difficulty": "medium",
            "status": "success",
            "duration_s": 8.3,
            "answer": "India was Nigeria's largest crude oil partner.",
        },
        {
            "question_id": "3",
            "question_text": "Bad question that errors",
            "category": "Other",
            "difficulty": "hard",
            "status": "error",
            "duration_s": 1.0,
            "error": "timeout",
        },
    ]


@pytest.fixture()
def sample_judge_results() -> dict[str, dict]:
    return {
        "1": {
            "judge_mode": "ground_truth",
            "factual_correctness": {"score": 5, "reasoning": "Correct value."},
            "data_accuracy": {"score": 4, "reasoning": "Within 1%."},
            "completeness": {"score": 4, "reasoning": "Answered fully."},
            "reasoning_quality": {"score": 5, "reasoning": "Good analysis."},
            "weighted_score": 4.55,
            "verdict": "pass",
            "overall_comment": "Accurate answer.",
        },
        "2": {
            "judge_mode": "plausibility",
            "plausible": True,
            "factually_absurd": False,
            "score": 3,
            "weighted_score": 3.0,
            "verdict": "partial",
            "reasoning": "Plausible but unverified.",
        },
    }


@pytest.fixture()
def fixture_run_dir(tmp_path: Path, sample_run_results, sample_judge_results) -> Path:
    """Create a minimal run directory with report.json and per-question data."""
    run_dir = tmp_path / "20260227T120000Z"
    run_dir.mkdir()

    # Build report using the actual generate_report function
    questions_meta = {
        "1": {
            "text": "What is Brazil's total exports?",
            "category": "Trade Values",
            "difficulty": "easy",
        },
        "2": {
            "text": "Nigeria largest crude oil partner?",
            "category": "Trade Partners",
            "difficulty": "medium",
        },
        "3": {"text": "Bad question", "category": "Other", "difficulty": "hard"},
    }
    report = generate_report(sample_run_results, sample_judge_results, questions_meta)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))

    # Per-question result.json files
    for r in sample_run_results:
        qdir = run_dir / r["question_id"]
        qdir.mkdir()
        (qdir / "result.json").write_text(json.dumps(r, indent=2, default=str))

    return run_dir


# ---------------------------------------------------------------------------
# Tests: _select_balanced
# ---------------------------------------------------------------------------


class TestSelectBalanced:
    def test_returns_requested_count(self, questions_meta):
        result = _select_balanced(questions_meta, 12)
        assert len(result) == 12

    def test_covers_all_categories(self, questions_meta):
        result = _select_balanced(questions_meta, 12)
        categories = {questions_meta[qid]["category"] for qid in result}
        assert categories == {"Trade Values", "Growth", "Complexity", "Services"}

    def test_balanced_across_categories(self, questions_meta):
        result = _select_balanced(questions_meta, 12)
        cat_counts = Counter(questions_meta[qid]["category"] for qid in result)
        # With 12 questions and 4 categories, should get 3 each
        assert all(c == 3 for c in cat_counts.values())

    def test_difficulty_diversity(self, questions_meta):
        result = _select_balanced(questions_meta, 12)
        # Each category should have all 3 difficulties represented
        for cat in ["Trade Values", "Growth", "Complexity", "Services"]:
            cat_qs = [qid for qid in result if questions_meta[qid]["category"] == cat]
            diffs = {questions_meta[qid]["difficulty"] for qid in cat_qs}
            assert diffs == {"easy", "medium", "hard"}

    def test_deterministic(self, questions_meta):
        r1 = _select_balanced(questions_meta, 12)
        r2 = _select_balanced(questions_meta, 12)
        assert r1 == r2

    def test_n_greater_than_total(self, questions_meta):
        result = _select_balanced(questions_meta, 999)
        # Should return all 36 questions
        assert len(result) == 36
        assert set(result) == set(questions_meta.keys())

    def test_n_zero(self, questions_meta):
        result = _select_balanced(questions_meta, 0)
        assert result == []

    def test_single_category(self):
        meta = {
            "1": {"category": "A", "difficulty": "easy", "text": "Q1"},
            "2": {"category": "A", "difficulty": "medium", "text": "Q2"},
            "3": {"category": "A", "difficulty": "hard", "text": "Q3"},
        }
        result = _select_balanced(meta, 2)
        assert len(result) == 2
        # Should get easy first, then medium
        assert result[0] == "1"
        assert result[1] == "2"

    def test_returns_strings(self, questions_meta):
        result = _select_balanced(questions_meta, 5)
        assert all(isinstance(qid, str) for qid in result)

    def test_no_duplicates(self, questions_meta):
        result = _select_balanced(questions_meta, 20)
        assert len(result) == len(set(result))


# ---------------------------------------------------------------------------
# Tests: judge_details in generate_report
# ---------------------------------------------------------------------------


class TestJudgeDetails:
    def test_judge_details_present(self, sample_run_results, sample_judge_results):
        meta = {
            "1": {"category": "Trade Values", "difficulty": "easy"},
            "2": {"category": "Trade Partners", "difficulty": "medium"},
            "3": {"category": "Other", "difficulty": "hard"},
        }
        report = generate_report(sample_run_results, sample_judge_results, meta)

        for entry in report["per_question"]:
            assert "judge_details" in entry

    def test_judge_details_matches_verdict(
        self, sample_run_results, sample_judge_results
    ):
        meta = {
            "1": {"category": "Trade Values", "difficulty": "easy"},
            "2": {"category": "Trade Partners", "difficulty": "medium"},
            "3": {"category": "Other", "difficulty": "hard"},
        }
        report = generate_report(sample_run_results, sample_judge_results, meta)

        q1 = next(q for q in report["per_question"] if q["question_id"] == "1")
        assert q1["judge_details"]["verdict"] == "pass"
        assert q1["judge_details"]["judge_mode"] == "ground_truth"
        assert "factual_correctness" in q1["judge_details"]

    def test_judge_details_empty_for_unjudged(
        self, sample_run_results, sample_judge_results
    ):
        meta = {"3": {"category": "Other", "difficulty": "hard"}}
        report = generate_report(sample_run_results, sample_judge_results, meta)

        q3 = next(q for q in report["per_question"] if q["question_id"] == "3")
        assert q3["judge_details"] == {}


# ---------------------------------------------------------------------------
# Tests: generate_html_report
# ---------------------------------------------------------------------------


class TestHtmlReport:
    def test_generates_html_file(self, fixture_run_dir):
        html_path = generate_html_report(fixture_run_dir)
        assert html_path.exists()
        assert html_path.suffix == ".html"

    def test_html_contains_report_data(self, fixture_run_dir):
        html_path = generate_html_report(fixture_run_dir)
        html_content = html_path.read_text(encoding="utf-8")

        # Should contain question text
        assert "Brazil" in html_content
        assert "Nigeria" in html_content

    def test_html_is_self_contained(self, fixture_run_dir):
        html_path = generate_html_report(fixture_run_dir)
        html_content = html_path.read_text(encoding="utf-8")

        # Should have HTML structure
        assert "<!DOCTYPE html>" in html_content
        assert "<style>" in html_content
        assert "<script>" in html_content
        assert "const REPORT =" in html_content

    def test_embedded_json_is_parseable(self, fixture_run_dir):
        html_path = generate_html_report(fixture_run_dir)
        html_content = html_path.read_text(encoding="utf-8")

        # Extract JSON blob between "const REPORT = " and ";\n"
        start = html_content.index("const REPORT = ") + len("const REPORT = ")
        end = html_content.index(";\n", start)
        json_blob = html_content[start:end]
        data = json.loads(json_blob)

        assert "aggregate" in data
        assert "per_question" in data
        assert len(data["per_question"]) == 3

    def test_missing_report_json_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            generate_html_report(tmp_path)

    def test_enriched_data_includes_agent_answer(self, fixture_run_dir):
        html_path = generate_html_report(fixture_run_dir)
        html_content = html_path.read_text(encoding="utf-8")

        start = html_content.index("const REPORT = ") + len("const REPORT = ")
        end = html_content.index(";\n", start)
        data = json.loads(html_content[start:end])

        q1 = next(q for q in data["per_question"] if q["question_id"] == "1")
        assert q1["agent_answer"] == "Brazil exported $223B in 2018."


# ---------------------------------------------------------------------------
# Tests: cost analysis in report
# ---------------------------------------------------------------------------


class TestCostAnalysis:
    def test_report_includes_cost_analysis(self):
        """When run_results contain cost data, report should include cost_analysis."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Brazil exports?",
                "category": "Trade Values",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 10.0,
                "cost": {
                    "total_cost_usd": 0.0035,
                    "by_pipeline": {"agent": 0.001, "query_tool": 0.0025},
                    "record_count": 3,
                },
                "token_usage": {
                    "by_pipeline": {
                        "agent": {
                            "input_tokens": 500,
                            "output_tokens": 100,
                            "total_tokens": 600,
                        },
                        "query_tool": {
                            "input_tokens": 1500,
                            "output_tokens": 300,
                            "total_tokens": 1800,
                        },
                    },
                    "total": {
                        "input_tokens": 2000,
                        "output_tokens": 400,
                        "total_tokens": 2400,
                    },
                },
                "tool_call_counts": {"query_tool": 1},
            },
            {
                "question_id": "2",
                "question_text": "India ECI?",
                "category": "Complexity",
                "difficulty": "medium",
                "status": "success",
                "duration_s": 8.0,
                "cost": {
                    "total_cost_usd": 0.0042,
                    "by_pipeline": {"agent": 0.0012, "atlas_graphql": 0.003},
                    "record_count": 4,
                },
                "token_usage": {
                    "by_pipeline": {
                        "agent": {
                            "input_tokens": 600,
                            "output_tokens": 120,
                            "total_tokens": 720,
                        },
                        "atlas_graphql": {
                            "input_tokens": 1800,
                            "output_tokens": 250,
                            "total_tokens": 2050,
                        },
                    },
                    "total": {
                        "input_tokens": 2400,
                        "output_tokens": 370,
                        "total_tokens": 2770,
                    },
                },
                "tool_call_counts": {"atlas_graphql": 1},
            },
        ]
        judge_results = {}
        questions_meta = {
            "1": {"category": "Trade Values", "difficulty": "easy"},
            "2": {"category": "Complexity", "difficulty": "medium"},
        }

        report = generate_report(run_results, judge_results, questions_meta)

        assert "cost_analysis" in report
        ca = report["cost_analysis"]
        assert ca["total_cost_usd"] == pytest.approx(0.0077, abs=1e-4)
        assert ca["questions_with_costs"] == 2
        assert "agent" in ca["cost_by_pipeline"]
        assert "query_tool" in ca["tool_call_totals"]
        assert "atlas_graphql" in ca["tool_call_totals"]

    def test_markdown_includes_cost_section(self):
        """report_to_markdown should include Cost Analysis heading when data is present."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "cost": {
                    "total_cost_usd": 0.005,
                    "by_pipeline": {"agent": 0.005},
                    "record_count": 1,
                },
                "token_usage": {
                    "by_pipeline": {
                        "agent": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "total_tokens": 150,
                        },
                    },
                    "total": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "total_tokens": 150,
                    },
                },
                "tool_call_counts": {"query_tool": 1},
            },
        ]
        report = generate_report(
            run_results, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )
        md = report_to_markdown(report)

        assert "## Cost Analysis" in md
        assert "$0.005" in md

    def test_no_cost_data_no_section(self):
        """When no run_results have cost data, cost_analysis should be absent."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
            },
        ]
        report = generate_report(
            run_results, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )

        assert "cost_analysis" not in report

    def test_per_question_cost_in_report(self):
        """Per-question entries should include cost_usd when cost data is available."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "cost": {
                    "total_cost_usd": 0.003,
                    "by_pipeline": {"agent": 0.003},
                    "record_count": 1,
                },
            },
        ]
        report = generate_report(
            run_results, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )

        q1 = report["per_question"][0]
        assert q1["cost_usd"] == 0.003


# ---------------------------------------------------------------------------
# Tests: latency analysis in report
# ---------------------------------------------------------------------------


class TestLatencyAnalysis:
    def _make_timing_results(self):
        """Helper: run results with step_timing data."""
        return [
            {
                "question_id": "1",
                "question_text": "Brazil exports?",
                "category": "Trade Values",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 10.0,
                "step_timing_summary": {
                    "by_node": {
                        "agent": {
                            "wall_time_ms": 2000,
                            "llm_time_ms": 1800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                        "generate_sql": {
                            "wall_time_ms": 4000,
                            "llm_time_ms": 3500,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                        "execute_sql": {
                            "wall_time_ms": 3000,
                            "llm_time_ms": 0,
                            "io_time_ms": 2800,
                            "call_count": 1,
                        },
                    },
                    "by_pipeline": {
                        "agent": {"wall_time_ms": 2000, "call_count": 1},
                        "query_tool": {"wall_time_ms": 7000, "call_count": 2},
                    },
                    "total": {
                        "wall_time_ms": 9000,
                        "llm_time_ms": 5300,
                        "io_time_ms": 2800,
                        "overhead_ms": 900,
                    },
                    "slowest_node": {"node": "generate_sql", "wall_time_ms": 4000},
                },
            },
            {
                "question_id": "2",
                "question_text": "India ECI?",
                "category": "Complexity",
                "difficulty": "medium",
                "status": "success",
                "duration_s": 15.0,
                "step_timing_summary": {
                    "by_node": {
                        "agent": {
                            "wall_time_ms": 3000,
                            "llm_time_ms": 2500,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                        "classify_query": {
                            "wall_time_ms": 1000,
                            "llm_time_ms": 800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                    },
                    "by_pipeline": {
                        "agent": {"wall_time_ms": 3000, "call_count": 1},
                        "atlas_graphql": {"wall_time_ms": 1000, "call_count": 1},
                    },
                    "total": {
                        "wall_time_ms": 4000,
                        "llm_time_ms": 3300,
                        "io_time_ms": 0,
                        "overhead_ms": 700,
                    },
                    "slowest_node": {"node": "agent", "wall_time_ms": 3000},
                },
            },
        ]

    def test_report_includes_latency_analysis(self):
        """When results have step_timing_summary, report should include latency_analysis."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )

        assert "latency_analysis" in report
        la = report["latency_analysis"]
        assert "avg_total_ms" in la
        assert "p50_total_ms" in la
        assert "p90_total_ms" in la
        assert "p95_total_ms" in la
        assert "avg_by_node" in la
        assert "time_breakdown" in la
        assert la["questions_with_timing"] == 2

    def test_latency_percentiles_correct(self):
        """Percentile calculations should be reasonable."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )

        la = report["latency_analysis"]
        # avg of 9000 and 4000 = 6500
        assert la["avg_total_ms"] == pytest.approx(6500.0, abs=1)
        # With 2 values, p50 should be between them
        assert la["p50_total_ms"] >= 4000
        assert la["p50_total_ms"] <= 9000

    def test_time_breakdown_percentages(self):
        """Time breakdown should show LLM, I/O, and overhead as percentages."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )

        tb = report["latency_analysis"]["time_breakdown"]
        assert "llm_pct" in tb
        assert "io_pct" in tb
        assert "overhead_pct" in tb
        # Percentages should sum to ~100
        total = tb["llm_pct"] + tb["io_pct"] + tb["overhead_pct"]
        assert total == pytest.approx(100.0, abs=1)

    def test_no_timing_data_no_section(self):
        """When no results have step_timing_summary, latency_analysis should be absent."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
            },
        ]
        report = generate_report(
            run_results, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )

        assert "latency_analysis" not in report

    def test_markdown_includes_latency_section(self):
        """report_to_markdown should include Latency Analysis heading."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )
        md = report_to_markdown(report)

        assert "## Latency Analysis" in md
        assert "Bottleneck Nodes" in md


# ---------------------------------------------------------------------------
# Tests: budget violations
# ---------------------------------------------------------------------------


class TestBudgetViolations:
    def test_duration_threshold_violation(self):
        """Questions exceeding duration threshold should be flagged."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Fast question?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
            },
            {
                "question_id": "2",
                "question_text": "Slow question?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 45.0,
            },
        ]
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "A", "difficulty": "easy"},
                "2": {"category": "A", "difficulty": "easy"},
            },
        )

        assert "budget_violations" in report
        bv = report["budget_violations"]
        assert bv["duration_violations"] == 1
        # Question 2 exceeds the 30s threshold
        assert any(v["question_id"] == "2" for v in bv["violations"])

    def test_cost_threshold_violation(self):
        """Questions exceeding cost threshold should be flagged."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Cheap question?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "cost": {"total_cost_usd": 0.01, "by_pipeline": {}, "record_count": 1},
            },
            {
                "question_id": "2",
                "question_text": "Expensive question?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "cost": {"total_cost_usd": 0.08, "by_pipeline": {}, "record_count": 1},
            },
        ]
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "A", "difficulty": "easy"},
                "2": {"category": "A", "difficulty": "easy"},
            },
        )

        assert "budget_violations" in report
        bv = report["budget_violations"]
        assert bv["cost_violations"] == 1

    def test_no_violations_no_section(self):
        """When no thresholds are exceeded, budget_violations should be absent."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Normal question?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "cost": {"total_cost_usd": 0.01, "by_pipeline": {}, "record_count": 1},
            },
        ]
        report = generate_report(
            run_results,
            {},
            {"1": {"category": "A", "difficulty": "easy"}},
        )

        assert "budget_violations" not in report


# ---------------------------------------------------------------------------
# Tests: pipeline latency in report
# ---------------------------------------------------------------------------


class TestPipelineLatency:
    """Tests for per-pipeline latency aggregation in _aggregate_latency()."""

    def _make_timing_results(self):
        """Reuse the same fixture shape as TestLatencyAnalysis."""
        return [
            {
                "question_id": "1",
                "question_text": "Brazil exports?",
                "category": "Trade Values",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 10.0,
                "step_timing_summary": {
                    "by_node": {
                        "agent": {
                            "wall_time_ms": 2000,
                            "llm_time_ms": 1800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                    },
                    "by_pipeline": {
                        "agent": {
                            "wall_time_ms": 2000,
                            "llm_time_ms": 1800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                        "query_tool": {
                            "wall_time_ms": 7000,
                            "llm_time_ms": 3500,
                            "io_time_ms": 2800,
                            "call_count": 2,
                        },
                    },
                    "total": {
                        "wall_time_ms": 9000,
                        "llm_time_ms": 5300,
                        "io_time_ms": 2800,
                        "overhead_ms": 900,
                    },
                },
            },
            {
                "question_id": "2",
                "question_text": "India ECI?",
                "category": "Complexity",
                "difficulty": "medium",
                "status": "success",
                "duration_s": 15.0,
                "step_timing_summary": {
                    "by_node": {
                        "agent": {
                            "wall_time_ms": 3000,
                            "llm_time_ms": 2500,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                    },
                    "by_pipeline": {
                        "agent": {
                            "wall_time_ms": 3000,
                            "llm_time_ms": 2500,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                        "atlas_graphql": {
                            "wall_time_ms": 1000,
                            "llm_time_ms": 800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                    },
                    "total": {
                        "wall_time_ms": 4000,
                        "llm_time_ms": 3300,
                        "io_time_ms": 0,
                        "overhead_ms": 700,
                    },
                },
            },
        ]

    def test_report_includes_avg_by_pipeline(self):
        """When timing data has by_pipeline, report should include avg_by_pipeline."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )

        assert "latency_analysis" in report
        la = report["latency_analysis"]
        assert "avg_by_pipeline" in la
        assert "agent" in la["avg_by_pipeline"]

    def test_pipeline_averages_computed_correctly(self):
        """Verify arithmetic: agent appears in both, query_tool in Q1, atlas_graphql in Q2."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )

        avg_by_pipeline = report["latency_analysis"]["avg_by_pipeline"]

        # "agent" appears in both questions: avg wall = (2000+3000)/2 = 2500
        assert avg_by_pipeline["agent"]["avg_wall_time_ms"] == pytest.approx(
            2500.0, abs=1
        )
        assert avg_by_pipeline["agent"]["appearances"] == 2

        # "query_tool" only in Q1: avg wall = 7000/1 = 7000
        assert avg_by_pipeline["query_tool"]["avg_wall_time_ms"] == pytest.approx(
            7000.0, abs=1
        )
        assert avg_by_pipeline["query_tool"]["appearances"] == 1

        # "atlas_graphql" only in Q2: avg wall = 1000/1 = 1000
        assert avg_by_pipeline["atlas_graphql"]["avg_wall_time_ms"] == pytest.approx(
            1000.0, abs=1
        )
        assert avg_by_pipeline["atlas_graphql"]["appearances"] == 1

        # Verify LLM time for agent: (1800+2500)/2 = 2150
        assert avg_by_pipeline["agent"]["avg_llm_time_ms"] == pytest.approx(
            2150.0, abs=1
        )

    def test_markdown_includes_pipeline_section(self):
        """report_to_markdown should include Pipeline Latency heading."""
        run_results = self._make_timing_results()
        report = generate_report(
            run_results,
            {},
            {
                "1": {"category": "Trade Values", "difficulty": "easy"},
                "2": {"category": "Complexity", "difficulty": "medium"},
            },
        )
        md = report_to_markdown(report)

        assert "### Pipeline Latency" in md
        assert "agent" in md
        assert "query_tool" in md

    def test_no_pipeline_data_no_section(self):
        """When timing data lacks by_pipeline, avg_by_pipeline should be absent."""
        run_results = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
            },
        ]
        report = generate_report(
            run_results, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )

        # No timing data at all → no latency_analysis
        assert "latency_analysis" not in report

        # Also test: timing present but no by_pipeline key
        run_results_no_pipeline = [
            {
                "question_id": "1",
                "question_text": "Test?",
                "category": "A",
                "difficulty": "easy",
                "status": "success",
                "duration_s": 5.0,
                "step_timing_summary": {
                    "by_node": {
                        "agent": {
                            "wall_time_ms": 1000,
                            "llm_time_ms": 800,
                            "io_time_ms": 0,
                            "call_count": 1,
                        },
                    },
                    "total": {
                        "wall_time_ms": 1000,
                        "llm_time_ms": 800,
                        "io_time_ms": 0,
                        "overhead_ms": 200,
                    },
                },
            },
        ]
        report2 = generate_report(
            run_results_no_pipeline, {}, {"1": {"category": "A", "difficulty": "easy"}}
        )
        la = report2.get("latency_analysis", {})
        assert la.get("avg_by_pipeline") is None or la.get("avg_by_pipeline") == {}
        # Markdown should not include Pipeline Latency section
        md = report_to_markdown(report2)
        assert "### Pipeline Latency" not in md
