"""Tests for the year-gap caveat logic in evaluation/judge.py.

Verifies:
- _YEAR_GAP_CAVEAT contains required business-rule language
- judge_answer tools_used parameter has correct default
- Caveat flag is conditionally applied based on tools_used and judge path
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.prompts import ChatPromptTemplate

# judge.py lives in evaluation/, not src/, so adjust the import path
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evaluation"))

from judge import (
    _GROUND_TRUTH_PROMPT,
    _GROUND_TRUTH_PROMPT_WITH_YEAR_GAP,
    _YEAR_GAP_CAVEAT,
    DimensionPass,
    JudgeVerdict,
    PlausibilityVerdict,
    RefusalVerdict,
    WebResearchVerdict,
    judge_answer,
)

# ---------------------------------------------------------------------------
# Caveat content: business-rule assertions
# ---------------------------------------------------------------------------


class TestYearGapCaveatContent:
    def test_contains_pass_guidance(self):
        """Caveat must instruct the judge to give data_accuracy a PASS for plausible older data."""
        assert "give data_accuracy a PASS" in _YEAR_GAP_CAVEAT

    def test_preserves_strictness_for_other_errors(self):
        """Caveat must NOT grant blanket leniency — wrong data should still be penalised."""
        assert "wrong countries/products/metrics" in _YEAR_GAP_CAVEAT
        assert "fabricated" in _YEAR_GAP_CAVEAT

    def test_scoped_to_post_sql_years_only(self):
        """Questions about years within SQL coverage should be scored normally."""
        assert "score normally" in _YEAR_GAP_CAVEAT

    def test_covers_implicit_latest_year(self):
        """Questions that omit a year (implying 'latest') must be treated as gap-relevant."""
        assert "does not mention a year" in _YEAR_GAP_CAVEAT


# ---------------------------------------------------------------------------
# Signature: backwards compatibility
# ---------------------------------------------------------------------------


class TestJudgeAnswerSignature:
    def test_tools_used_defaults_to_none(self):
        """Existing callers that don't pass tools_used must not break."""
        sig = inspect.signature(judge_answer)
        param = sig.parameters["tools_used"]
        assert param.default is None


# ---------------------------------------------------------------------------
# Prompt template structure
# ---------------------------------------------------------------------------


class TestPromptTemplateStructure:
    def test_base_prompt_does_not_contain_caveat(self):
        """The base prompt should NOT accidentally include caveat text."""
        system_text = _GROUND_TRUTH_PROMPT.messages[0].prompt.template
        assert "SQL database" not in system_text

    def test_both_prompts_have_same_human_message(self):
        """Refactoring into two prompts must not change the human message."""
        base_human = _GROUND_TRUTH_PROMPT.messages[1].prompt.template
        gap_human = _GROUND_TRUTH_PROMPT_WITH_YEAR_GAP.messages[1].prompt.template
        assert base_human == gap_human


# ---------------------------------------------------------------------------
# Integration: caveat flag in judge_answer output
# ---------------------------------------------------------------------------


def _make_mock_chain(verdict_dict: dict) -> AsyncMock:
    """Build a mock chain whose ainvoke returns a verdict with .to_dict()."""
    mock_verdict = MagicMock()
    mock_verdict.to_dict.return_value = verdict_dict.copy()
    mock_chain = AsyncMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_verdict)
    return mock_chain


_GROUND_TRUTH_VERDICT = {
    "judge_mode": "ground_truth",
    "factual_correctness": {"passed": True, "reasoning": "ok"},
    "data_accuracy": {"passed": True, "reasoning": "ok"},
    "completeness": {"passed": True, "reasoning": "ok"},
    "reasoning_quality": {"passed": True, "reasoning": "ok"},
    "pass_count": 4,
    "weighted_score": 4.0,
    "verdict": "pass",
    "overall_comment": "Good",
}

_REFUSAL_VERDICT = {
    "judge_mode": "refusal",
    "appropriate_refusal": True,
    "graceful": True,
    "pass_count": 4,
    "weighted_score": 4.0,
    "verdict": "pass",
    "reasoning": "Good refusal",
}

_PLAUSIBILITY_VERDICT = {
    "judge_mode": "plausibility",
    "plausible": True,
    "factually_absurd": False,
    "pass_count": 4,
    "weighted_score": 4.0,
    "verdict": "pass",
    "reasoning": "Plausible",
    "note": "No ground truth SQL — scored on plausibility only, not verified accuracy.",
}


class TestYearGapCaveatApplication:
    """Verify caveat flag is applied conditionally based on tools_used and judge path."""

    async def _call_judge(self, **kwargs) -> dict:
        """Call judge_answer with mocked LLM, returning the verdict dict."""
        verdict_dict = kwargs.pop("_verdict", _GROUND_TRUTH_VERDICT)
        mock_chain = _make_mock_chain(verdict_dict)
        mock_llm = MagicMock()
        mock_llm.with_structured_output.return_value = MagicMock()
        with (
            patch("judge.create_llm", return_value=mock_llm),
            patch.object(ChatPromptTemplate, "__or__", return_value=mock_chain),
        ):
            return await judge_answer(**kwargs)

    # -- Ground truth path: caveat applied --

    @pytest.mark.asyncio
    async def test_caveat_applied_when_query_tool_used(self):
        result = await self._call_judge(
            question="test",
            agent_answer="answer",
            ground_truth_data=[{"col": "val"}],
            tools_used=["query_tool"],
        )
        assert result.get("year_gap_caveat_applied") is True

    @pytest.mark.asyncio
    async def test_caveat_applied_when_both_tools_used(self):
        """Caveat triggers if query_tool appears anywhere in the list."""
        result = await self._call_judge(
            question="test",
            agent_answer="answer",
            ground_truth_data=[{"col": "val"}],
            tools_used=["atlas_graphql", "query_tool"],
        )
        assert result.get("year_gap_caveat_applied") is True

    # -- Ground truth path: caveat NOT applied --

    @pytest.mark.asyncio
    async def test_caveat_not_applied_when_no_tools_used(self):
        result = await self._call_judge(
            question="test",
            agent_answer="answer",
            ground_truth_data=[{"col": "val"}],
            tools_used=None,
        )
        assert "year_gap_caveat_applied" not in result

    @pytest.mark.asyncio
    async def test_caveat_not_applied_when_only_graphql_used(self):
        result = await self._call_judge(
            question="test",
            agent_answer="answer",
            ground_truth_data=[{"col": "val"}],
            tools_used=["atlas_graphql"],
        )
        assert "year_gap_caveat_applied" not in result

    @pytest.mark.asyncio
    async def test_caveat_not_applied_with_empty_tools_list(self):
        """Empty list should behave like None — no SQL was used."""
        result = await self._call_judge(
            question="test",
            agent_answer="answer",
            ground_truth_data=[{"col": "val"}],
            tools_used=[],
        )
        assert "year_gap_caveat_applied" not in result

    # -- Non-ground-truth paths: caveat never applies --

    @pytest.mark.asyncio
    async def test_caveat_not_applied_for_refusal_path(self):
        """Refusal path should never get the caveat, even with query_tool."""
        result = await self._call_judge(
            _verdict=_REFUSAL_VERDICT,
            question="test",
            agent_answer="answer",
            ground_truth_data=None,
            expected_behavior="Should refuse",
            tools_used=["query_tool"],
        )
        assert "year_gap_caveat_applied" not in result

    @pytest.mark.asyncio
    async def test_caveat_not_applied_for_plausibility_path(self):
        """Plausibility path should never get the caveat, even with query_tool."""
        result = await self._call_judge(
            _verdict=_PLAUSIBILITY_VERDICT,
            question="test",
            agent_answer="answer",
            ground_truth_data=None,
            expected_behavior=None,
            tools_used=["query_tool"],
        )
        assert "year_gap_caveat_applied" not in result


# ---------------------------------------------------------------------------
# Binary scoring: verdict logic tests
# ---------------------------------------------------------------------------


def _make_dim(passed: bool) -> DimensionPass:
    return DimensionPass(passed=passed, reasoning="test")


class TestBinaryScoringVerdict:
    """Test pass_count and verdict thresholds with critical-dimension caps."""

    def test_all_pass(self):
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.pass_count == 4
        assert v.verdict == "pass"

    def test_fc_fail_caps_at_partial(self):
        """FC fails, 3 others pass → pass_count=3, but capped at partial."""
        v = JudgeVerdict(
            factual_correctness=_make_dim(False),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.pass_count == 3
        assert v.verdict == "partial"

    def test_da_fail_caps_at_partial(self):
        """DA fails, 3 others pass → pass_count=3, but capped at partial."""
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(False),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.pass_count == 3
        assert v.verdict == "partial"

    def test_no_cap_when_non_critical_fails(self):
        """Completeness fails, FC+DA+RQ pass → pass_count=3, verdict=pass (no cap)."""
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(False),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.pass_count == 3
        assert v.verdict == "pass"

    def test_pass_count_2_is_partial(self):
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(False),
            reasoning_quality=_make_dim(False),
            overall_comment="ok",
        )
        assert v.pass_count == 2
        assert v.verdict == "partial"

    def test_pass_count_1_is_fail(self):
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(False),
            completeness=_make_dim(False),
            reasoning_quality=_make_dim(False),
            overall_comment="ok",
        )
        assert v.pass_count == 1
        assert v.verdict == "fail"

    def test_pass_count_0_is_fail(self):
        v = JudgeVerdict(
            factual_correctness=_make_dim(False),
            data_accuracy=_make_dim(False),
            completeness=_make_dim(False),
            reasoning_quality=_make_dim(False),
            overall_comment="ok",
        )
        assert v.pass_count == 0
        assert v.verdict == "fail"

    def test_to_dict_has_pass_count_and_backward_compat(self):
        v = JudgeVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(False),
            overall_comment="ok",
        )
        d = v.to_dict()
        assert d["pass_count"] == 3
        assert d["weighted_score"] == 3.0
        assert d["factual_correctness"]["passed"] is True
        assert "score" not in d["factual_correctness"]


class TestRefusalBinaryVerdict:
    def test_pass_maps_to_4(self):
        v = RefusalVerdict(appropriate_refusal=True, graceful=True, reasoning="ok")
        assert v.verdict == "pass"
        d = v.to_dict()
        assert d["pass_count"] == 4
        assert d["weighted_score"] == 4.0

    def test_fail_maps_to_0(self):
        v = RefusalVerdict(appropriate_refusal=False, graceful=True, reasoning="bad")
        assert v.verdict == "fail"
        d = v.to_dict()
        assert d["pass_count"] == 0
        assert d["weighted_score"] == 0.0


class TestPlausibilityBinaryVerdict:
    def test_pass_maps_to_4(self):
        v = PlausibilityVerdict(plausible=True, factually_absurd=False, reasoning="ok")
        assert v.verdict == "pass"
        d = v.to_dict()
        assert d["pass_count"] == 4
        assert d["weighted_score"] == 4.0

    def test_fail_maps_to_0(self):
        v = PlausibilityVerdict(plausible=False, factually_absurd=True, reasoning="bad")
        assert v.verdict == "fail"
        d = v.to_dict()
        assert d["pass_count"] == 0

    def test_absurd_overrides_plausible(self):
        v = PlausibilityVerdict(
            plausible=True, factually_absurd=True, reasoning="contradiction"
        )
        assert v.verdict == "fail"


class TestWebResearchBinaryVerdict:
    def test_all_pass(self):
        v = WebResearchVerdict(
            factual_correctness=_make_dim(True),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.pass_count == 4
        assert v.verdict == "pass"
        d = v.to_dict()
        assert d["pass_count"] == 4
        assert d["judge_mode"] == "web_research"

    def test_fc_fail_caps_at_partial(self):
        v = WebResearchVerdict(
            factual_correctness=_make_dim(False),
            data_accuracy=_make_dim(True),
            completeness=_make_dim(True),
            reasoning_quality=_make_dim(True),
            overall_comment="ok",
        )
        assert v.verdict == "partial"
