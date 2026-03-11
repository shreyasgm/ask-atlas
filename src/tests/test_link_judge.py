"""Tests for the link quality judge (evaluation/link_judge.py).

Covers:
- LinkVerdict schema: pass count calculation, verdict thresholds, to_dict()
- Prompt template structure
- judge_links() function with mocked LLM
- Dimension weight correctness (kept for backward compat)
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
    LinkDimensionPass,
    LinkVerdict,
    judge_links,
)

# ---------------------------------------------------------------------------
# Dimension weights (kept for backward compat with old report parsing)
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
# LinkVerdict schema (binary pass/fail)
# ---------------------------------------------------------------------------


def _make_verdict(
    presence: bool = True,
    relevance: bool = True,
    entity: bool = True,
    params: bool = True,
) -> LinkVerdict:
    return LinkVerdict(
        link_presence=LinkDimensionPass(passed=presence, reasoning="ok"),
        content_relevance=LinkDimensionPass(passed=relevance, reasoning="ok"),
        entity_correctness=LinkDimensionPass(passed=entity, reasoning="ok"),
        parameter_accuracy=LinkDimensionPass(passed=params, reasoning="ok"),
        overall_comment="test",
    )


class TestLinkVerdictSchema:
    def test_all_pass(self):
        v = _make_verdict(True, True, True, True)
        assert v.pass_count == 4
        assert v.verdict == "pass"

    def test_all_fail(self):
        v = _make_verdict(False, False, False, False)
        assert v.pass_count == 0
        assert v.verdict == "fail"

    def test_three_pass_is_pass(self):
        """3 passing dimensions = pass (if link_presence and content_relevance pass)."""
        v = _make_verdict(True, True, True, False)
        assert v.pass_count == 3
        assert v.verdict == "pass"

    def test_three_pass_capped_if_link_presence_fails(self):
        """3 pass but link_presence fails → capped at partial."""
        v = _make_verdict(False, True, True, True)
        assert v.pass_count == 3
        assert v.verdict == "partial"

    def test_three_pass_capped_if_content_relevance_fails(self):
        """3 pass but content_relevance fails → capped at partial."""
        v = _make_verdict(True, False, True, True)
        assert v.pass_count == 3
        assert v.verdict == "partial"

    def test_two_pass_is_partial(self):
        v = _make_verdict(True, True, False, False)
        assert v.pass_count == 2
        assert v.verdict == "partial"

    def test_one_pass_is_fail(self):
        v = _make_verdict(True, False, False, False)
        assert v.pass_count == 1
        assert v.verdict == "fail"

    def test_to_dict_structure(self):
        v = _make_verdict(True, False, True, False)
        d = v.to_dict()
        assert "pass_count" in d
        assert "weighted_score" in d  # backward compat
        assert d["pass_count"] == 2
        assert d["weighted_score"] == 2.0  # backward compat: float(pass_count)
        for dim in [
            "link_presence",
            "content_relevance",
            "entity_correctness",
            "parameter_accuracy",
        ]:
            assert "passed" in d[dim]
            assert "reasoning" in d[dim]

    def test_to_dict_verdict(self):
        v = _make_verdict(True, True, True, True)
        d = v.to_dict()
        assert d["verdict"] == "pass"
        assert d["pass_count"] == 4


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
    "link_presence": {"passed": True, "reasoning": "Link exists"},
    "content_relevance": {"passed": True, "reasoning": "Shows relevant data"},
    "entity_correctness": {"passed": True, "reasoning": "Correct country"},
    "parameter_accuracy": {"passed": False, "reasoning": "Year off by 3"},
    "pass_count": 3,
    "weighted_score": 3.0,
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
        assert result["pass_count"] == 3
        assert "link_presence" in result
        assert "content_relevance" in result

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
                model="gpt-5.4",
                provider="openai",
            )

        # Verify ainvoke was called with the right keys
        call_args = mock_chain.ainvoke.call_args[0][0]
        assert "question" in call_args
        assert "agent_links" in call_args
        assert "ground_truth_url" in call_args
        assert "Spain" in call_args["question"]
