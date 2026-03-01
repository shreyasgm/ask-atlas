#!/usr/bin/env python3
"""LLM-as-judge: evaluate agent answers against ground truth data.

Three evaluation paths:

1. **Ground truth** — agent's text answer is scored against verified data rows
   from executed SQL (4-dimension rubric, 1-5 each).
2. **Refusal** — for out-of-scope / data-boundary questions with an
   ``expected_behavior`` field, checks whether the agent refused appropriately.
3. **Plausibility** — for questions without ground truth SQL *and* without an
   expected_behavior field, the judge uses its own knowledge to assess whether
   the answer is roughly plausible (not fabricated nonsense).  These results
   are flagged as ``"judge_mode": "plausibility"`` so they're clearly
   distinguished from verified evaluations.
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.config import create_llm

# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

DIMENSION_WEIGHTS = {
    "factual_correctness": 0.35,
    "data_accuracy": 0.30,
    "completeness": 0.20,
    "reasoning_quality": 0.15,
}


class DimensionScore(BaseModel):
    score: int = Field(..., ge=1, le=5, description="Score from 1 (worst) to 5 (best)")
    reasoning: str = Field(..., description="Brief justification for this score")


class JudgeVerdict(BaseModel):
    """Structured verdict from the LLM judge."""

    factual_correctness: DimensionScore = Field(
        ..., description="Are specific numbers, countries, products correct?"
    )
    data_accuracy: DimensionScore = Field(
        ..., description="Do reported numbers match ground truth (within rounding)?"
    )
    completeness: DimensionScore = Field(
        ..., description="Does the answer address all parts of the question?"
    )
    reasoning_quality: DimensionScore = Field(
        ..., description="Is the interpretation and analysis sound?"
    )
    overall_comment: str = Field(
        ..., description="One-sentence summary of the evaluation"
    )

    @property
    def weighted_score(self) -> float:
        scores = {
            "factual_correctness": self.factual_correctness.score,
            "data_accuracy": self.data_accuracy.score,
            "completeness": self.completeness.score,
            "reasoning_quality": self.reasoning_quality.score,
        }
        return sum(scores[k] * DIMENSION_WEIGHTS[k] for k in DIMENSION_WEIGHTS)

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        ws = self.weighted_score
        if ws >= 3.5:
            return "pass"
        if ws >= 2.5:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        return {
            "judge_mode": "ground_truth",
            "factual_correctness": {
                "score": self.factual_correctness.score,
                "reasoning": self.factual_correctness.reasoning,
            },
            "data_accuracy": {
                "score": self.data_accuracy.score,
                "reasoning": self.data_accuracy.reasoning,
            },
            "completeness": {
                "score": self.completeness.score,
                "reasoning": self.completeness.reasoning,
            },
            "reasoning_quality": {
                "score": self.reasoning_quality.score,
                "reasoning": self.reasoning_quality.reasoning,
            },
            "weighted_score": round(self.weighted_score, 3),
            "verdict": self.verdict,
            "overall_comment": self.overall_comment,
        }


class RefusalVerdict(BaseModel):
    """Verdict for questions that expect a refusal or edge-case handling."""

    appropriate_refusal: bool = Field(
        ..., description="Did the agent appropriately refuse or flag the limitation?"
    )
    graceful: bool = Field(..., description="Was the response polite and informative?")
    score: int = Field(..., ge=1, le=5, description="Overall appropriateness score 1-5")
    reasoning: str = Field(..., description="Justification")

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        if self.score >= 4:
            return "pass"
        if self.score >= 3:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        return {
            "judge_mode": "refusal",
            "appropriate_refusal": self.appropriate_refusal,
            "graceful": self.graceful,
            "score": self.score,
            "weighted_score": float(self.score),
            "verdict": self.verdict,
            "reasoning": self.reasoning,
        }


class PlausibilityVerdict(BaseModel):
    """Verdict for questions without ground truth SQL — rough plausibility check.

    The judge uses its own world knowledge to assess whether the agent's
    answer is broadly reasonable, not whether it is precisely correct.
    """

    plausible: bool = Field(
        ...,
        description="Is the answer broadly plausible based on real-world knowledge?",
    )
    factually_absurd: bool = Field(
        ...,
        description="Does the answer contain obviously wrong claims (e.g. wrong order of magnitude, impossible values)?",
    )
    score: int = Field(
        ...,
        ge=1,
        le=5,
        description=(
            "Plausibility score 1-5: "
            "1=nonsense/fabricated, 2=major issues, 3=somewhat plausible, "
            "4=mostly plausible, 5=highly plausible"
        ),
    )
    reasoning: str = Field(..., description="Justification for the score")

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        if self.score >= 4:
            return "pass"
        if self.score >= 3:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        return {
            "judge_mode": "plausibility",
            "plausible": self.plausible,
            "factually_absurd": self.factually_absurd,
            "score": self.score,
            "weighted_score": float(self.score),
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "note": "No ground truth SQL — scored on plausibility only, not verified accuracy.",
        }


# ---------------------------------------------------------------------------
# Data-year coverage constants (mirror src/prompts.py values)
# ---------------------------------------------------------------------------

_SQL_DATA_MAX_YEAR: int = 2022
_GRAPHQL_DATA_MAX_YEAR: int = 2024

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GROUND_TRUTH_SYSTEM_TEXT = (
    "You are an expert evaluator for a trade-data Q&A system. "
    "Compare the agent's answer to the ground truth data and score it.\n\n"
    "Scoring rubric (1-5 each):\n"
    "- **Factual Correctness** (weight 0.35): Are specific numbers, countries, "
    "products, and years correct?\n"
    "- **Data Accuracy** (weight 0.30): Do numbers match ground truth within "
    "reasonable rounding (±2%)?  Penalise fabricated numbers.\n"
    "- **Completeness** (weight 0.20): Does the answer address all parts of "
    "the question?\n"
    "- **Reasoning Quality** (weight 0.15): Is the interpretation, analysis, "
    "and contextual explanation sound?\n\n"
    "Be strict on factual accuracy; be lenient on rounding differences."
)

_GROUND_TRUTH_HUMAN_TEXT = (
    "**Question**: {question}\n\n"
    "**Ground truth data** (from verified SQL):\n```json\n{ground_truth}\n```\n\n"
    "**Agent answer**:\n{agent_answer}\n\n"
    "Evaluate the agent's answer against the ground truth."
)

_KNOWN_DATA_CAVEATS = (
    "\n\nKnown data caveats to consider when scoring:\n"
    "- RCA>1 product count: The Atlas browser Product Space visualization displays "
    "a filtered count (~25-30% lower) compared to the API. Both the Explore API "
    "countryProductYear count and the countryProfile.diversity field return a higher "
    "count that includes all products meeting RCA>=1. Either the API count or the "
    "browser-displayed count should be accepted as valid.\n"
    "- Services share: The API's countryYear.exportValue returns goods+services "
    "combined regardless of classification parameter. Accept answers that correctly "
    "compute services share at the product level."
)

_YEAR_GAP_CAVEAT = (
    f"\n\nNote: the agent used the SQL database (data through {_SQL_DATA_MAX_YEAR}) "
    f"while the ground truth may reflect up to {_GRAPHQL_DATA_MAX_YEAR} data. "
    f"Determine whether a year gap is relevant: if the question explicitly asks about "
    f"a year {_SQL_DATA_MAX_YEAR} or earlier, score normally with no adjustment. "
    f"However, if the question asks about a year after {_SQL_DATA_MAX_YEAR}, OR does not "
    "mention a year at all (implying 'latest available', which differs between the two "
    "sources), treat the year gap as relevant. "
    f"When the year gap IS relevant: score data_accuracy 4 if values are plausible for "
    f"{_SQL_DATA_MAX_YEAR} (correct magnitude/direction); reserve 5 for close matches "
    f"despite the gap. Do not penalise factual_correctness if the agent correctly "
    f"stated '{_SQL_DATA_MAX_YEAR}'. "
    "Still penalise normally for wrong countries/products/metrics or fabricated numbers."
)

_GROUND_TRUTH_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _GROUND_TRUTH_SYSTEM_TEXT),
        ("human", _GROUND_TRUTH_HUMAN_TEXT),
    ]
)

_GROUND_TRUTH_PROMPT_WITH_YEAR_GAP = ChatPromptTemplate.from_messages(
    [
        ("system", _GROUND_TRUTH_SYSTEM_TEXT + _YEAR_GAP_CAVEAT),
        ("human", _GROUND_TRUTH_HUMAN_TEXT),
    ]
)

_REFUSAL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert evaluator for a trade-data Q&A system. "
            "The following question is expected to trigger a refusal or limitation "
            "acknowledgment from the agent (e.g. out-of-scope, missing data, "
            "normative policy question).\n\n"
            "Evaluate whether the agent handled it appropriately: did it refuse "
            "gracefully? did it explain why the question can't be answered? "
            "did it avoid fabricating data?",
        ),
        (
            "human",
            "**Question**: {question}\n\n"
            "**Expected behaviour**: {expected_behavior}\n\n"
            "**Agent answer**:\n{agent_answer}\n\n"
            "Evaluate the appropriateness of the agent's response.",
        ),
    ]
)

_PLAUSIBILITY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert evaluator for a trade-data Q&A system. "
            "The following question does NOT have verified ground truth data, "
            "so you cannot check exact numbers. Instead, use your own knowledge "
            "of international trade, economics, and the countries involved to "
            "assess whether the agent's answer is **broadly plausible**.\n\n"
            "Things to check:\n"
            "- Are the countries, products, and time periods reasonable?\n"
            "- Are dollar values in the right order of magnitude? (e.g. a small "
            "island nation shouldn't have $500B in exports)\n"
            "- Are rankings and relative magnitudes sensible? (e.g. petroleum "
            "for Saudi Arabia, electronics for South Korea)\n"
            "- Does the agent acknowledge uncertainty or data limitations "
            "when appropriate?\n"
            "- Did the agent fabricate data or present made-up numbers?\n\n"
            "Be lenient on exact figures — the goal is to catch answers that "
            "are obviously wrong or fabricated, not to verify precision.\n\n"
            "IMPORTANT: This is a plausibility check only. A 'pass' here does "
            "NOT mean the answer is verified — it means it's not obviously wrong.",
        ),
        (
            "human",
            "**Question**: {question}\n\n"
            "**Agent answer**:\n{agent_answer}\n\n"
            "Assess whether this answer is plausible.",
        ),
    ]
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def judge_answer(
    question: str,
    agent_answer: str,
    ground_truth_data: list[dict] | None,
    expected_behavior: str | None = None,
    model: str = "gpt-5-mini",
    provider: str = "openai",
    tools_used: list[str] | None = None,
    classification_note: str | None = None,
) -> dict:
    """Score an agent answer using an LLM judge.

    Three paths:
    1. **ground_truth_data provided** → full 4-dimension rubric against data.
    2. **expected_behavior provided** (no data) → refusal appropriateness check.
    3. **neither** → plausibility check using the judge's own knowledge.

    Args:
        question: The original user question.
        agent_answer: The agent's text response.
        ground_truth_data: Rows from verified SQL execution, or None.
        expected_behavior: For refusal/edge-case questions, the expected behaviour.
        model: Judge LLM model name.
        provider: Judge LLM provider.
        tools_used: List of tool names the agent invoked, or None.
        classification_note: Optional note about product classification context
            (e.g., HS92 vs HS12) to inject into the ground truth judging prompt.

    Returns:
        Dictionary with scores, verdict, reasoning, and ``judge_mode``.
    """
    llm = create_llm(model, provider, temperature=0)

    if ground_truth_data is not None:
        # Path 1: verified ground truth
        # Apply year-gap caveat when the agent used SQL and the years differ
        apply_caveat = (
            tools_used is not None
            and "query_tool" in tools_used
            and _SQL_DATA_MAX_YEAR < _GRAPHQL_DATA_MAX_YEAR
        )

        # Build system text with optional caveats
        system_text = _GROUND_TRUTH_SYSTEM_TEXT + _KNOWN_DATA_CAVEATS
        if apply_caveat:
            system_text += _YEAR_GAP_CAVEAT
        if classification_note:
            system_text += "\n\n" + classification_note

        prompt = ChatPromptTemplate.from_messages(
            [("system", system_text), ("human", _GROUND_TRUTH_HUMAN_TEXT)]
        )
        chain = prompt | llm.with_structured_output(JudgeVerdict)
        result: JudgeVerdict = await chain.ainvoke(
            {
                "question": question,
                "ground_truth": json.dumps(ground_truth_data, indent=2, default=str),
                "agent_answer": agent_answer,
            }
        )
        verdict_dict = result.to_dict()
        if apply_caveat:
            verdict_dict["year_gap_caveat_applied"] = True
        if classification_note:
            verdict_dict["classification_note_applied"] = True
        return verdict_dict

    if expected_behavior is not None:
        # Path 2: expected refusal / limitation acknowledgment
        chain = _REFUSAL_PROMPT | llm.with_structured_output(RefusalVerdict)
        result: RefusalVerdict = await chain.ainvoke(
            {
                "question": question,
                "expected_behavior": expected_behavior,
                "agent_answer": agent_answer,
            }
        )
        return result.to_dict()

    # Path 3: no ground truth, no expected behavior → plausibility check
    chain = _PLAUSIBILITY_PROMPT | llm.with_structured_output(PlausibilityVerdict)
    result: PlausibilityVerdict = await chain.ainvoke(
        {
            "question": question,
            "agent_answer": agent_answer,
        }
    )
    return result.to_dict()
