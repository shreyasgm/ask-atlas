#!/usr/bin/env python3
"""LLM-as-judge: evaluate agent-generated Atlas links against ground truth URLs.

Separate from the answer quality judge (judge.py). Produces independent link
quality scores across four dimensions:

- **Link Presence** (0.35): Was at least one well-formed Atlas link generated?
- **Content Relevance** (0.30): Does the linked page show the data needed to
  verify the answer?
- **Entity Correctness** (0.25): Are the correct countries and products referenced?
- **Parameter Accuracy** (0.10): Are years, trade direction, product level, and
  classification correct?

Same thresholds as the main judge: pass >= 3.5, partial >= 2.5, fail < 2.5.
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


class LinkDimensionScore(BaseModel):
    score: int = Field(..., ge=1, le=5, description="Score from 1 (worst) to 5 (best)")
    reasoning: str = Field(..., description="Brief justification for this score")


class LinkVerdict(BaseModel):
    """Structured verdict from the link quality judge."""

    link_presence: LinkDimensionScore = Field(
        ...,
        description="Was at least one well-formed Atlas link generated?",
    )
    content_relevance: LinkDimensionScore = Field(
        ...,
        description="Does the linked page show the data needed to verify the answer?",
    )
    entity_correctness: LinkDimensionScore = Field(
        ...,
        description="Are the correct countries and products referenced in the link?",
    )
    parameter_accuracy: LinkDimensionScore = Field(
        ...,
        description="Are years, trade direction, product level, classification correct?",
    )
    overall_comment: str = Field(
        ..., description="One-sentence summary of the link evaluation"
    )

    @property
    def weighted_score(self) -> float:
        scores = {
            "link_presence": self.link_presence.score,
            "content_relevance": self.content_relevance.score,
            "entity_correctness": self.entity_correctness.score,
            "parameter_accuracy": self.parameter_accuracy.score,
        }
        return sum(
            scores[k] * LINK_DIMENSION_WEIGHTS[k] for k in LINK_DIMENSION_WEIGHTS
        )

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
            "link_presence": {
                "score": self.link_presence.score,
                "reasoning": self.link_presence.reasoning,
            },
            "content_relevance": {
                "score": self.content_relevance.score,
                "reasoning": self.content_relevance.reasoning,
            },
            "entity_correctness": {
                "score": self.entity_correctness.score,
                "reasoning": self.entity_correctness.reasoning,
            },
            "parameter_accuracy": {
                "score": self.parameter_accuracy.score,
                "reasoning": self.parameter_accuracy.reasoning,
            },
            "weighted_score": round(self.weighted_score, 3),
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

## Scoring Rubric (1-5 each)

### Link Presence (weight 0.35)
- **5**: A well-formed Atlas link exists with proper URL structure and parameters.
- **4**: Link exists with minor formatting issues but is functional.
- **3**: Link exists but is generic/incomplete (e.g., treemap with no country specified, \
or country page root with no subpage).
- **2**: Link exists but is malformed or points to a non-existent page.
- **1**: No Atlas link was generated at all.

### Content Relevance (weight 0.30)
Does the linked page contain the data needed to verify the answer? It does not matter \
whether it is a country page or explore page — only whether the page shows the relevant data.
- **5**: The page directly shows the exact data referenced in the answer (e.g., answer \
mentions top exports, link goes to export-basket or treemap for that country).
- **4**: The page shows the right type of data but requires minor navigation (e.g., \
scrolling, changing a dropdown) to find the exact data point.
- **3**: The page shows related but not exactly matching data (e.g., answer is about \
ECI but link goes to export-basket).
- **2**: The page is tangentially related (right country, wrong data type entirely).
- **1**: The page is irrelevant to the answer.

### Entity Correctness (weight 0.25)
Are the correct country/countries and products referenced in the link?
- **5**: All entities (countries, products) in the link exactly match what the answer discusses.
- **4**: Main entity is correct but a secondary one is wrong or missing.
- **3**: Some entities match but others are wrong or missing.
- **2**: The primary entity is wrong (e.g., wrong country).
- **1**: Completely wrong entities.

### Parameter Accuracy (weight 0.10)
Are years, trade direction (export/import), product level, and classification correct?
- **5**: All parameters match (year, trade direction, product level, classification).
- **4**: One minor parameter differs (e.g., year off by 1-2 years).
- **3**: Parameters are mostly right but with notable differences (e.g., wrong \
trade direction, significantly wrong year).
- **2**: Multiple parameters are wrong.
- **1**: Parameters are fundamentally wrong or missing.

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
    model: str = "gpt-5-mini",
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
    chain = _LINK_JUDGE_PROMPT | llm.with_structured_output(LinkVerdict)

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
