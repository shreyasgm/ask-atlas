"""Eval-based integration tests: real LLM + production DB + LLM-as-judge.

Runs a small subset of evaluation questions (from ``evaluation/questions/``)
through the full agent pipeline and scores the answers with the eval judge.
These give a fast signal on answer quality during CI.

Requires:
    - ``ATLAS_DB_URL`` pointing at the production Atlas database
    - LLM API keys configured in ``.env``

Run only these tests::

    PYTHONPATH=$(pwd) uv run pytest -m "eval" -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from evaluation.judge import judge_answer
from src.text_to_sql import AtlasTextToSQL

pytestmark = [pytest.mark.eval, pytest.mark.asyncio(loop_scope="module")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_question(base_dir: Path, question_id: int) -> dict:
    """Load a question definition from the evaluation corpus."""
    path = base_dir / "evaluation" / "questions" / str(question_id) / "question.json"
    return json.loads(path.read_text())


def _load_ground_truth(base_dir: Path, question_id: int) -> list[dict] | None:
    """Load ground truth results for a question, or ``None`` if unavailable."""
    path = (
        base_dir
        / "evaluation"
        / "results"
        / str(question_id)
        / "ground_truth"
        / "results.json"
    )
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("results", {}).get("data")


# ---------------------------------------------------------------------------
# Shared agent fixture (expensive — created once per module)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def atlas_agent(base_dir: Path):
    """Create a single shared AtlasTextToSQL instance for all eval tests.

    Skips the entire module if the production database is not configured.
    """
    from src.config import get_settings

    settings = get_settings()
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured — skipping eval tests")

    async with await AtlasTextToSQL.create_async() as agent:
        yield agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_eval_nigeria_crude_oil_exports_2020(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Question 2: crude oil export value for Nigeria in 2020 (ground truth comparison).

    Single product (HS 2709), single country, single year — straightforward
    goods query with no goods/services ambiguity.
    """
    question_data = _load_question(base_dir, 2)
    ground_truth = _load_ground_truth(base_dir, 2)
    assert ground_truth is not None, "Ground truth missing for question 2"

    result = await atlas_agent.aanswer_question(question_data["user_question"])
    answer = result.answer
    assert answer, "Agent returned an empty answer"

    verdict = await judge_answer(
        question=question_data["user_question"],
        agent_answer=answer,
        ground_truth_data=ground_truth,
    )

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_out_of_scope_refusal(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Question 53: 'What is the capital of France?' — should refuse gracefully."""
    question_data = _load_question(base_dir, 53)
    expected_behavior = question_data.get("expected_behavior")
    assert expected_behavior, "expected_behavior missing for question 53"

    result = await atlas_agent.aanswer_question(question_data["user_question"])
    answer = result.answer
    assert answer, "Agent returned an empty answer"

    verdict = await judge_answer(
        question=question_data["user_question"],
        agent_answer=answer,
        ground_truth_data=None,
        expected_behavior=expected_behavior,
    )

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(score={verdict['score']}): {verdict.get('reasoning', '')}"
    )
