#!/usr/bin/env python3
"""LLM-as-judge: evaluate agent answers against ground truth data.

Four evaluation paths:

1. **Ground truth** — agent's text answer is scored against verified data rows
   from executed SQL (binary pass/fail per dimension).
2. **Refusal** — for out-of-scope / data-boundary questions with an
   ``expected_behavior`` field, checks whether the agent refused appropriately.
3. **Web research** — for questions without SQL ground truth or expected_behavior,
   but with a ``web_research.json`` reference answer produced by an independent
   LLM with web search.  Scores the agent against this research-grade reference.
4. **Plausibility** — for questions without any reference data, the judge uses
   its own knowledge to assess whether the answer is roughly plausible
   (not fabricated nonsense).  These results are flagged as
   ``"judge_mode": "plausibility"``.

Scoring version 2: binary pass/fail per dimension (replacing Likert 1-5).
"""

from __future__ import annotations

import json
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from src.config import create_llm

# ---------------------------------------------------------------------------
# Structured output schema — Scoring Version 2 (binary pass/fail)
# ---------------------------------------------------------------------------

# Legacy weights kept for backward-compat reading of old Likert verdicts.
DIMENSION_WEIGHTS = {
    "factual_correctness": 0.35,
    "data_accuracy": 0.30,
    "completeness": 0.20,
    "reasoning_quality": 0.15,
}

# Dimensions whose failure caps the overall verdict at "partial" even when
# the total pass_count would otherwise qualify as "pass".
_CRITICAL_DIMENSIONS = frozenset({"factual_correctness", "data_accuracy"})


class DimensionPass(BaseModel):
    passed: bool = Field(..., description="Whether this dimension passes")
    reasoning: str = Field(..., description="Brief justification")


class JudgeVerdict(BaseModel):
    """Structured verdict from the LLM judge (binary pass/fail per dimension)."""

    factual_correctness: DimensionPass = Field(
        ..., description="Are specific numbers, countries, products correct?"
    )
    data_accuracy: DimensionPass = Field(
        ..., description="Do reported numbers match ground truth (within ±5%)?"
    )
    completeness: DimensionPass = Field(
        ..., description="Does the answer address all parts of the question?"
    )
    reasoning_quality: DimensionPass = Field(
        ..., description="Is the interpretation and analysis sound?"
    )
    overall_comment: str = Field(
        ..., description="One-sentence summary of the evaluation"
    )

    @property
    def pass_count(self) -> int:
        return sum(
            1
            for d in (
                self.factual_correctness,
                self.data_accuracy,
                self.completeness,
                self.reasoning_quality,
            )
            if d.passed
        )

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        pc = self.pass_count
        if pc >= 3:
            # Cap at "partial" if a critical dimension failed
            if not self.factual_correctness.passed or not self.data_accuracy.passed:
                return "partial"
            return "pass"
        if pc == 2:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        pc = self.pass_count
        return {
            "judge_mode": "ground_truth",
            "factual_correctness": {
                "passed": self.factual_correctness.passed,
                "reasoning": self.factual_correctness.reasoning,
            },
            "data_accuracy": {
                "passed": self.data_accuracy.passed,
                "reasoning": self.data_accuracy.reasoning,
            },
            "completeness": {
                "passed": self.completeness.passed,
                "reasoning": self.completeness.reasoning,
            },
            "reasoning_quality": {
                "passed": self.reasoning_quality.passed,
                "reasoning": self.reasoning_quality.reasoning,
            },
            "pass_count": pc,
            "weighted_score": float(pc),  # backward compat
            "verdict": self.verdict,
            "overall_comment": self.overall_comment,
        }


class RefusalVerdict(BaseModel):
    """Verdict for questions that expect a refusal or edge-case handling."""

    appropriate_refusal: bool = Field(
        ..., description="Did the agent appropriately refuse or flag the limitation?"
    )
    graceful: bool = Field(..., description="Was the response polite and informative?")
    reasoning: str = Field(..., description="Justification")

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "pass" if self.appropriate_refusal and self.graceful else "fail"

    def to_dict(self) -> dict:
        is_pass = self.verdict == "pass"
        return {
            "judge_mode": "refusal",
            "appropriate_refusal": self.appropriate_refusal,
            "graceful": self.graceful,
            "pass_count": 4 if is_pass else 0,
            "weighted_score": 4.0 if is_pass else 0.0,  # backward compat
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
    reasoning: str = Field(..., description="Justification")

    @property
    def verdict(self) -> Literal["pass", "fail"]:
        return "pass" if self.plausible and not self.factually_absurd else "fail"

    def to_dict(self) -> dict:
        is_pass = self.verdict == "pass"
        return {
            "judge_mode": "plausibility",
            "plausible": self.plausible,
            "factually_absurd": self.factually_absurd,
            "pass_count": 4 if is_pass else 0,
            "weighted_score": 4.0 if is_pass else 0.0,  # backward compat
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "note": "No ground truth SQL — scored on plausibility only, not verified accuracy.",
        }


class WebResearchVerdict(BaseModel):
    """Verdict for questions scored against web-research context.

    The web research provides directional context (not exact reference data)
    for evaluating a database-query agent's answer.
    """

    factual_correctness: DimensionPass = Field(
        ...,
        description=(
            "Are the general trends directionally correct given web research context? "
            "Different specific items are fine if the agent's systematic approach is valid."
        ),
    )
    data_accuracy: DimensionPass = Field(
        ...,
        description=(
            "Are the agent's numbers internally consistent and plausible in magnitude? "
            "Do NOT match against web research figures — different sources and years are expected."
        ),
    )
    completeness: DimensionPass = Field(
        ...,
        description=(
            "Does the answer address all parts of the question? "
            "Structured data tables are a valid and complete response format."
        ),
    )
    reasoning_quality: DimensionPass = Field(
        ...,
        description=(
            "Does the agent explain its methodology, time window, and data limitations? "
            "Clear data presentation counts as good reasoning."
        ),
    )
    overall_comment: str = Field(
        ..., description="One-sentence summary of the evaluation"
    )

    @property
    def pass_count(self) -> int:
        return sum(
            1
            for d in (
                self.factual_correctness,
                self.data_accuracy,
                self.completeness,
                self.reasoning_quality,
            )
            if d.passed
        )

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        pc = self.pass_count
        if pc >= 3:
            if not self.factual_correctness.passed or not self.data_accuracy.passed:
                return "partial"
            return "pass"
        if pc == 2:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        pc = self.pass_count
        return {
            "judge_mode": "web_research",
            "factual_correctness": {
                "passed": self.factual_correctness.passed,
                "reasoning": self.factual_correctness.reasoning,
            },
            "data_accuracy": {
                "passed": self.data_accuracy.passed,
                "reasoning": self.data_accuracy.reasoning,
            },
            "completeness": {
                "passed": self.completeness.passed,
                "reasoning": self.completeness.reasoning,
            },
            "reasoning_quality": {
                "passed": self.reasoning_quality.passed,
                "reasoning": self.reasoning_quality.reasoning,
            },
            "pass_count": pc,
            "weighted_score": float(pc),  # backward compat
            "verdict": self.verdict,
            "overall_comment": self.overall_comment,
            "note": (
                "Scored against web-research reference (not verified SQL ground truth). "
                "Data accuracy scoring is lenient on exact numbers."
            ),
        }


# ---------------------------------------------------------------------------
# Data-year coverage constants (mirror src/prompts.py values)
# ---------------------------------------------------------------------------

_SQL_DATA_MAX_YEAR: int = 2024
_GRAPHQL_DATA_MAX_YEAR: int = 2024

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_GROUND_TRUTH_SYSTEM_TEXT = (
    "You are an expert evaluator for a trade-data Q&A system. "
    "Compare the agent's answer to the ground truth data.\n\n"
    "For each dimension, determine PASS or FAIL. Provide brief reasoning for each.\n\n"
    "- **Factual Correctness (PASS/FAIL):** PASS if the agent identifies the correct "
    "countries, products, time periods, and directions of change as shown in the ground "
    "truth. Minor omissions acceptable (e.g., missing one country in a top-10 list). "
    "FAIL if wrong countries, wrong products, wrong direction, or fabricated entities.\n"
    "- **Data Accuracy (PASS/FAIL):** PASS if reported numbers match ground truth "
    "within ±5%. FAIL if off by >5%, wrong order of magnitude, or fabricated.\n"
    "- **Completeness (PASS/FAIL):** PASS if the answer addresses all parts of "
    "the question. FAIL if a significant part is ignored.\n"
    "- **Reasoning Quality (PASS/FAIL):** PASS if interpretation is sound and "
    "doesn't contradict its own data. FAIL if logical errors, self-contradictions, "
    "or unsupported conclusions."
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
    f"When the year gap IS relevant: give data_accuracy a PASS if values are plausible "
    f"for {_SQL_DATA_MAX_YEAR} (correct magnitude/direction). Do not penalise "
    f"factual_correctness if the agent correctly stated '{_SQL_DATA_MAX_YEAR}'. "
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
            "did it avoid fabricating data?\n\n"
            "Produce a binary pass/fail verdict: PASS if the agent appropriately "
            "refused AND did so gracefully. FAIL otherwise.",
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
            "Produce two boolean outputs: `plausible` and `factually_absurd`. "
            "PASS if plausible and NOT factually absurd. FAIL otherwise.\n\n"
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

_WEB_RESEARCH_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert evaluator for a trade-data Q&A system.\n\n"
            "IMPORTANT CONTEXT: The agent being evaluated is a **database query system** "
            "that answers questions by querying the Atlas of Economic Complexity trade "
            "database and returning specific data. It is NOT a research analyst.\n\n"
            "You have been given a **web research summary** compiled by an independent LLM "
            "with web search. This research provides DIRECTIONAL CONTEXT — use it to check "
            "whether the agent's answer is in the right ballpark, NOT as a strict reference "
            "to match against.\n\n"
            "The agent and the web research will legitimately differ in:\n"
            "- **Data sources**: Agent uses Atlas DB (trade data through 2022); web research "
            "uses news articles, government reports, and mixed sources (potentially newer).\n"
            "- **Product granularity**: Agent uses HS codes (e.g., 'HS 6702 Artificial "
            "flowers'); web research uses common names (e.g., 'gallium', 'solar panels'). "
            "These often don't map 1:1.\n"
            "- **Metrics**: Agent measures export value share from trade data; web research "
            "may mix production share, refining capacity, or other metrics.\n"
            "- **Country/product lists**: Agent does systematic computation across all data; "
            "web research cherry-picks prominent examples from news. Both can be correct.\n"
            "- **Format**: Agent returns structured data tables; web research returns "
            "narrative prose. Tables with numbers are the EXPECTED agent output format.\n\n"
            "These differences are EXPECTED and must NOT be penalised.\n\n"
            "For each dimension, determine PASS or FAIL. Provide brief reasoning for each.\n\n"
            "- **Factual Correctness (PASS/FAIL):** PASS if the agent's answer is "
            "directionally consistent with the web research — right trends, right major "
            "players, right general story. Different specific countries/products OK if the "
            "agent's systematic approach is valid. FAIL if the agent contradicts major "
            "directional claims, identifies fundamentally different top players with no "
            "reasonable explanation, or fabricates trends.\n"
            "- **Data Accuracy (PASS/FAIL):** PASS if numbers are internally consistent, "
            "plausible in magnitude, and not contradicted by clear web research benchmarks. "
            "FAIL if internally inconsistent (percentages don't sum, growth rates contradict "
            "start/end values), implausible magnitude, or specific numbers directly contradict "
            "well-sourced web research figures by more than an order of magnitude.\n"
            "- **Completeness (PASS/FAIL):** PASS if the answer addresses all parts of the "
            "question. FAIL if a significant part is ignored.\n"
            "- **Reasoning Quality (PASS/FAIL):** PASS if the agent explains data source, "
            "time window, methodology, and doesn't draw unsupported conclusions. "
            "FAIL if claims unsupported by own data or fails to acknowledge obvious "
            "limitations.\n\n"
            "Be FAIR. The web research is context for an informed plausibility check, "
            "not ground truth. The agent's job is to query trade data accurately, not to "
            "write research reports. A data-table answer that captures the right trends "
            "from the Atlas database is a good answer.",
        ),
        (
            "human",
            "**Question**: {question}\n\n"
            "**Web research context** (for directional reference only — NOT ground truth):"
            "\n{web_research_answer}\n\n"
            "**Agent answer**:\n{agent_answer}\n\n"
            "Evaluate the agent's answer. Remember: the agent is a database query system. "
            "Use the web research as context, not as a strict reference to match against.",
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
    web_research_answer: str | None = None,
    model: str = "gpt-5-mini",
    provider: str = "openai",
    tools_used: list[str] | None = None,
    classification_note: str | None = None,
) -> dict:
    """Score an agent answer using an LLM judge.

    Four paths (in priority order):
    1. **ground_truth_data provided** → full 4-dimension rubric against data.
    2. **expected_behavior provided** (no data) → refusal appropriateness check.
    3. **web_research_answer provided** → 4-dimension rubric against web research.
    4. **none of the above** → plausibility check using the judge's own knowledge.

    Args:
        question: The original user question.
        agent_answer: The agent's text response.
        ground_truth_data: Rows from verified SQL execution, or None.
        expected_behavior: For refusal/edge-case questions, the expected behaviour.
        web_research_answer: Reference answer from independent web research, or None.
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
        chain = prompt | llm.with_structured_output(JudgeVerdict, method="json_schema")
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
        chain = _REFUSAL_PROMPT | llm.with_structured_output(
            RefusalVerdict, method="json_schema"
        )
        result: RefusalVerdict = await chain.ainvoke(
            {
                "question": question,
                "expected_behavior": expected_behavior,
                "agent_answer": agent_answer,
            }
        )
        return result.to_dict()

    if web_research_answer is not None:
        # Path 3: web research reference answer
        chain = _WEB_RESEARCH_PROMPT | llm.with_structured_output(
            WebResearchVerdict, method="json_schema"
        )
        result: WebResearchVerdict = await chain.ainvoke(
            {
                "question": question,
                "web_research_answer": web_research_answer,
                "agent_answer": agent_answer,
            }
        )
        return result.to_dict()

    # Path 4: no ground truth, no expected behavior, no web research → plausibility check
    chain = _PLAUSIBILITY_PROMPT | llm.with_structured_output(
        PlausibilityVerdict, method="json_schema"
    )
    result: PlausibilityVerdict = await chain.ainvoke(
        {
            "question": question,
            "agent_answer": agent_answer,
        }
    )
    return result.to_dict()
