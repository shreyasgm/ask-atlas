#!/usr/bin/env python3
"""LLM-as-judge: evaluate agent-generated Atlas links against ground truth URLs.

Separate from the answer quality judge (judge.py). Produces independent link
quality verdicts across four dimensions (binary pass/fail):

- **Link Presence**: Was at least one well-formed Atlas link generated?
- **Content Relevance**: Does the linked page show the data needed to
  verify the answer?
- **Entity Correctness**: Are the correct countries and products referenced?
- **Parameter Accuracy**: Are years, trade direction, product level, and
  classification correct?

Scoring version 2: binary pass/fail per dimension (replacing Likert 1-5).
Verdict: >=3 pass = "pass" (capped to "partial" if link_presence or
content_relevance fails), 2 = "partial", <2 = "fail".
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

LINK_DIMENSION_WEIGHTS = {
    "link_presence": 0.35,
    "content_relevance": 0.30,
    "entity_correctness": 0.25,
    "parameter_accuracy": 0.10,
}


class LinkDimensionPass(BaseModel):
    passed: bool = Field(..., description="Whether this dimension passes")
    reasoning: str = Field(..., description="Brief justification")


class LinkVerdict(BaseModel):
    """Structured verdict from the link quality judge (binary pass/fail)."""

    link_presence: LinkDimensionPass = Field(
        ...,
        description="PASS if a well-formed Atlas link exists. FAIL if no link or malformed.",
    )
    content_relevance: LinkDimensionPass = Field(
        ...,
        description="PASS if linked page shows data relevant to the answer. FAIL if irrelevant page.",
    )
    entity_correctness: LinkDimensionPass = Field(
        ...,
        description="PASS if correct countries/products in link. FAIL if primary entity wrong.",
    )
    parameter_accuracy: LinkDimensionPass = Field(
        ...,
        description="PASS if year, trade direction, product level correct or close. FAIL if fundamentally wrong.",
    )
    overall_comment: str = Field(
        ..., description="One-sentence summary of the link evaluation"
    )

    @property
    def pass_count(self) -> int:
        return sum(
            1
            for d in (
                self.link_presence,
                self.content_relevance,
                self.entity_correctness,
                self.parameter_accuracy,
            )
            if d.passed
        )

    @property
    def verdict(self) -> Literal["pass", "partial", "fail"]:
        pc = self.pass_count
        if pc >= 3:
            # Cap at "partial" if a critical link dimension failed
            if not self.link_presence.passed or not self.content_relevance.passed:
                return "partial"
            return "pass"
        if pc == 2:
            return "partial"
        return "fail"

    def to_dict(self) -> dict:
        pc = self.pass_count
        return {
            "link_presence": {
                "passed": self.link_presence.passed,
                "reasoning": self.link_presence.reasoning,
            },
            "content_relevance": {
                "passed": self.content_relevance.passed,
                "reasoning": self.content_relevance.reasoning,
            },
            "entity_correctness": {
                "passed": self.entity_correctness.passed,
                "reasoning": self.entity_correctness.reasoning,
            },
            "parameter_accuracy": {
                "passed": self.parameter_accuracy.passed,
                "reasoning": self.parameter_accuracy.reasoning,
            },
            "pass_count": pc,
            "weighted_score": float(pc),  # backward compat
            "verdict": self.verdict,
            "overall_comment": self.overall_comment,
        }


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_LINK_JUDGE_SYSTEM_TEXT = """\
You are an expert evaluator for the Harvard Growth Lab's Atlas of Economic Complexity \
(https://atlas.hks.harvard.edu). Your task is to evaluate whether the agent-generated \
Atlas link(s) correctly point to a page that lets the user verify the agent's answer.

## Scoring Rubric (PASS/FAIL each)

### Link Presence
**PASS** if a well-formed Atlas link exists with proper URL structure and parameters \
(minor formatting issues OK if functional). \
**FAIL** if no Atlas link was generated, or the link is malformed/points to a non-existent page.

### Content Relevance
Does the linked page contain the data needed to verify the answer? It does not matter \
whether it is a country page or explore page — only whether the page shows the relevant data.
**PASS** if the page shows data relevant to the answer (exact data or close enough that \
minor navigation like scrolling or changing a dropdown reaches the data). \
**FAIL** if the page is irrelevant or only tangentially related to the answer.

### Entity Correctness
**PASS** if the correct primary countries and products are referenced in the link \
(secondary entities may be missing but primary must be correct). \
**FAIL** if the primary entity is wrong (e.g., wrong country).

### Parameter Accuracy
**PASS** if year, trade direction, product level, and classification are correct or close \
(e.g., year off by 1-2 years is acceptable). \
**FAIL** if parameters are fundamentally wrong (e.g., wrong trade direction, year off by \
more than 2 years, wrong product level).

## Atlas Page Reference

### Country Pages
Base URL: `https://atlas.hks.harvard.edu/countries/{{country_id}}`

Country IDs use M49/ISO 3166-1 numeric codes (e.g., 840=USA, 404=Kenya, 724=Spain, \
392=Japan, 792=Turkiye).

| # | Subpage | URL Pattern | Data Available |
|---|---------|------------|----------------|
| 1 | Introduction | `/countries/{{id}}` | GDP per capita, population, ECI ranking, growth \
projection, complexity-income relationship |
| 2 | Export Basket | `/countries/{{id}}/export-basket` | Total exports, exporter rank, \
current account, treemap of products, export growth rate, top trade partners |
| 3 | Export Complexity | `/countries/{{id}}/export-complexity` | ECI ranking, rank change \
over 10 years, treemap colored by complexity (PCI values) |
| 4 | Growth Dynamics | `/countries/{{id}}/growth-dynamics` | Export growth bubble chart, \
product-level growth rates (CAGR), ECI value |
| 5 | Market Share | `/countries/{{id}}/market-share` | Largest market share sector, share \
of global trade, sector market share time series |
| 6 | Diversification | `/countries/{{id}}/new-products` | Number of new products gained/lost, \
product turnover visualization |
| 7 | Product Space | `/countries/{{id}}/paths` | Country's product space network |
| 8 | Strategic Approach | `/countries/{{id}}/strategic-approach` | Recommended approach \
(Parsimonious, Strategic Bets, Light Touch, etc.) |
| 9 | Growth Opportunities | `/countries/{{id}}/growth-opportunities` | Feasibility vs \
complexity scatter, list of opportunity products |
| 10 | Product Table | `/countries/{{id}}/product-table` | Sortable table of products with \
RCA, distance, complexity, opportunity gain |
| 11 | Summary | `/countries/{{id}}/summary` | Country summary text, key metrics overview |

### Explore Pages
Base URL: `https://atlas.hks.harvard.edu/explore/{{viz_type}}`

| # | Viz Type | URL Pattern | Data Available |
|---|----------|------------|----------------|
| 1 | Treemap | `/explore/treemap?year={{y}}&exporter=country-{{iso}}` | Trade composition \
by product (Products mode) or by trade partner (Locations mode: add `view=markets`) |
| 2 | Geomap | `/explore/geomap?year={{y}}&exporter=country-{{iso}}` | Choropleth map of \
bilateral trade flows (Locations mode only) |
| 3 | Overtime | `/explore/overtime?year={{y}}&startYear={{s}}&endYear={{e}}&exporter=country-{{iso}}` \
| Stacked area time series of trade composition over time |
| 4 | Market Share | `/explore/marketshare?year={{y}}&startYear={{s}}&endYear={{e}}&exporter=country-{{iso}}` \
| Sector-level global market share time series |
| 5 | Product Space | `/explore/productspace?year={{y}}&exporter=country-{{iso}}` | Interactive \
product space network showing revealed comparative advantage |
| 6 | Feasibility | `/explore/feasibility?year={{y}}&exporter=country-{{iso}}` | Growth \
opportunity scatter (feasibility vs strategic value) |
| 7 | Feasibility Table | `/explore/feasibility/table?year={{y}}&exporter=country-{{iso}}&productLevel=4` \
| Ranked table of growth opportunity products |

### Explore URL Parameters
| Parameter | Values | Description |
|-----------|--------|-------------|
| `year` | `1995`-`2024` | Display year |
| `startYear`/`endYear` | `1995`-`2024` | Time series range (overtime, marketshare) |
| `exporter` | `country-{{iso}}`, `group-{{id}}` | Exporter country or group |
| `importer` | `country-{{iso}}`, `group-1` (World) | Importer country or "World" |
| `product` | `product-HS92-{{id}}` | Filter to specific product |
| `productLevel` | `2`, `4`, `6` | HS digit level |
| `view` | `markets` | Switch to Locations mode |
| `tradeDirection` | `imports` | Import flows (default = exports) |

### Key Overlaps Between Country Pages and Explore Pages
- Export composition: Both `/countries/{{id}}/export-basket` (treemap) and \
`/explore/treemap?exporter=country-{{iso}}` show the same data
- Product space: Both `/countries/{{id}}/paths` and \
`/explore/productspace?exporter=country-{{iso}}` show product space
- Growth opportunities: Both `/countries/{{id}}/growth-opportunities` and \
`/explore/feasibility?exporter=country-{{iso}}` show opportunity products

## Important Notes
- Country page IDs use M49 numeric codes (e.g., 724 for Spain)
- Explore page `exporter` parameter uses `country-{{iso_numeric}}` format (e.g., `country-724`)
- The explore page exporter format uses the same M49 numeric codes, just prefixed with "country-"
- `group-1` in the importer field means "World" (all partners)
- If no exporter is specified in an explore URL, the site defaults to an arbitrary country \
— this is usually wrong
- Country pages are single scrollable pages — all subpages are sections, not separate loads
"""

_LINK_JUDGE_HUMAN_TEXT = """\
**Question asked**: {question}

**Agent-generated Atlas links**:
```json
{agent_links}
```

**Ground truth Atlas URL**: {ground_truth_url}

Evaluate the agent-generated link(s) against the ground truth URL. Consider whether \
the generated link(s) would let a user verify the agent's answer to the question.
"""

_LINK_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _LINK_JUDGE_SYSTEM_TEXT),
        ("human", _LINK_JUDGE_HUMAN_TEXT),
    ]
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def judge_links(
    question: str,
    agent_links: list[dict],
    ground_truth_url: str | None,
    model: str = "gpt-5.4",
    provider: str = "openai",
) -> dict:
    """Evaluate agent-generated Atlas links against a ground truth URL.

    Only called when the agent used the GraphQL pipeline and produced links.

    Args:
        question: The original user question.
        agent_links: List of link dicts from ``graphql_atlas_links``.
        ground_truth_url: The expected Atlas URL from ground truth, or None.
        model: Judge LLM model name.
        provider: Judge LLM provider.

    Returns:
        Dictionary with per-dimension scores, weighted score, verdict, and comment.
    """
    llm = create_llm(model, provider, temperature=0)
    chain = _LINK_JUDGE_PROMPT | llm.with_structured_output(
        LinkVerdict, method="json_schema"
    )

    gt_url_str = (
        ground_truth_url if ground_truth_url else "(no ground truth URL available)"
    )

    result: LinkVerdict = await chain.ainvoke(
        {
            "question": question,
            "agent_links": json.dumps(agent_links, indent=2, default=str),
            "ground_truth_url": gt_url_str,
        }
    )
    return result.to_dict()
