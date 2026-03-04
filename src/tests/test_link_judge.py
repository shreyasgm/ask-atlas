"""Tests for the link quality judge (evaluation/link_judge.py).

Covers:
- LinkVerdict schema: weighted score calculation, verdict thresholds, to_dict()
- Prompt template structure
- judge_links() function with mocked LLM
- Dimension weight correctness
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.prompts import ChatPromptTemplate

# link_judge.py lives in evaluation/, not src/
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

from link_judge import (
    LINK_DIMENSION_WEIGHTS,
    LinkDimensionScore,
    LinkVerdict,
    _LINK_JUDGE_PROMPT,
    judge_links,
)

# ---------------------------------------------------------------------------
# Dimension weights
# ---------------------------------------------------------------------------


class TestDimensionWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(LINK_DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9

    def test_link_presence_is_heaviest(self):
        assert LINK_DIMENSION_WEIGHTS["link_presence"] == max(
            LINK_DIMENSION_WEIGHTS.values()
        )

    def test_parameter_accuracy_is_lightest(self):
        assert LINK_DIMENSION_WEIGHTS["parameter_accuracy"] == min(
            LINK_DIMENSION_WEIGHTS.values()
        )


# ---------------------------------------------------------------------------
# LinkVerdict schema
# ---------------------------------------------------------------------------


def _make_verdict(
    presence: int = 5,
    relevance: int = 5,
    entity: int = 5,
    params: int = 5,
) -> LinkVerdict:
    return LinkVerdict(
        link_presence=LinkDimensionScore(score=presence, reasoning="ok"),
        content_relevance=LinkDimensionScore(score=relevance, reasoning="ok"),
        entity_correctness=LinkDimensionScore(score=entity, reasoning="ok"),
        parameter_accuracy=LinkDimensionScore(score=params, reasoning="ok"),
        overall_comment="test",
    )


class TestLinkVerdictSchema:
    def test_perfect_score(self):
        v = _make_verdict(5, 5, 5, 5)
        assert v.weighted_score == 5.0
        assert v.verdict == "pass"

    def test_lowest_score(self):
        v = _make_verdict(1, 1, 1, 1)
        assert v.weighted_score == 1.0
        assert v.verdict == "fail"

    def test_pass_threshold(self):
        """Score exactly at 3.5 should be pass."""
        # 0.35*4 + 0.30*3 + 0.25*3 + 0.10*5 = 1.4+0.9+0.75+0.5 = 3.55
        v = _make_verdict(4, 3, 3, 5)
        assert v.weighted_score >= 3.5
        assert v.verdict == "pass"

    def test_partial_threshold(self):
        """Score in [2.5, 3.5) should be partial."""
        # 0.35*3 + 0.30*2 + 0.25*2 + 0.10*3 = 1.05+0.6+0.5+0.3 = 2.45 -> fail
        # Let's find partial: 0.35*3 + 0.30*3 + 0.25*2 + 0.10*3 = 1.05+0.9+0.5+0.3 = 2.75
        v = _make_verdict(3, 3, 2, 3)
        assert 2.5 <= v.weighted_score < 3.5
        assert v.verdict == "partial"

    def test_fail_threshold(self):
        """Score below 2.5 should be fail."""
        v = _make_verdict(2, 2, 2, 2)
        assert v.weighted_score < 2.5
        assert v.verdict == "fail"

    def test_weighted_score_calculation(self):
        v = _make_verdict(4, 3, 5, 2)
        expected = 4 * 0.35 + 3 * 0.30 + 5 * 0.25 + 2 * 0.10
        assert abs(v.weighted_score - expected) < 1e-9

    def test_to_dict_structure(self):
        v = _make_verdict(4, 3, 5, 2)
        d = v.to_dict()
        assert set(d.keys()) == {
            "link_presence",
            "content_relevance",
            "entity_correctness",
            "parameter_accuracy",
            "weighted_score",
            "verdict",
            "overall_comment",
        }
        for dim in [
            "link_presence",
            "content_relevance",
            "entity_correctness",
            "parameter_accuracy",
        ]:
            assert "score" in d[dim]
            assert "reasoning" in d[dim]

    def test_to_dict_weighted_score_rounded(self):
        v = _make_verdict(4, 3, 5, 2)
        d = v.to_dict()
        assert d["weighted_score"] == round(v.weighted_score, 3)


# ---------------------------------------------------------------------------
# Prompt template structure
# ---------------------------------------------------------------------------


class TestPromptStructure:
    def test_has_system_and_human_messages(self):
        assert len(_LINK_JUDGE_PROMPT.messages) == 2

    def test_system_prompt_contains_atlas_reference(self):
        system_text = _LINK_JUDGE_PROMPT.messages[0].prompt.template
        assert "atlas.hks.harvard.edu" in system_text

    def test_system_prompt_contains_country_pages(self):
        system_text = _LINK_JUDGE_PROMPT.messages[0].prompt.template
        assert "/countries/" in system_text

    def test_system_prompt_contains_explore_pages(self):
        system_text = _LINK_JUDGE_PROMPT.messages[0].prompt.template
        assert "/explore/" in system_text

    def test_human_prompt_has_required_variables(self):
        human_text = _LINK_JUDGE_PROMPT.messages[1].prompt.template
        assert "{question}" in human_text
        assert "{agent_links}" in human_text
        assert "{ground_truth_url}" in human_text


# ---------------------------------------------------------------------------
# judge_links() with mocked LLM
# ---------------------------------------------------------------------------


def _make_mock_chain(verdict_dict: dict) -> AsyncMock:
    """Build a mock chain whose ainvoke returns a LinkVerdict-like object."""
    mock_verdict = MagicMock()
    mock_verdict.to_dict.return_value = verdict_dict.copy()
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_verdict)
    return mock_chain


_LINK_VERDICT_DICT = {
    "link_presence": {"score": 5, "reasoning": "Link exists"},
    "content_relevance": {"score": 4, "reasoning": "Shows relevant data"},
    "entity_correctness": {"score": 5, "reasoning": "Correct country"},
    "parameter_accuracy": {"score": 3, "reasoning": "Year off by 1"},
    "weighted_score": 4.55,
    "verdict": "pass",
    "overall_comment": "Good link",
}


class TestJudgeLinks:
    async def _call_judge(self, **kwargs) -> dict:
        mock_chain = _make_mock_chain(_LINK_VERDICT_DICT)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()
        with (
            patch("link_judge.create_llm", return_value=mock_llm),
            patch.object(ChatPromptTemplate, "__or__", return_value=mock_chain),
        ):
            return await judge_links(**kwargs)

    @pytest.mark.asyncio
    async def test_returns_verdict_dict(self):
        result = await self._call_judge(
            question="What does Spain export?",
            agent_links=[
                {"url": "https://atlas.hks.harvard.edu/countries/724/export-basket"}
            ],
            ground_truth_url="https://atlas.hks.harvard.edu/countries/724/export-basket",
        )
        assert result["verdict"] == "pass"
        assert result["weighted_score"] == 4.55
        assert "link_presence" in result
        assert "content_relevance" in result

    @pytest.mark.asyncio
    async def test_handles_no_ground_truth_url(self):
        result = await self._call_judge(
            question="What does Kenya export?",
            agent_links=[
                {"url": "https://atlas.hks.harvard.edu/countries/404/export-basket"}
            ],
            ground_truth_url=None,
        )
        assert "verdict" in result

    @pytest.mark.asyncio
    async def test_handles_empty_agent_links(self):
        result = await self._call_judge(
            question="What is Spain's ECI?",
            agent_links=[],
            ground_truth_url="https://atlas.hks.harvard.edu/countries/724/export-complexity",
        )
        assert "verdict" in result

    @pytest.mark.asyncio
    async def test_passes_correct_args_to_chain(self):
        mock_chain = _make_mock_chain(_LINK_VERDICT_DICT)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()

        with (
            patch("link_judge.create_llm", return_value=mock_llm),
            patch.object(ChatPromptTemplate, "__or__", return_value=mock_chain),
        ):
            await judge_links(
                question="What does Spain export?",
                agent_links=[{"url": "https://atlas.hks.harvard.edu/countries/724"}],
                ground_truth_url="https://atlas.hks.harvard.edu/countries/724/export-basket",
                model="gpt-5-mini",
                provider="openai",
            )

        # Verify ainvoke was called with the right keys
        call_args = mock_chain.ainvoke.call_args[0][0]
        assert "question" in call_args
        assert "agent_links" in call_args
        assert "ground_truth_url" in call_args
        assert "Spain" in call_args["question"]
