"""Shared product enrichment module for synonym generation.

Generates synonym-enriched text for product names using LLM calls.
Each product gets 3-5 common synonyms appended after a pipe separator::

    ``Motor cars and other motor vehicles | cars automobiles sedans SUVs``

Used by both the production index builder
(``scripts/build_product_search_index.py``) and the evaluation framework
(``evaluation/product_search/eval.py``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# LLM settings for synonym generation
ENRICHMENT_MODEL = "gpt-5-nano"
ENRICHMENT_BATCH_SIZE = 50
ENRICHMENT_CONCURRENCY = 20


async def build_enrichment(
    catalogs: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, str]]:
    """Build enriched text for each product: name + LLM-generated synonyms.

    Uses async batched LLM calls for synonym generation.  Each product gets
    3-5 common synonyms/aliases appended after a pipe separator:

        ``Motor cars and other motor vehicles | cars automobiles sedans SUVs``

    Args:
        catalogs: Product catalogs keyed by schema. Each entry must have
            ``product_code`` and ``product_name`` keys.

    Returns:
        ``{schema: {product_code: enriched_text}}``
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    semaphore = asyncio.Semaphore(ENRICHMENT_CONCURRENCY)

    enrichment: dict[str, dict[str, str]] = {}

    for schema, entries in catalogs.items():
        enrichment[schema] = {}

        # Build batches of products
        batches: list[list[dict[str, Any]]] = []
        for i in range(0, len(entries), ENRICHMENT_BATCH_SIZE):
            batches.append(entries[i : i + ENRICHMENT_BATCH_SIZE])

        async def _process_batch(
            batch: list[dict[str, Any]], schema_name: str
        ) -> dict[str, str]:
            """Generate synonyms for a batch of products via LLM."""
            product_lines = "\n".join(
                f"- {p['product_code']}: {p['product_name']}" for p in batch
            )
            prompt = (
                f"For each product below from the {schema_name} trade classification, "
                f"generate 3-5 common synonyms or alternative names that a user might "
                f'search for. Return JSON: {{"synonyms": {{"<code>": "synonym1 synonym2 synonym3", ...}}}}\n\n'
                f"Products:\n{product_lines}"
            )

            async with semaphore:
                try:
                    response = await client.chat.completions.create(
                        model=ENRICHMENT_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"},
                    )
                    content = response.choices[0].message.content
                    parsed = json.loads(content)
                    synonyms_map = parsed.get("synonyms", {})

                    result: dict[str, str] = {}
                    for p in batch:
                        code = p["product_code"]
                        name = p["product_name"]
                        syns = synonyms_map.get(code, "")
                        if syns:
                            result[code] = f"{name} | {syns}"
                        else:
                            result[code] = name
                    return result
                except Exception as e:
                    logger.warning(
                        "Enrichment LLM call failed for %s batch: %s",
                        schema_name,
                        e,
                    )
                    return {p["product_code"]: p["product_name"] for p in batch}

        # Run all batches concurrently with progress tracking
        from tqdm.asyncio import tqdm as atqdm

        tasks = [_process_batch(batch, schema) for batch in batches]
        batch_results = await atqdm.gather(
            *tasks,
            desc=f"Enriching {schema}",
            unit="batch",
        )

        for batch_result in batch_results:
            enrichment[schema].update(batch_result)

    return enrichment


def load_enrichment_cache(cache_path: Path) -> dict[str, dict[str, str]] | None:
    """Load enrichment cache from disk if it exists."""
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


def save_enrichment_cache(
    enrichment: dict[str, dict[str, str]], cache_path: Path
) -> None:
    """Save enrichment cache to disk."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(enrichment, f, indent=2)
    logger.info("Saved enrichment cache to %s", cache_path)
