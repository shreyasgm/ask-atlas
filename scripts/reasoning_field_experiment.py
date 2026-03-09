#!/usr/bin/env python3
"""A/B experiment: does the `reasoning` field in structured output calls matter?

Compares accuracy, latency, and token usage of structured output schemas
with vs. without a reasoning field across three call types:

- GraphQLQueryPlan (currently HAS reasoning → test removing it)
- DocsSelection (currently HAS reasoning → test removing it)
- SchemasAndProductsFound (currently NO reasoning → test adding it)

Usage:
    PYTHONPATH=$(pwd) uv run python scripts/reasoning_field_experiment.py
    PYTHONPATH=$(pwd) uv run python scripts/reasoning_field_experiment.py --repetitions 5
    PYTHONPATH=$(pwd) uv run python scripts/reasoning_field_experiment.py --only graphql
    PYTHONPATH=$(pwd) uv run python scripts/reasoning_field_experiment.py --model gpt-4.1-mini
"""

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field, create_model

from src.config import create_llm
from src.model_config import LIGHTWEIGHT_MODEL, LIGHTWEIGHT_MODEL_PROVIDER
from src.prompts.prompt_docs import DOCUMENT_SELECTION_PROMPT
from src.prompts.prompt_graphql import build_query_plan_prompt
from src.prompts.prompt_sql import PRODUCT_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token tracking callback
# ---------------------------------------------------------------------------


class TokenTracker(BaseCallbackHandler):
    """Captures token usage from LLM responses."""

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for gen_list in response.generations:
            for gen in gen_list:
                msg = gen.message if hasattr(gen, "message") else None
                if msg and hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    self.input_tokens += msg.usage_metadata.get("input_tokens", 0)
                    self.output_tokens += msg.usage_metadata.get("output_tokens", 0)
                elif hasattr(msg, "response_metadata") and msg.response_metadata:
                    usage = msg.response_metadata.get("token_usage", {})
                    self.input_tokens += usage.get("prompt_tokens", 0)
                    self.output_tokens += usage.get("completion_tokens", 0)


# ---------------------------------------------------------------------------
# Schema variants — dynamic model creation
# ---------------------------------------------------------------------------

# ---- GraphQLQueryPlan fields (minus reasoning) ----

_QUERY_TYPE_LITERAL = Literal[
    "country_profile",
    "country_profile_exports",
    "country_profile_partners",
    "country_profile_complexity",
    "country_lookback",
    "new_products",
    "treemap_products",
    "treemap_partners",
    "treemap_bilateral",
    "overtime_products",
    "overtime_partners",
    "marketshare",
    "product_space",
    "feasibility",
    "feasibility_table",
    "growth_opportunities",
    "product_table",
    "country_year",
    "product_info",
    "bilateral_aggregate",
    "explore_bilateral",
    "explore_group",
    "group_products",
    "group_bilateral",
    "group_membership",
    "global_product",
    "global_datum",
    "explore_data_availability",
    "reject",
]

_GRAPHQL_FIELDS: dict[str, Any] = {
    "query_type": (
        _QUERY_TYPE_LITERAL,
        Field(description="Query type classification."),
    ),
    "rejection_reason": (
        str | None,
        Field(default=None, description="Rejection reason."),
    ),
    "api_target": (
        Literal["explore", "country_pages"] | None,
        Field(default=None, description="API target."),
    ),
    "country_name": (
        str | None,
        Field(default=None, description="Primary country."),
    ),
    "country_code_guess": (
        str | None,
        Field(default=None, description="ISO3 code guess."),
    ),
    "partner_name": (
        str | None,
        Field(default=None, description="Partner country."),
    ),
    "partner_code_guess": (
        str | None,
        Field(default=None, description="Partner ISO3 code."),
    ),
    "product_name": (
        str | None,
        Field(default=None, description="Product mentioned."),
    ),
    "product_code_guess": (
        str | None,
        Field(default=None, description="HS code guess."),
    ),
    "product_level": (
        Literal["section", "twoDigit", "fourDigit", "sixDigit"] | None,
        Field(default="fourDigit", description="Product digit level."),
    ),
    "product_class": (
        Literal["HS92", "HS12", "HS22", "SITC"] | None,
        Field(default=None, description="Product classification."),
    ),
    "year": (int | None, Field(default=None, description="Specific year.")),
    "year_min": (int | None, Field(default=None, description="Year range start.")),
    "year_max": (int | None, Field(default=None, description="Year range end.")),
    "group_name": (str | None, Field(default=None, description="Exporter group.")),
    "group_type": (str | None, Field(default=None, description="Group type.")),
    "partner_group_name": (
        str | None,
        Field(default=None, description="Partner group."),
    ),
    "partner_group_type": (
        str | None,
        Field(default=None, description="Partner group type."),
    ),
    "lookback_years": (
        Literal[3, 5, 10, 15] | None,
        Field(default=None, description="Lookback years."),
    ),
    "services_class": (
        Literal["unilateral", "bilateral"] | None,
        Field(default=None, description="Services class."),
    ),
    "trade_direction": (
        Literal["exports", "imports"] | None,
        Field(default=None, description="Trade direction."),
    ),
}


def _make_graphql_model(with_reasoning: bool) -> type[BaseModel]:
    fields = dict(_GRAPHQL_FIELDS)
    if with_reasoning:
        fields["reasoning"] = (
            str,
            Field(description="Step-by-step reasoning (max 300 chars)."),
        )
    return create_model(
        (
            "GraphQLQueryPlanWithReasoning"
            if with_reasoning
            else "GraphQLQueryPlanNoReasoning"
        ),
        **fields,
    )


# ---- DocsSelection variants ----


def _make_docs_model(with_reasoning: bool, max_docs: int = 3) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "selected_indices": (
            list[int],
            Field(
                description=f"Zero-based indices of the 1-{max_docs} most relevant documents.",
                max_length=max_docs,
            ),
        ),
    }
    if with_reasoning:
        fields["reasoning"] = (
            str,
            Field(description="Brief explanation of why these documents are relevant."),
        )
    return create_model(
        "DocsSelectionWithReasoning" if with_reasoning else "DocsSelectionNoReasoning",
        **fields,
    )


# ---- SchemasAndProductsFound variants ----


class _CountryDetails(BaseModel):
    name: str = Field(description="Country name")
    iso3_code: str = Field(description="ISO3 code")


class _ProductDetails(BaseModel):
    name: str = Field(description="Product name")
    classification_schema: str = Field(description="Schema")
    codes: list[str] = Field(description="Product codes")


def _make_product_model(with_reasoning: bool) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "classification_schemas": (
            list[str],
            Field(description="Relevant schema names."),
        ),
        "products": (list[_ProductDetails], Field(description="Identified products.")),
        "requires_product_lookup": (
            bool,
            Field(description="Whether products need DB lookup."),
        ),
        "countries": (
            list[_CountryDetails],
            Field(default_factory=list, description="Countries mentioned."),
        ),
        "requires_group_tables": (
            bool,
            Field(default=False, description="Group aggregate needed."),
        ),
    }
    if with_reasoning:
        fields["reasoning"] = (
            str,
            Field(description="Step-by-step reasoning for extraction."),
        )
    return create_model(
        (
            "SchemasAndProductsWithReasoning"
            if with_reasoning
            else "SchemasAndProductsNoReasoning"
        ),
        **fields,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

GRAPHQL_TEST_CASES = [
    # From prompt examples
    {
        "question": "What is Kenya's economic complexity ranking?",
        "expected": {
            "query_type": "country_profile_complexity",
            "api_target": "country_pages",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
        },
    },
    {
        "question": "What products did Brazil export to China in 2023?",
        "expected": {
            "query_type": "treemap_bilateral",
            "api_target": "explore",
            "country_name": "Brazil",
            "country_code_guess": "BRA",
            "partner_name": "China",
            "partner_code_guess": "CHN",
            "year": 2023,
        },
    },
    {
        "question": "How has Germany's export basket changed since 2010?",
        "expected": {
            "query_type": "overtime_products",
            "api_target": "explore",
            "country_name": "Germany",
            "country_code_guess": "DEU",
            "year_min": 2010,
        },
    },
    {
        "question": "What new products did Vietnam start exporting recently?",
        "expected": {
            "query_type": "new_products",
            "api_target": "country_pages",
            "country_name": "Vietnam",
            "country_code_guess": "VNM",
        },
    },
    {
        "question": "What are the top growth opportunities for Rwanda?",
        "expected": {
            "query_type": "feasibility",
            "api_target": "explore",
            "country_name": "Rwanda",
            "country_code_guess": "RWA",
        },
    },
    {
        "question": "What percentage of global coffee exports does Colombia account for?",
        "expected": {
            "query_type": "marketshare",
            "api_target": "explore",
            "country_name": "Colombia",
            "country_code_guess": "COL",
            "product_name": "coffee",
            "product_code_guess": "0901",
        },
    },
    {
        "question": "Tell me about Kenya's economy and its main exports",
        "expected": {
            "query_type": "country_profile",
            "api_target": "country_pages",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
        },
    },
    {
        "question": "What are the most complex products Kenya could diversify into?",
        "expected": {
            "query_type": "growth_opportunities",
            "api_target": "country_pages",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
        },
    },
    {
        "question": "Show me product-level data for Thailand's exports — RCA, PCI, export values",
        "expected": {
            "query_type": "product_table",
            "api_target": "explore",
            "country_name": "Thailand",
            "country_code_guess": "THA",
        },
    },
    {
        "question": "What years of trade data are available for South Sudan?",
        "expected": {
            "query_type": "explore_data_availability",
            "api_target": "explore",
            "country_name": "South Sudan",
            "country_code_guess": "SSD",
        },
    },
    {
        "question": "What is the total global trade value in 2023?",
        "expected": {
            "query_type": "global_datum",
            "api_target": "explore",
            "year": 2023,
        },
    },
    {
        "question": "Show me the product space for South Korea",
        "expected": {
            "query_type": "product_space",
            "api_target": "explore",
            "country_name": "South Korea",
            "country_code_guess": "KOR",
        },
    },
    {
        "question": "What is the global PCI ranking for electronic integrated circuits?",
        "expected": {"query_type": "product_info", "api_target": "explore"},
    },
    {
        "question": "What does Kenya export to the EU?",
        "expected": {
            "query_type": "group_products",
            "api_target": "explore",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
            "partner_group_name": "EU",
            "partner_group_type": "trade",
        },
    },
    {
        "question": "What does the EU export to Kenya?",
        "expected": {
            "query_type": "group_bilateral",
            "api_target": "explore",
            "group_name": "EU",
            "group_type": "trade",
            "partner_name": "Kenya",
            "partner_code_guess": "KEN",
        },
    },
    {
        "question": "Which countries belong to the EU?",
        "expected": {
            "query_type": "group_membership",
            "api_target": "explore",
            "group_name": "EU",
            "group_type": "trade",
        },
    },
    {
        "question": "What are the top 10 most exported products in the world?",
        "expected": {"query_type": "global_product", "api_target": "explore"},
    },
    {
        "question": "What has been Brazil's ECI trend over the last 15 years?",
        "expected": {
            "query_type": "country_year",
            "api_target": "explore",
            "country_name": "Brazil",
            "country_code_guess": "BRA",
            "lookback_years": 15,
        },
    },
    {
        "question": "What is the top imported product for USA?",
        "expected": {
            "query_type": "treemap_products",
            "api_target": "explore",
            "country_name": "United States",
            "country_code_guess": "USA",
            "trade_direction": "imports",
        },
    },
    {
        "question": "What are Kenya's total exports?",
        "expected": {
            "query_type": "treemap_products",
            "api_target": "explore",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
            "services_class": "unilateral",
        },
    },
    {
        "question": "Calculate the average ECI across all OECD countries for the last 5 years",
        "expected": {"query_type": "reject"},
    },
    {
        "question": "Which 10 countries have the highest RCA in semiconductors?",
        "expected": {"query_type": "reject"},
    },
    {
        "question": "What is Spain's ECI value? Use SITC classification.",
        "expected": {
            "query_type": "country_year",
            "api_target": "country_pages",
            "country_name": "Spain",
            "country_code_guess": "ESP",
            "product_class": "SITC",
        },
    },
    {
        "question": "How has Mexico's export diversification changed in the past decade?",
        "expected": {
            "query_type": "new_products",
            "api_target": "country_pages",
            "country_name": "Mexico",
            "country_code_guess": "MEX",
        },
    },
    {
        "question": "What products does Japan export to the US?",
        "expected": {
            "query_type": "treemap_bilateral",
            "api_target": "explore",
            "country_name": "Japan",
            "country_code_guess": "JPN",
            "partner_name": "United States",
            "partner_code_guess": "USA",
        },
    },
]

# Mock manifest for docs selection
DOCS_MANIFEST_ENTRIES = [
    {
        "index": 0,
        "title": "Atlas Database Schema Reference",
        "purpose": "Database table structures, column definitions, and schema details",
    },
    {
        "index": 1,
        "title": "Trade Data Coverage and Sources",
        "purpose": "Year ranges, data sources (BACI/Comtrade), country coverage, and data gaps",
    },
    {
        "index": 2,
        "title": "Economic Complexity Metrics Glossary",
        "purpose": "Definitions and formulas for ECI, PCI, RCA, proximity, density, COG, COI",
    },
    {
        "index": 3,
        "title": "Product Classification Systems",
        "purpose": "HS92, HS12, SITC classification hierarchies, concordance tables, digit levels",
    },
    {
        "index": 4,
        "title": "Services Trade Data Guide",
        "purpose": "Services trade classification, bilateral vs unilateral, available categories",
    },
    {
        "index": 5,
        "title": "Country and Group Definitions",
        "purpose": "ISO codes, location groups, trade blocs, income groups, regional definitions",
    },
    {
        "index": 6,
        "title": "Growth Opportunities Methodology",
        "purpose": "How feasibility, attractiveness, and strategic value are calculated",
    },
    {
        "index": 7,
        "title": "Atlas API and GraphQL Reference",
        "purpose": "API endpoints, query types, pagination, rate limits",
    },
]

DOCS_MANIFEST_STR = "\n".join(
    f"[{e['index']}] {e['title']}: {e['purpose']}" for e in DOCS_MANIFEST_ENTRIES
)

DOCS_TEST_CASES = [
    {"question": "What is ECI and how is it calculated?", "expected_indices": [2]},
    {"question": "What years of HS12 data are available?", "expected_indices": [1]},
    {"question": "How do I use the Atlas GraphQL API?", "expected_indices": [7]},
    {
        "question": "What is the difference between HS92 and HS12?",
        "expected_indices": [3],
    },
    {"question": "How are growth opportunities calculated?", "expected_indices": [6]},
    {"question": "What services trade data is available?", "expected_indices": [4]},
    {
        "question": "What tables and columns are in the database?",
        "expected_indices": [0],
    },
    {"question": "Which countries are in the EU trade bloc?", "expected_indices": [5]},
    {
        "question": "What is RCA and what does distance measure?",
        "expected_indices": [2],
    },
    {
        "question": "What is PCI and how is product feasibility assessed?",
        "expected_indices": [2, 6],
    },
]

PRODUCT_TEST_CASES = [
    {
        "question": "What were US exports of cotton and wheat in 2021?",
        "expected": {
            "classification_schemas": ["hs12"],
            "requires_product_lookup": True,
            "countries": [{"name": "United States", "iso3_code": "USA"}],
        },
    },
    {
        "question": "What services did India export to the US in 2021?",
        "expected": {
            "classification_schemas": ["services_bilateral"],
            "requires_product_lookup": False,
            "countries": [
                {"name": "India", "iso3_code": "IND"},
                {"name": "United States", "iso3_code": "USA"},
            ],
        },
    },
    {
        "question": "What is the total value of exports for Brazil in 2018?",
        "expected": {
            "classification_schemas": ["hs12", "services_unilateral"],
            "requires_product_lookup": False,
            "countries": [{"name": "Brazil", "iso3_code": "BRA"}],
        },
    },
    {
        "question": "What goods did India export in 2022?",
        "expected": {
            "classification_schemas": ["hs12"],
            "requires_product_lookup": False,
            "countries": [{"name": "India", "iso3_code": "IND"}],
            "requires_group_tables": False,
        },
    },
    {
        "question": "What are the top exports of Sub-Saharan Africa?",
        "expected": {
            "classification_schemas": ["hs12", "services_unilateral"],
            "requires_product_lookup": False,
            "requires_group_tables": True,
            "countries": [],
        },
    },
    {
        "question": "How much coffee does the EU export?",
        "expected": {
            "classification_schemas": ["hs12"],
            "requires_product_lookup": True,
            "requires_group_tables": True,
            "countries": [],
        },
    },
    {
        "question": "What are India's top products?",
        "expected": {
            "classification_schemas": ["hs12", "services_unilateral"],
            "requires_product_lookup": False,
            "countries": [{"name": "India", "iso3_code": "IND"}],
        },
    },
    {
        "question": "What were US exports of cars and vehicles (HS 87) in 2020?",
        "expected": {
            "classification_schemas": ["hs12"],
            "requires_product_lookup": False,
            "countries": [{"name": "United States", "iso3_code": "USA"}],
        },
    },
    {
        "question": "Show me trade in both goods and services between US and China in HS 2012.",
        "expected": {
            "classification_schemas": ["hs12", "services_bilateral"],
            "requires_product_lookup": False,
            "countries": [
                {"name": "United States", "iso3_code": "USA"},
                {"name": "China", "iso3_code": "CHN"},
            ],
        },
    },
    {
        "question": "Which country is the world's biggest exporter of fruits and vegetables?",
        "expected": {
            "classification_schemas": ["hs12"],
            "requires_product_lookup": True,
            "countries": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Accuracy scoring
# ---------------------------------------------------------------------------


def score_graphql(result: dict, expected: dict) -> dict[str, bool]:
    """Score each expected field against the LLM result."""
    scores = {}
    for field, exp_val in expected.items():
        if field == "reasoning":
            continue
        actual = result.get(field)
        # For strings, case-insensitive
        if isinstance(exp_val, str) and isinstance(actual, str):
            scores[field] = actual.lower() == exp_val.lower()
        else:
            scores[field] = actual == exp_val
    return scores


def score_docs(result: dict, expected_indices: list[int]) -> dict[str, bool]:
    """Score docs selection: check if selected indices match expected."""
    actual = set(result.get("selected_indices", []))
    expected = set(expected_indices)
    return {
        "indices_exact_match": actual == expected,
        "expected_covered": expected.issubset(actual),
    }


def score_product(result: dict, expected: dict) -> dict[str, bool]:
    """Score product extraction fields."""
    scores = {}

    # classification_schemas — set equality
    if "classification_schemas" in expected:
        actual_schemas = set(result.get("classification_schemas", []))
        expected_schemas = set(expected["classification_schemas"])
        scores["classification_schemas"] = actual_schemas == expected_schemas

    # requires_product_lookup — exact
    if "requires_product_lookup" in expected:
        scores["requires_product_lookup"] = (
            result.get("requires_product_lookup") == expected["requires_product_lookup"]
        )

    # requires_group_tables — exact
    if "requires_group_tables" in expected:
        scores["requires_group_tables"] = (
            result.get("requires_group_tables", False)
            == expected["requires_group_tables"]
        )

    # countries — compare iso3 codes as sets
    if "countries" in expected:
        actual_codes = {
            c.get("iso3_code", "").upper() for c in result.get("countries", [])
        }
        expected_codes = {c["iso3_code"].upper() for c in expected["countries"]}
        scores["countries"] = actual_codes == expected_codes

    return scores


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


async def run_single_call(
    llm: Any,
    schema: type[BaseModel],
    prompt_text: str,
    method: str = "json_schema",
) -> dict:
    """Run a single structured output call and collect metrics."""
    tracker = TokenTracker()

    if method == "bind_tools":
        from langchain_core.output_parsers.openai_tools import PydanticToolsParser

        bound = llm.bind_tools([schema], tool_choice="any")
        chain = bound | PydanticToolsParser(tools=[schema])
    else:
        chain = llm.with_structured_output(schema, method="json_schema")

    start = time.perf_counter()
    try:
        result = await chain.ainvoke(prompt_text, config={"callbacks": [tracker]})
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return {
            "error": str(e),
            "latency_ms": elapsed,
            "input_tokens": tracker.input_tokens,
            "output_tokens": tracker.output_tokens,
            "result": None,
        }
    elapsed = (time.perf_counter() - start) * 1000

    # Handle bind_tools returning a list
    if isinstance(result, list):
        result = result[0] if result else None

    result_dict = result.model_dump() if result else {}

    return {
        "latency_ms": elapsed,
        "input_tokens": tracker.input_tokens,
        "output_tokens": tracker.output_tokens,
        "result": result_dict,
        "error": None,
    }


async def run_experiment_group(
    name: str,
    llm: Any,
    test_cases: list[dict],
    make_prompt: Any,
    make_model_with: type[BaseModel],
    make_model_without: type[BaseModel],
    score_fn: Any,
    method: str = "json_schema",
    repetitions: int = 3,
    concurrency: int = 10,
) -> dict:
    """Run all test cases for both variants concurrently."""
    sem = asyncio.Semaphore(concurrency)
    total = len(test_cases) * repetitions * 2
    done_count = 0
    lock = asyncio.Lock()

    async def run_one(
        tc_idx: int, tc: dict, variant: str, schema: type[BaseModel], rep: int
    ) -> dict:
        nonlocal done_count
        prompt_text = make_prompt(tc)

        async with sem:
            call_result = await run_single_call(llm, schema, prompt_text, method=method)

        async with lock:
            done_count += 1
            logger.info(
                "  [%s/%s] %s | %s | case %s | rep %s",
                done_count,
                total,
                name,
                variant,
                tc_idx,
                rep + 1,
            )

        if call_result["error"]:
            field_scores = {}
            overall = 0.0
        else:
            field_scores = score_fn(call_result["result"], tc)
            overall = sum(field_scores.values()) / max(len(field_scores), 1)

        return {
            "variant": variant,
            "test_case_idx": tc_idx,
            "question": tc.get("question", ""),
            "repetition": rep,
            "latency_ms": call_result["latency_ms"],
            "input_tokens": call_result["input_tokens"],
            "output_tokens": call_result["output_tokens"],
            "field_accuracy": field_scores,
            "overall_accuracy": overall,
            "error": call_result["error"],
            "raw_result": call_result["result"],
        }

    # Build all tasks
    tasks = []
    for tc_idx, tc in enumerate(test_cases):
        for variant, schema in [
            ("with_reasoning", make_model_with),
            ("without_reasoning", make_model_without),
        ]:
            for rep in range(repetitions):
                tasks.append(run_one(tc_idx, tc, variant, schema, rep))

    # Run all concurrently (bounded by semaphore)
    all_entries = await asyncio.gather(*tasks)

    results: dict[str, list] = {"with_reasoning": [], "without_reasoning": []}
    for entry in all_entries:
        v = entry.pop("variant")
        results[v].append(entry)

    return results


# ---------------------------------------------------------------------------
# Prompt builders (per experiment group)
# ---------------------------------------------------------------------------


def graphql_prompt_builder(tc: dict) -> str:
    return build_query_plan_prompt(tc["question"])


def docs_prompt_builder(tc: dict) -> str:
    return DOCUMENT_SELECTION_PROMPT.format(
        question=tc["question"],
        context_block="",
        manifest=DOCS_MANIFEST_STR,
        max_docs=3,
    )


def product_prompt_builder(tc: dict) -> str:
    # Replicate the ChatPromptTemplate used in production
    return PRODUCT_EXTRACTION_PROMPT + f"\n\nQuestion: {tc['question']}"


# ---------------------------------------------------------------------------
# Score adapters (unify interface to (result_dict, test_case) -> scores)
# ---------------------------------------------------------------------------


def graphql_score_adapter(result: dict, tc: dict) -> dict[str, bool]:
    return score_graphql(result, tc["expected"])


def docs_score_adapter(result: dict, tc: dict) -> dict[str, bool]:
    return score_docs(result, tc["expected_indices"])


def product_score_adapter(result: dict, tc: dict) -> dict[str, bool]:
    return score_product(result, tc["expected"])


# ---------------------------------------------------------------------------
# Summary printing
# ---------------------------------------------------------------------------


def print_summary(name: str, results: dict) -> None:
    """Print a summary table for one experiment group."""
    logger.info("\n%s", "=" * 60)
    logger.info("  %s", name)
    logger.info("%s", "=" * 60)
    logger.info(
        "%-22s | %8s | %11s | %14s",
        "Variant",
        "Accuracy",
        "Avg Latency",
        "Avg Out Tokens",
    )
    logger.info("%s-+-%s-+-%s-+-%s", "-" * 22, "-" * 8, "-" * 11, "-" * 14)

    field_agg: dict[str, dict[str, list[bool]]] = {}

    for variant in ["with_reasoning", "without_reasoning"]:
        entries = results[variant]
        valid = [e for e in entries if not e["error"]]
        errors = len(entries) - len(valid)

        if valid:
            avg_acc = sum(e["overall_accuracy"] for e in valid) / len(valid)
            avg_lat = sum(e["latency_ms"] for e in valid) / len(valid)
            avg_tok = sum(e["output_tokens"] for e in valid) / len(valid)
        else:
            avg_acc = avg_lat = avg_tok = 0.0

        err_str = f" ({errors} errors)" if errors else ""
        logger.info(
            "%-22s | %8s | %9sms | %14s%s",
            variant,
            f"{avg_acc:.2%}",
            f"{avg_lat:.0f}",
            f"{avg_tok:.0f}",
            err_str,
        )

        # Aggregate per-field accuracy
        for e in valid:
            for field, correct in e["field_accuracy"].items():
                field_agg.setdefault(field, {}).setdefault(variant, []).append(correct)

    # Per-field breakdown
    if field_agg:
        logger.info("\nPer-field breakdown:")
        for field in sorted(field_agg.keys()):
            vals = field_agg[field]
            with_acc = sum(vals.get("with_reasoning", [])) / max(
                len(vals.get("with_reasoning", [])), 1
            )
            without_acc = sum(vals.get("without_reasoning", [])) / max(
                len(vals.get("without_reasoning", [])), 1
            )
            diff = without_acc - with_acc
            sign = "+" if diff >= 0 else ""
            logger.info(
                "  %-25s: %s vs %s  (%s%s)",
                field,
                f"{with_acc:.2%}",
                f"{without_acc:.2%}",
                sign,
                f"{diff:.0%}",
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reasoning field A/B experiment")
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repetitions per test case (default 3)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=LIGHTWEIGHT_MODEL,
        help=f"Model name (default {LIGHTWEIGHT_MODEL})",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=LIGHTWEIGHT_MODEL_PROVIDER,
        help=f"Provider (default {LIGHTWEIGHT_MODEL_PROVIDER})",
    )
    parser.add_argument(
        "--only",
        type=str,
        choices=["graphql", "docs", "product"],
        help="Run only one experiment group",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent API calls (default 10)",
    )
    args = parser.parse_args()

    logger.info("Model: %s (%s)", args.model, args.provider)
    logger.info("Repetitions: %s", args.repetitions)
    logger.info("Concurrency: %s", args.concurrency)
    logger.info("")

    llm = create_llm(args.model, args.provider, temperature=0)

    all_results = {}

    # --- GraphQL ---
    if not args.only or args.only == "graphql":
        logger.info(
            "Running GraphQLQueryPlan (%s cases × %s reps × 2 variants)...",
            len(GRAPHQL_TEST_CASES),
            args.repetitions,
        )
        graphql_results = await run_experiment_group(
            name="GraphQLQueryPlan",
            llm=llm,
            test_cases=GRAPHQL_TEST_CASES,
            make_prompt=graphql_prompt_builder,
            make_model_with=_make_graphql_model(True),
            make_model_without=_make_graphql_model(False),
            score_fn=graphql_score_adapter,
            method="function_calling",
            repetitions=args.repetitions,
            concurrency=args.concurrency,
        )
        all_results["GraphQLQueryPlan"] = graphql_results
        print_summary("GraphQLQueryPlan", graphql_results)

    # --- DocsSelection ---
    if not args.only or args.only == "docs":
        logger.info(
            "\nRunning DocsSelection (%s cases × %s reps × 2 variants)...",
            len(DOCS_TEST_CASES),
            args.repetitions,
        )
        docs_results = await run_experiment_group(
            name="DocsSelection",
            llm=llm,
            test_cases=DOCS_TEST_CASES,
            make_prompt=docs_prompt_builder,
            make_model_with=_make_docs_model(True),
            make_model_without=_make_docs_model(False),
            score_fn=docs_score_adapter,
            method="function_calling",
            repetitions=args.repetitions,
            concurrency=args.concurrency,
        )
        all_results["DocsSelection"] = docs_results
        print_summary("DocsSelection", docs_results)

    # --- SchemasAndProductsFound ---
    if not args.only or args.only == "product":
        logger.info(
            "\nRunning SchemasAndProductsFound (%s cases × %s reps × 2 variants)...",
            len(PRODUCT_TEST_CASES),
            args.repetitions,
        )
        product_results = await run_experiment_group(
            name="SchemasAndProductsFound",
            llm=llm,
            test_cases=PRODUCT_TEST_CASES,
            make_prompt=product_prompt_builder,
            make_model_with=_make_product_model(True),
            make_model_without=_make_product_model(False),
            score_fn=product_score_adapter,
            method="function_calling",
            repetitions=args.repetitions,
            concurrency=args.concurrency,
        )
        all_results["SchemasAndProductsFound"] = product_results
        print_summary("SchemasAndProductsFound", product_results)

    # --- Save results ---
    output_path = Path("scripts/reasoning_experiment_results.json")
    # Convert for JSON serialization
    serializable = {}
    for group_name, group_data in all_results.items():
        serializable[group_name] = {}
        for variant, entries in group_data.items():
            serializable[group_name][variant] = [
                {k: v for k, v in e.items()} for e in entries
            ]

    output_path.write_text(json.dumps(serializable, indent=2, default=str))
    logger.info("\nResults saved to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    asyncio.run(main())
