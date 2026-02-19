#!/usr/bin/env python3
"""LLM-as-judge: evaluate agent answers against ground truth data.

Compares the agent's **text answer** to the ground truth **data** (not SQL),
using a 4-dimension rubric scored 1-5 each.  Produces a weighted overall
score and a pass / partial / fail verdict.

For questions without ground truth (edge cases, refusals), the judge
evaluates the *appropriateness* of the response instead.
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
    graceful: bool = Field(
        ..., description="Was the response polite and informative?"
    )
    score: int = Field(
        ..., ge=1, le=5, description="Overall appropriateness score 1-5"
    )
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
            "appropriate_refusal": self.appropriate_refusal,
            "graceful": self.graceful,
            "score": self.score,
            "weighted_score": float(self.score),
            "verdict": self.verdict,
            "reasoning": self.reasoning,
        }


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GROUND_TRUTH_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
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
            "Be strict on factual accuracy; be lenient on rounding differences.",
        ),
        (
            "human",
            "**Question**: {question}\n\n"
            "**Ground truth data** (from verified SQL):\n```json\n{ground_truth}\n```\n\n"
            "**Agent answer**:\n{agent_answer}\n\n"
            "Evaluate the agent's answer against the ground truth.",
        ),
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
) -> dict:
    """Score an agent answer using an LLM judge.

    Args:
        question: The original user question.
        agent_answer: The agent's text response.
        ground_truth_data: Rows from verified SQL execution (None for refusal questions).
        expected_behavior: For refusal/edge-case questions, the expected behaviour description.
        model: Judge LLM model name.
        provider: Judge LLM provider.

    Returns:
        Dictionary with scores, verdict, and reasoning.
    """
    llm = create_llm(model, provider, temperature=0)

    if ground_truth_data is not None:
        chain = _GROUND_TRUTH_PROMPT | llm.with_structured_output(JudgeVerdict)
        result: JudgeVerdict = await chain.ainvoke(
            {
                "question": question,
                "ground_truth": json.dumps(ground_truth_data, indent=2, default=str),
                "agent_answer": agent_answer,
            }
        )
        return result.to_dict()
    else:
        chain = _REFUSAL_PROMPT | llm.with_structured_output(RefusalVerdict)
        result: RefusalVerdict = await chain.ainvoke(
            {
                "question": question,
                "expected_behavior": expected_behavior or "Graceful refusal",
                "agent_answer": agent_answer,
            }
        )
        return result.to_dict()
