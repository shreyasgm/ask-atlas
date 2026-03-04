"""Unit tests for evaluation/review_server.py.

Tests:
- GT data archive logic (correct-gt endpoint)
- GT URL archive logic (correct-gt-url endpoint)
- Classification persistence (classify endpoint)
- Rejudge endpoint (mocked judge calls)
- Reviewer identity endpoint
- Review HTML generation
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure evaluation/ is importable
_EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from review_server import (  # noqa: E402
    app,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory with report.json and result files."""
    run = tmp_path / "runs" / "20260301T120000Z"
    run.mkdir(parents=True)

    # Create a minimal report.json
    report = {
        "timestamp": "2026-03-01T12:00:00Z",
        "judge_model": "gpt-5-mini",
        "judge_provider": "openai",
        "aggregate": {"count": 2, "pass_rate": 50},
        "per_question": [
            {
                "question_id": "1",
                "question_text": "What are Brazil's top exports?",
                "category": "Trade Values",
                "difficulty": "easy",
                "status": "success",
                "verdict": "pass",
                "weighted_score": 4.2,
                "judge_mode": "ground_truth",
                "judge_comment": "Good answer.",
                "judge_details": {
                    "judge_mode": "ground_truth",
                    "factual_correctness": {"score": 4, "reasoning": "ok"},
                    "weighted_score": 4.2,
                    "verdict": "pass",
                },
            },
            {
                "question_id": "10",
                "question_text": "Top partners for Kenya?",
                "category": "Trade Values",
                "difficulty": "medium",
                "status": "success",
                "verdict": "fail",
                "weighted_score": 2.0,
                "judge_mode": "ground_truth",
                "judge_comment": "Wrong data.",
                "judge_details": {
                    "judge_mode": "ground_truth",
                    "factual_correctness": {"score": 2, "reasoning": "bad"},
                    "weighted_score": 2.0,
                    "verdict": "fail",
                },
            },
        ],
    }
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")

    # Create per-question result.json files
    q1_dir = run / "1"
    q1_dir.mkdir()
    (q1_dir / "result.json").write_text(
        json.dumps(
            {
                "question_id": "1",
                "status": "success",
                "answer": "Brazil exports soybeans and iron ore.",
                "tools_used": ["atlas_graphql"],
                "graphql_atlas_links": [
                    {"url": "https://atlas.hks.harvard.edu/explore?exporter=76"}
                ],
            }
        ),
        encoding="utf-8",
    )

    q10_dir = run / "10"
    q10_dir.mkdir()
    (q10_dir / "result.json").write_text(
        json.dumps(
            {
                "question_id": "10",
                "status": "success",
                "answer": "Kenya's top partners are USA and UK.",
                "tools_used": ["query_tool"],
            }
        ),
        encoding="utf-8",
    )

    return run


@pytest.fixture()
def gt_dir(tmp_path: Path) -> Path:
    """Create ground truth files in the evaluation results structure."""
    eval_dir = tmp_path / "evaluation"
    eval_dir.mkdir()

    # Q1 GT
    gt1 = eval_dir / "results" / "1" / "ground_truth"
    gt1.mkdir(parents=True)
    (gt1 / "results.json").write_text(
        json.dumps(
            {
                "question_id": "1",
                "execution_timestamp": "2026-02-28T10:00:00Z",
                "source": "atlas_explore_page",
                "atlas_url": "https://atlas.hks.harvard.edu/explore?exporter=76",
                "results": {
                    "data": [
                        {"product": "Soybeans", "value": "$33B", "year": "2024"},
                        {"product": "Iron Ore", "value": "$28B", "year": "2024"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    # Q10 GT (no atlas_url)
    gt10 = eval_dir / "results" / "10" / "ground_truth"
    gt10.mkdir(parents=True)
    (gt10 / "results.json").write_text(
        json.dumps(
            {
                "question_id": "10",
                "execution_timestamp": "2026-02-28T10:00:00Z",
                "source": "atlas_explore_page",
                "results": {
                    "data": [
                        {"partner": "USA", "value": "$1.2B"},
                        {"partner": "UK", "value": "$900M"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    # Create eval_questions.json
    (eval_dir / "eval_questions.json").write_text(
        json.dumps(
            {
                "categories": [{"id": "total_export_values", "name": "Trade Values"}],
                "questions": [
                    {
                        "id": 1,
                        "category_id": "total_export_values",
                        "difficulty": "easy",
                        "text": "What are Brazil's top exports?",
                    },
                    {
                        "id": 10,
                        "category_id": "total_export_values",
                        "difficulty": "medium",
                        "text": "Top partners for Kenya?",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    return eval_dir


@pytest.fixture()
def _patch_dirs(run_dir: Path, gt_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch review_server globals so endpoints use temp dirs."""
    import review_server

    monkeypatch.setattr(review_server, "RUN_DIR", run_dir)
    # Patch EVALUATION_BASE_DIR used by _gt_path
    monkeypatch.setattr(review_server, "EVALUATION_BASE_DIR", gt_dir)


@pytest.fixture()
def client(_patch_dirs: None) -> AsyncClient:
    """Create a test client for the review server."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewer:
    @pytest.mark.anyio()
    async def test_reviewer_endpoint_returns_all_fields(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/reviewer")
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"name", "email", "display"}
        # display should be non-empty (falls back to "anonymous" if git not configured)
        assert len(data["display"]) > 0


class TestClassify:
    @pytest.mark.anyio()
    async def test_classify_saves_to_report(
        self, client: AsyncClient, run_dir: Path
    ) -> None:
        resp = await client.post(
            "/api/classify/10",
            json={"classification": "gt_data_needs_correction", "note": "Wrong data"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["status"] == "ok"
        assert result["review"]["classification"] == "gt_data_needs_correction"
        assert result["review"]["note"] == "Wrong data"
        assert "reviewed_by" in result["review"]
        assert "reviewed_at" in result["review"]

        # Verify persistence in report.json
        report = json.loads((run_dir / "report.json").read_text())
        q10 = next(q for q in report["per_question"] if q["question_id"] == "10")
        assert q10["review"]["classification"] == "gt_data_needs_correction"

    @pytest.mark.anyio()
    async def test_classify_unknown_question_404(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/classify/999",
            json={"classification": "agent_error"},
        )
        assert resp.status_code == 404

    @pytest.mark.anyio()
    async def test_classify_overwrites_previous(
        self, client: AsyncClient, run_dir: Path
    ) -> None:
        await client.post("/api/classify/1", json={"classification": "agent_error"})
        await client.post(
            "/api/classify/1",
            json={"classification": "reviewed_ok", "note": "Actually fine"},
        )

        report = json.loads((run_dir / "report.json").read_text())
        q1 = next(q for q in report["per_question"] if q["question_id"] == "1")
        assert q1["review"]["classification"] == "reviewed_ok"
        assert q1["review"]["note"] == "Actually fine"


class TestCorrectGTData:
    @pytest.mark.anyio()
    async def test_correct_gt_archives_old_data(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        new_data = [{"product": "Soybeans", "value": "$35B", "year": "2024"}]
        resp = await client.post(
            "/api/correct-gt/1",
            json={"data": new_data, "note": "Updated value from latest Atlas data"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["archived_count"] == 2  # 2 old rows
        assert result["new_count"] == 1

        # Verify the GT file
        gt = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        assert len(gt["results"]["data"]) == 1
        assert gt["results"]["data"][0]["value"] == "$35B"

        # Verify archive
        assert len(gt["data_archived"]) == 2
        assert gt["data_archived"][0]["product"] == "Soybeans"
        assert gt["data_archived"][0]["value"] == "$33B"
        assert "archived_at" in gt["data_archived"][0]
        assert "archived_by" in gt["data_archived"][0]
        assert gt["data_archived"][0]["note"] == "Updated value from latest Atlas data"

    @pytest.mark.anyio()
    async def test_correct_gt_missing_note_fails(self, client: AsyncClient) -> None:
        """Pydantic validation should reject requests without a note."""
        resp = await client.post(
            "/api/correct-gt/1",
            json={"data": [{"x": 1}]},
        )
        assert resp.status_code == 422  # Validation error

    @pytest.mark.anyio()
    async def test_correct_gt_nonexistent_question(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/correct-gt/999",
            json={"data": [], "note": "test"},
        )
        assert resp.status_code == 404

    @pytest.mark.anyio()
    async def test_correct_gt_preserves_existing_archive(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        """Two corrections should accumulate archived entries."""
        await client.post(
            "/api/correct-gt/1",
            json={"data": [{"product": "Soy", "value": "$35B"}], "note": "first fix"},
        )
        await client.post(
            "/api/correct-gt/1",
            json={"data": [{"product": "Soy", "value": "$36B"}], "note": "second fix"},
        )
        gt = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        # Original 2 rows + 1 from first correction
        assert len(gt["data_archived"]) == 3
        assert gt["results"]["data"][0]["value"] == "$36B"

    @pytest.mark.anyio()
    async def test_correct_gt_updates_execution_timestamp(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        """execution_timestamp should be updated to reflect the correction time."""
        gt_before = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        old_ts = gt_before["execution_timestamp"]

        await client.post(
            "/api/correct-gt/1",
            json={"data": [{"product": "Soy", "value": "$35B"}], "note": "fix"},
        )

        gt_after = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        assert gt_after["execution_timestamp"] != old_ts
        # New timestamp should be a valid ISO string later than the original
        assert gt_after["execution_timestamp"] > old_ts

    @pytest.mark.anyio()
    async def test_correct_gt_with_empty_data_array(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        """Correcting GT to an empty array should archive old data and set empty data."""
        resp = await client.post(
            "/api/correct-gt/1",
            json={"data": [], "note": "Removing GT — question is out of scope"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["archived_count"] == 2
        assert result["new_count"] == 0

        gt = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        assert gt["results"]["data"] == []
        assert len(gt["data_archived"]) == 2


class TestCorrectGTUrl:
    @pytest.mark.anyio()
    async def test_correct_gt_url_archives_old(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        new_url = "https://atlas.hks.harvard.edu/explore?exporter=76&year=2024"
        resp = await client.post(
            "/api/correct-gt-url/1",
            json={"atlas_url": new_url, "note": "Added year param"},
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["old_url"] == "https://atlas.hks.harvard.edu/explore?exporter=76"
        assert result["new_url"] == new_url

        gt = json.loads(
            (gt_dir / "results" / "1" / "ground_truth" / "results.json").read_text()
        )
        assert gt["atlas_url"] == new_url
        assert any(
            e.get("type") == "atlas_url_archived" for e in gt.get("data_archived", [])
        )

    @pytest.mark.anyio()
    async def test_correct_gt_url_no_previous_url(
        self, client: AsyncClient, gt_dir: Path
    ) -> None:
        """Q10 has no atlas_url; correction should add it without archiving."""
        resp = await client.post(
            "/api/correct-gt-url/10",
            json={
                "atlas_url": "https://atlas.hks.harvard.edu/explore?exporter=404",
                "note": "Added URL",
            },
        )
        assert resp.status_code == 200
        result = resp.json()
        assert result["old_url"] is None

        gt = json.loads(
            (gt_dir / "results" / "10" / "ground_truth" / "results.json").read_text()
        )
        assert gt["atlas_url"] == "https://atlas.hks.harvard.edu/explore?exporter=404"
        # No URL archive entry since there was no old URL
        archived_urls = [
            e
            for e in gt.get("data_archived", [])
            if e.get("type") == "atlas_url_archived"
        ]
        assert len(archived_urls) == 0

    @pytest.mark.anyio()
    async def test_correct_gt_url_missing_note_fails(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/correct-gt-url/1",
            json={"atlas_url": "https://example.com"},
        )
        assert resp.status_code == 422


class TestRejudge:
    @pytest.mark.anyio()
    async def test_rejudge_calls_judge_and_updates_report(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        mock_verdict = {
            "judge_mode": "ground_truth",
            "verdict": "pass",
            "weighted_score": 4.5,
            "overall_comment": "Excellent after correction.",
            "factual_correctness": {"score": 5, "reasoning": "spot on"},
        }
        with patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = mock_verdict
            # Also patch EVALUATION_BASE_DIR for eval_questions.json
            resp = await client.post("/api/rejudge/10")

        assert resp.status_code == 200
        result = resp.json()
        assert result["verdict"] == "pass"
        assert result["weighted_score"] == 4.5

        # Verify report.json was updated
        report = json.loads((run_dir / "report.json").read_text())
        q10 = next(q for q in report["per_question"] if q["question_id"] == "10")
        assert q10["verdict"] == "pass"
        assert q10["weighted_score"] == 4.5

    @pytest.mark.anyio()
    async def test_rejudge_with_link_judge(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        """Q1 uses graphql, so link judge should also be called."""
        mock_answer_verdict = {
            "judge_mode": "ground_truth",
            "verdict": "pass",
            "weighted_score": 4.0,
            "overall_comment": "Good.",
        }
        mock_link_verdict = {
            "verdict": "pass",
            "weighted_score": 3.8,
            "overall_comment": "Links are relevant.",
        }
        with (
            patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge,
            patch(
                "review_server.judge_links", new_callable=AsyncMock
            ) as mock_link_judge,
        ):
            mock_judge.return_value = mock_answer_verdict
            mock_link_judge.return_value = mock_link_verdict
            resp = await client.post("/api/rejudge/1")

        assert resp.status_code == 200
        result = resp.json()
        assert result["link_verdict"] is not None
        assert result["link_verdict"]["verdict"] == "pass"

        # Verify link judge was called
        mock_link_judge.assert_called_once()

    @pytest.mark.anyio()
    async def test_rejudge_passes_correct_args_to_judge(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        """Verify judge_answer is called with the right question, answer, GT data, and model."""
        mock_verdict = {
            "judge_mode": "ground_truth",
            "verdict": "pass",
            "weighted_score": 4.0,
            "overall_comment": "ok",
        }
        with patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = mock_verdict
            await client.post("/api/rejudge/10")

        mock_judge.assert_called_once()
        call_kwargs = mock_judge.call_args.kwargs
        assert call_kwargs["question"] == "Top partners for Kenya?"
        assert call_kwargs["agent_answer"] == "Kenya's top partners are USA and UK."
        assert call_kwargs["model"] == "gpt-5-mini"
        assert call_kwargs["provider"] == "openai"
        # Q10 GT has data, so ground_truth_data should be passed
        assert call_kwargs["ground_truth_data"] is not None
        assert len(call_kwargs["ground_truth_data"]) == 2

    @pytest.mark.anyio()
    async def test_rejudge_sql_only_skips_link_judge(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        """Q10 uses query_tool (not graphql), so link judge should NOT be called."""
        mock_verdict = {
            "judge_mode": "ground_truth",
            "verdict": "pass",
            "weighted_score": 4.0,
            "overall_comment": "ok",
        }
        with (
            patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge,
            patch(
                "review_server.judge_links", new_callable=AsyncMock
            ) as mock_link_judge,
        ):
            mock_judge.return_value = mock_verdict
            resp = await client.post("/api/rejudge/10")

        assert resp.status_code == 200
        mock_link_judge.assert_not_called()
        assert resp.json()["link_verdict"] is None

    @pytest.mark.anyio()
    async def test_rejudge_failed_agent_returns_400(
        self, client: AsyncClient, run_dir: Path
    ) -> None:
        """Rejudge should fail with 400 if agent result was not successful."""
        # Overwrite Q10's result to simulate a failed agent run
        (run_dir / "10" / "result.json").write_text(
            json.dumps({"question_id": "10", "status": "error", "error": "timeout"}),
            encoding="utf-8",
        )
        resp = await client.post("/api/rejudge/10")
        assert resp.status_code == 400

    @pytest.mark.anyio()
    async def test_rejudge_nonexistent_question(self, client: AsyncClient) -> None:
        resp = await client.post("/api/rejudge/999")
        assert resp.status_code == 404


class TestReviewWorkflowIntegration:
    """Multi-step integration tests that exercise the full review workflow."""

    @pytest.mark.anyio()
    async def test_classify_then_correct_gt_then_rejudge(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        """Full reviewer workflow: classify a failure, correct GT, re-judge.

        This verifies that all three actions compose correctly — classification
        is preserved after GT correction, GT correction is used by re-judge, and
        the final report reflects all changes.
        """
        # Step 1: Classify Q10 as needing GT correction
        resp = await client.post(
            "/api/classify/10",
            json={
                "classification": "gt_data_needs_correction",
                "note": "Values are wrong",
            },
        )
        assert resp.status_code == 200

        # Step 2: Correct the GT data
        corrected_data = [
            {"partner": "China", "value": "$2.1B"},
            {"partner": "USA", "value": "$1.5B"},
        ]
        resp = await client.post(
            "/api/correct-gt/10",
            json={"data": corrected_data, "note": "Updated from Atlas 2024 data"},
        )
        assert resp.status_code == 200

        # Step 3: Re-judge with corrected GT
        mock_verdict = {
            "judge_mode": "ground_truth",
            "verdict": "partial",
            "weighted_score": 3.2,
            "overall_comment": "Partially correct with new GT.",
        }
        with patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = mock_verdict
            resp = await client.post("/api/rejudge/10")
            # Verify judge was called with the CORRECTED GT data
            call_kwargs = mock_judge.call_args.kwargs
            assert call_kwargs["ground_truth_data"] == corrected_data

        assert resp.status_code == 200

        # Step 4: Verify final state of report.json has both classification and new verdict
        report = json.loads((run_dir / "report.json").read_text())
        q10 = next(q for q in report["per_question"] if q["question_id"] == "10")
        assert q10["review"]["classification"] == "gt_data_needs_correction"
        assert q10["verdict"] == "partial"
        assert q10["weighted_score"] == 3.2

        # Step 5: Verify GT file has original data archived and corrected data active
        gt = json.loads(
            (gt_dir / "results" / "10" / "ground_truth" / "results.json").read_text()
        )
        assert gt["results"]["data"] == corrected_data
        assert len(gt["data_archived"]) == 2  # 2 original rows archived
        assert gt["data_archived"][0]["partner"] == "USA"
        assert gt["data_archived"][1]["partner"] == "UK"

    @pytest.mark.anyio()
    async def test_correct_url_then_rejudge_triggers_link_judge(
        self, client: AsyncClient, run_dir: Path, gt_dir: Path
    ) -> None:
        """Correcting a GT URL and then re-judging should pass the new URL to link judge."""
        new_url = "https://atlas.hks.harvard.edu/explore/treemap?exporter=76&year=2024"

        # Correct URL for Q1 (which uses graphql)
        resp = await client.post(
            "/api/correct-gt-url/1",
            json={"atlas_url": new_url, "note": "More specific URL"},
        )
        assert resp.status_code == 200

        mock_answer = {
            "judge_mode": "ground_truth",
            "verdict": "pass",
            "weighted_score": 4.0,
            "overall_comment": "ok",
        }
        mock_link = {
            "verdict": "pass",
            "weighted_score": 4.5,
            "overall_comment": "Great links.",
        }
        with (
            patch("review_server.judge_answer", new_callable=AsyncMock) as mock_judge,
            patch(
                "review_server.judge_links", new_callable=AsyncMock
            ) as mock_link_judge,
        ):
            mock_judge.return_value = mock_answer
            mock_link_judge.return_value = mock_link
            resp = await client.post("/api/rejudge/1")

            # Verify link judge was called with the CORRECTED URL
            link_call_kwargs = mock_link_judge.call_args.kwargs
            assert link_call_kwargs["ground_truth_url"] == new_url

        assert resp.status_code == 200
        assert resp.json()["link_verdict"]["weighted_score"] == 4.5


class TestServeReport:
    @pytest.mark.anyio()
    async def test_serve_review_html_has_review_ui_and_data(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        html = resp.text
        # Review banner injected before the main <h1> heading (not before <title>)
        body_start = html.index("<body>")
        banner_pos = html.index("review-banner", body_start)
        h1_pos = html.index("<h1", body_start)
        assert banner_pos < h1_pos
        # Review CSS was injected (inside <style>)
        assert ".review-panel" in html
        # Review JS functions are present (inside <script>)
        assert "function saveClassification" in html
        assert "function saveGTCorrection" in html
        assert "function rejudge" in html
        # Embedded report data includes our fixture questions
        assert '"question_id": "1"' in html or '"question_id":"1"' in html


class TestGenerateReviewHTML:
    def test_static_report_unchanged(self, run_dir: Path) -> None:
        """generate_html_report still produces static reports without review UI."""
        from html_report import generate_html_report

        html_path = generate_html_report(run_dir)
        html = html_path.read_text()
        assert "Review Mode" not in html
        assert "review-panel" not in html
        assert "saveClassification" not in html
        # But it still has the core report elements
        assert "Ask Atlas" in html
        assert "REPORT" in html
