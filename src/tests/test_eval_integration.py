"""Eval-based integration tests: real LLM + production DB + LLM-as-judge.

Runs a small subset of evaluation questions (from ``evaluation/questions/``)
through the full agent pipeline and scores the answers with the eval judge.
These give a fast signal on answer quality during CI.

Requires:
    - ``ATLAS_DB_URL`` pointing at the production Atlas database
    - LLM API keys configured in ``.env``

Run only these tests::

    PYTHONPATH=$(pwd) uv run pytest -m "eval" -v

Coverage:
    - Q2:   easy   | Total Export Values         | ground_truth  | single product, single year
    - Q53:  easy   | Out-of-Scope Refusals       | refusal       | general knowledge question
    - Q6:   easy   | Sectoral Export Composition  | ground_truth  | top-N products
    - Q25:  easy   | Growth and Performance       | ground_truth  | percentage change over time
    - Q61:  easy   | Country Profile Overview     | ground_truth  | GDP/population from country page
    - Q97:  medium | Growth Perf (Country Page)   | ground_truth  | ECI value from chart
    - Q170: easy   | Product Complexity (Explore) | ground_truth  | RCA for a product
    - Q195: easy   | Bilateral Trade (Explore)    | ground_truth  | bilateral export value
    - Q206: hard   | Bilateral Trade (Explore)    | ground_truth  | top-3 products, multi-row
    - Q57:  medium | Data Availability Boundaries | refusal       | missing data type
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
    eval_questions_path = base_dir / "evaluation" / "eval_questions.json"
    data = json.loads(eval_questions_path.read_text())
    categories = {cat["id"]: cat["name"] for cat in data["categories"]}
    for q in data["questions"]:
        if q["id"] == question_id:
            return {
                "question_id": str(question_id),
                "user_question": q["text"],
                "category": categories.get(q["category_id"], q["category_id"]),
                "difficulty": q["difficulty"],
                "expected_behavior": q.get("expected_behavior"),
            }
    raise ValueError(f"Question {question_id} not found in eval_questions.json")


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


async def _run_and_judge(
    atlas_agent: AtlasTextToSQL,
    base_dir: Path,
    question_id: int,
    *,
    expect_refusal: bool = False,
) -> dict:
    """Run agent on a question and return the judge verdict.

    Args:
        atlas_agent: Shared agent instance.
        base_dir: Project root.
        question_id: Numeric question ID.
        expect_refusal: If True, use expected_behavior from the question.

    Returns:
        Judge verdict dict.
    """
    question_data = _load_question(base_dir, question_id)

    result = await atlas_agent.aanswer_question(question_data["user_question"])
    answer = result.answer
    assert answer, f"Agent returned an empty answer for Q{question_id}"

    ground_truth = None if expect_refusal else _load_ground_truth(base_dir, question_id)
    expected_behavior = (
        question_data.get("expected_behavior") if expect_refusal else None
    )

    verdict = await judge_answer(
        question=question_data["user_question"],
        agent_answer=answer,
        ground_truth_data=ground_truth,
        expected_behavior=expected_behavior,
    )
    return verdict


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
# Ground-truth tests (4-dimension rubric)
# ---------------------------------------------------------------------------


async def test_eval_nigeria_crude_oil_exports_2020(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q2: crude oil export value for Nigeria in 2020.

    Single product (HS 2709), single country, single year — straightforward
    goods query with no goods/services ambiguity.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 2)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_india_top3_exports_2020(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q6: top 3 exported products from India in 2020.

    Tests sectoral composition — requires ranking and returning multiple products.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 6)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_spain_export_growth_2016_2020(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q25: percentage change in total export value for Spain between 2016 and 2020.

    Tests growth/performance category — requires computing a percentage change
    across two years.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 25)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_kenya_gdp_per_capita(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Q61: GDP per capita of Kenya.

    Tests country profile overview — data from the Atlas country page,
    not from SQL queries.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 61)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_spain_eci_value(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Q97: Spain's ECI value from the growth dynamics chart (medium difficulty).

    Tests country page growth performance — requires finding a specific
    complexity metric from a visualization.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 97)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_kenya_coffee_rca(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Q170: Kenya's RCA in Coffee (explore page product complexity).

    Tests explore page data — Revealed Comparative Advantage for a specific
    product in a specific country.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 170)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_brazil_china_bilateral_exports(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q195: total export value from Brazil to China (explore page bilateral trade).

    Tests bilateral trade — requires filtering by both exporter and importer.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 195)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


async def test_eval_germany_usa_top3_products(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q206: top 3 products Germany exports to USA (hard, multi-row result).

    Tests hard bilateral trade question — requires bilateral filter + product
    ranking + returning multiple rows.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 206)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(weighted_score={verdict['weighted_score']}): {verdict.get('overall_comment', '')}"
    )


# ---------------------------------------------------------------------------
# Refusal / edge-case tests
# ---------------------------------------------------------------------------


async def test_eval_out_of_scope_refusal(base_dir: Path, atlas_agent: AtlasTextToSQL):
    """Q53: 'What is the capital of France?' — should refuse gracefully."""
    verdict = await _run_and_judge(atlas_agent, base_dir, 53, expect_refusal=True)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(score={verdict['score']}): {verdict.get('reasoning', '')}"
    )


async def test_eval_data_boundary_bilateral_services(
    base_dir: Path, atlas_agent: AtlasTextToSQL
):
    """Q57: bilateral services trade (not available) — should explain limitation.

    Tests data boundary handling — the system should acknowledge that bilateral
    services data is not in the database.
    """
    verdict = await _run_and_judge(atlas_agent, base_dir, 57, expect_refusal=True)

    assert verdict["verdict"] in ("pass", "partial"), (
        f"Expected pass/partial, got {verdict['verdict']} "
        f"(score={verdict['score']}): {verdict.get('reasoning', '')}"
    )
