#!/usr/bin/env python3
"""Experiment: does relaxing Literal enums to plain strings reduce structured output latency?

Hypothesis: OpenAI's json_schema constraint decoding is significantly slower for
large enum types (e.g., 28-option Literal for query_type) than for unconstrained
string fields. If true, relaxing enums in the schema and validating post-hoc
could meaningfully reduce plan_query latency.

Variants tested (all use method="json_schema", same prompt, same field count):
  A. full_enums     — Current production schema (all Literal constraints)
  B. relaxed_qt     — query_type relaxed to str, all other enums intact
  C. all_relaxed    — All Literal fields relaxed to str/int/float

Usage:
    PYTHONPATH=$(pwd) uv run python scripts/enum_relaxation_experiment.py
    PYTHONPATH=$(pwd) uv run python scripts/enum_relaxation_experiment.py --repetitions 7
    PYTHONPATH=$(pwd) uv run python scripts/enum_relaxation_experiment.py --concurrency 5
"""

import argparse
import asyncio
import json
import logging
import statistics
import time
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel, Field, create_model

from src.config import create_llm
from src.model_config import LIGHTWEIGHT_MODEL, LIGHTWEIGHT_MODEL_PROVIDER
from src.prompts.prompt_graphql import build_query_plan_prompt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token tracking callback (same as reasoning experiment)
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
# Schema variants
# ---------------------------------------------------------------------------

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

VALID_QUERY_TYPES = set(_QUERY_TYPE_LITERAL.__args__)  # type: ignore[union-attr]


def _make_schema(variant: str) -> type[BaseModel]:
    """Build a GraphQLQueryPlan-equivalent schema with different enum strictness.

    Args:
        variant: One of "full_enums", "relaxed_qt", "all_relaxed".
    """
    if variant == "full_enums":
        # Production-equivalent: all Literal constraints
        qt_type = _QUERY_TYPE_LITERAL
        api_type = Literal["explore", "country_pages"] | None
        pl_type = Literal["section", "twoDigit", "fourDigit", "sixDigit"] | None
        pc_type = Literal["HS92", "HS12", "HS22", "SITC"] | None
        lb_type = Literal[3, 5, 10, 15] | None
        sc_type = Literal["unilateral", "bilateral"] | None
        td_type = Literal["exports", "imports"] | None
        strat_type = (
            Literal["balanced", "low_hanging_fruit", "long_jumps", "custom"] | None
        )
    elif variant == "relaxed_qt":
        # Only query_type relaxed to str
        qt_type = str  # type: ignore[assignment]
        api_type = Literal["explore", "country_pages"] | None
        pl_type = Literal["section", "twoDigit", "fourDigit", "sixDigit"] | None
        pc_type = Literal["HS92", "HS12", "HS22", "SITC"] | None
        lb_type = Literal[3, 5, 10, 15] | None
        sc_type = Literal["unilateral", "bilateral"] | None
        td_type = Literal["exports", "imports"] | None
        strat_type = (
            Literal["balanced", "low_hanging_fruit", "long_jumps", "custom"] | None
        )
    elif variant == "all_relaxed":
        # All enums relaxed
        qt_type = str  # type: ignore[assignment]
        api_type = str | None  # type: ignore[assignment]
        pl_type = str | None  # type: ignore[assignment]
        pc_type = str | None  # type: ignore[assignment]
        lb_type = int | None  # type: ignore[assignment]
        sc_type = str | None  # type: ignore[assignment]
        td_type = str | None  # type: ignore[assignment]
        strat_type = str | None  # type: ignore[assignment]
    else:
        raise ValueError(f"Unknown variant: {variant}")

    fields: dict[str, Any] = {
        "reasoning": (
            str,
            Field(description="Step-by-step reasoning (max 300 chars)."),
        ),
        "query_type": (
            qt_type,
            Field(description="Query type classification."),
        ),
        "rejection_reason": (
            str | None,
            Field(default=None, description="Rejection reason."),
        ),
        "api_target": (
            api_type,
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
            pl_type,
            Field(default="fourDigit", description="Product digit level."),
        ),
        "product_class": (
            pc_type,
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
            lb_type,
            Field(default=None, description="Lookback years."),
        ),
        "services_class": (
            sc_type,
            Field(default=None, description="Services class."),
        ),
        "trade_direction": (
            td_type,
            Field(default=None, description="Trade direction."),
        ),
        "strategy": (
            strat_type,
            Field(default=None, description="Growth opportunity strategy."),
        ),
        "custom_weights_distance": (
            float | None,
            Field(default=None, description="Custom distance weight (0-1)."),
        ),
        "custom_weights_pci": (
            float | None,
            Field(default=None, description="Custom PCI weight (0-1)."),
        ),
        "custom_weights_og": (
            float | None,
            Field(default=None, description="Custom OG weight (0-1)."),
        ),
    }

    name_map = {
        "full_enums": "GraphQLPlanFullEnums",
        "relaxed_qt": "GraphQLPlanRelaxedQT",
        "all_relaxed": "GraphQLPlanAllRelaxed",
    }
    return create_model(name_map[variant], **fields)


# ---------------------------------------------------------------------------
# Test cases (reused from reasoning experiment)
# ---------------------------------------------------------------------------

TEST_CASES = [
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
        "question": "Which countries belong to the EU?",
        "expected": {
            "query_type": "group_membership",
            "api_target": "explore",
            "group_name": "EU",
            "group_type": "trade",
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
        "question": "What has been Brazil's ECI trend over the last 15 years?",
        "expected": {
            "query_type": "country_year",
            "api_target": "explore",
            "country_name": "Brazil",
            "country_code_guess": "BRA",
        },
    },
    {
        "question": "Show me low-hanging fruit opportunities for Kenya",
        "expected": {
            "query_type": "feasibility",
            "api_target": "explore",
            "country_name": "Kenya",
            "country_code_guess": "KEN",
            "strategy": "low_hanging_fruit",
        },
    },
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_result(result: dict, expected: dict) -> dict[str, bool]:
    """Score each expected field against the LLM result."""
    scores = {}
    for field, exp_val in expected.items():
        if field == "reasoning":
            continue
        actual = result.get(field)
        if isinstance(exp_val, str) and isinstance(actual, str):
            scores[field] = actual.lower() == exp_val.lower()
        else:
            scores[field] = actual == exp_val
    return scores


def check_query_type_valid(result: dict) -> bool:
    """Check if the returned query_type is in the valid set."""
    return result.get("query_type", "") in VALID_QUERY_TYPES


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_single_call(
    llm: Any,
    schema: type[BaseModel],
    prompt_text: str,
) -> dict:
    """Run a single structured output call with json_schema method."""
    tracker = TokenTracker()
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

    result_dict = result.model_dump() if result else {}
    return {
        "latency_ms": elapsed,
        "input_tokens": tracker.input_tokens,
        "output_tokens": tracker.output_tokens,
        "result": result_dict,
        "error": None,
    }


async def run_experiment(
    llm: Any,
    variants: dict[str, type[BaseModel]],
    test_cases: list[dict],
    repetitions: int = 5,
    concurrency: int = 10,
) -> dict[str, list[dict]]:
    """Run all test cases for all variants concurrently."""
    sem = asyncio.Semaphore(concurrency)
    total = len(test_cases) * repetitions * len(variants)
    done_count = 0
    lock = asyncio.Lock()

    async def run_one(
        variant: str,
        schema: type[BaseModel],
        tc_idx: int,
        tc: dict,
        rep: int,
    ) -> dict:
        nonlocal done_count
        prompt_text = build_query_plan_prompt(tc["question"])

        async with sem:
            call_result = await run_single_call(llm, schema, prompt_text)

        async with lock:
            done_count += 1
            logger.info(
                "  [%s/%s] %s | case %s | rep %s | %.0fms",
                done_count,
                total,
                variant,
                tc_idx,
                rep + 1,
                call_result["latency_ms"],
            )

        if call_result["error"]:
            field_scores = {}
            overall = 0.0
            qt_valid = False
        else:
            field_scores = score_result(call_result["result"], tc["expected"])
            overall = sum(field_scores.values()) / max(len(field_scores), 1)
            qt_valid = check_query_type_valid(call_result["result"])

        return {
            "variant": variant,
            "test_case_idx": tc_idx,
            "question": tc["question"],
            "repetition": rep,
            "latency_ms": call_result["latency_ms"],
            "input_tokens": call_result["input_tokens"],
            "output_tokens": call_result["output_tokens"],
            "field_accuracy": field_scores,
            "overall_accuracy": overall,
            "query_type_valid": qt_valid,
            "error": call_result["error"],
            "raw_result": call_result["result"],
        }

    # Build all tasks
    tasks = []
    for variant_name, schema in variants.items():
        for tc_idx, tc in enumerate(test_cases):
            for rep in range(repetitions):
                tasks.append(run_one(variant_name, schema, tc_idx, tc, rep))

    all_entries = await asyncio.gather(*tasks)

    results: dict[str, list[dict]] = {v: [] for v in variants}
    for entry in all_entries:
        v = entry.pop("variant")
        results[v].append(entry)

    return results


# ---------------------------------------------------------------------------
# Summary printing
# ---------------------------------------------------------------------------


def print_summary(results: dict[str, list[dict]]) -> None:
    """Print a comparison table across all variants."""
    logger.info("\n%s", "=" * 80)
    logger.info("  ENUM RELAXATION EXPERIMENT RESULTS")
    logger.info("%s", "=" * 80)
    logger.info(
        "%-16s | %8s | %11s | %11s | %14s | %10s",
        "Variant",
        "Accuracy",
        "Avg Latency",
        "Med Latency",
        "Avg Out Tokens",
        "QT Valid %",
    )
    logger.info(
        "%s-+-%s-+-%s-+-%s-+-%s-+-%s",
        "-" * 16,
        "-" * 8,
        "-" * 11,
        "-" * 11,
        "-" * 14,
        "-" * 10,
    )

    for variant in results:
        entries = results[variant]
        valid = [e for e in entries if not e["error"]]
        errors = len(entries) - len(valid)

        if valid:
            avg_acc = sum(e["overall_accuracy"] for e in valid) / len(valid)
            latencies = [e["latency_ms"] for e in valid]
            avg_lat = statistics.mean(latencies)
            med_lat = statistics.median(latencies)
            avg_tok = sum(e["output_tokens"] for e in valid) / len(valid)
            qt_valid_pct = sum(e["query_type_valid"] for e in valid) / len(valid)
        else:
            avg_acc = avg_lat = med_lat = avg_tok = qt_valid_pct = 0.0

        err_str = f" ({errors} err)" if errors else ""
        logger.info(
            "%-16s | %8s | %9sms | %9sms | %14s | %10s%s",
            variant,
            f"{avg_acc:.2%}",
            f"{avg_lat:.0f}",
            f"{med_lat:.0f}",
            f"{avg_tok:.0f}",
            f"{qt_valid_pct:.1%}",
            err_str,
        )

    # Latency comparison: per-test-case breakdown
    variant_names = list(results.keys())
    if len(variant_names) >= 2:
        logger.info("\nPer-test-case median latency (ms):")
        logger.info(
            "%-55s | %s",
            "Question",
            " | ".join(f"{v:>14}" for v in variant_names),
        )
        logger.info(
            "%s-+-%s",
            "-" * 55,
            "-+-".join("-" * 14 for _ in variant_names),
        )

        # Group by test case
        by_case: dict[int, dict[str, list[float]]] = {}
        for variant, entries in results.items():
            for e in entries:
                if not e["error"]:
                    by_case.setdefault(e["test_case_idx"], {}).setdefault(
                        variant, []
                    ).append(e["latency_ms"])

        for tc_idx in sorted(by_case.keys()):
            q = TEST_CASES[tc_idx]["question"][:52]
            medians = []
            for v in variant_names:
                lats = by_case[tc_idx].get(v, [])
                med = statistics.median(lats) if lats else 0
                medians.append(f"{med:>12.0f}ms")
            logger.info("%-55s | %s", q, " | ".join(medians))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enum relaxation latency experiment for GraphQLQueryPlan"
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=5,
        help="Repetitions per test case per variant (default 5)",
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
        "--concurrency",
        type=int,
        default=10,
        help="Max concurrent API calls (default 10)",
    )
    parser.add_argument(
        "--variants",
        type=str,
        default="full_enums,relaxed_qt,all_relaxed",
        help="Comma-separated variants to test (default: all three)",
    )
    args = parser.parse_args()

    variant_names = [v.strip() for v in args.variants.split(",")]
    schemas = {v: _make_schema(v) for v in variant_names}

    logger.info("Enum Relaxation Experiment")
    logger.info("Model: %s (%s)", args.model, args.provider)
    logger.info("Repetitions: %s", args.repetitions)
    logger.info("Concurrency: %s", args.concurrency)
    logger.info("Variants: %s", variant_names)
    logger.info("Test cases: %s", len(TEST_CASES))
    logger.info(
        "Total API calls: %s",
        len(TEST_CASES) * args.repetitions * len(variant_names),
    )

    # Log the JSON schema sizes for each variant
    for name, schema in schemas.items():
        json_schema = schema.model_json_schema()
        schema_str = json.dumps(json_schema)
        logger.info("  %s schema size: %s chars", name, len(schema_str))

    logger.info("")

    llm = create_llm(args.model, args.provider, temperature=0)

    results = await run_experiment(
        llm=llm,
        variants=schemas,
        test_cases=TEST_CASES,
        repetitions=args.repetitions,
        concurrency=args.concurrency,
    )

    print_summary(results)

    # Save results
    output_path = Path("scripts/enum_relaxation_results.json")
    serializable = {
        "metadata": {
            "model": args.model,
            "provider": args.provider,
            "repetitions": args.repetitions,
            "variants": variant_names,
            "test_case_count": len(TEST_CASES),
            "schema_sizes": {
                name: len(json.dumps(schema.model_json_schema()))
                for name, schema in schemas.items()
            },
        },
        "results": {
            variant: [{k: v for k, v in e.items()} for e in entries]
            for variant, entries in results.items()
        },
    }
    output_path.write_text(json.dumps(serializable, indent=2, default=str))
    logger.info("\nResults saved to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )
    asyncio.run(main())
