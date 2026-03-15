"""Build gold-standard test cases for product search evaluation.

Phase 1: Query real product catalogs from the Atlas DB to get verified codes.
Phase 2: Sample 80+ products across schemas and hierarchy levels.
Phase 3: Use a lightweight LLM to generate 3 entity variants per product
         (synonym, paraphrase, partial) — simulating LLM extraction outputs.
Phase 4: Add exact-match cases (query == official name) and curated cases
         for ambiguous terms, broad categories, compound phrases, misspellings,
         acronyms, hierarchical queries, and service sector names.

Test case sources:
  - exact_match: query == official product name (no LLM, trivially easy)
  - llm_generated: 3 variant types per product via LLM
  - curated: hand-written cases for categories LLM can't reliably produce

Outputs ~400+ test cases with cost estimation in metadata.

Usage:
    ATLAS_DB_URL=postgresql://... PYTHONPATH=$(pwd) uv run python \\
        evaluation/product_search/build_gold_standard.py

    # Or with the test DB:
    ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \\
        PYTHONPATH=$(pwd) uv run python \\
        evaluation/product_search/build_gold_standard.py

Outputs: evaluation/product_search/data/gold_standard.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy import create_engine, text

from src.model_config import DEFAULT_PRICING, MODEL_PRICING

# Load .env for API keys
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

SCHEMA_TO_TABLE = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
}

OUTPUT_PATH = Path(__file__).parent / "data" / "gold_standard.json"

# LLM configuration for variant generation
VARIANT_MODEL = "gpt-5-mini"
MAX_CONCURRENT_LLM_CALLS = 20


@dataclass
class ProductSearchTestCase:
    """A single gold-standard test case for product search evaluation."""

    query: str
    schema: str
    expected_codes: list[str]
    acceptable_codes: list[str] = field(default_factory=list)
    official_name: str = ""
    difficulty: str = "easy"
    notes: str = ""


# ---------------------------------------------------------------------------
# Phase 1: Fetch product catalogs
# ---------------------------------------------------------------------------


def fetch_all_products(engine, schema: str) -> dict[str, dict[str, str]]:
    """Fetch all products from a classification table.

    Returns:
        Dict keyed by product code, values are dicts with name, level, product_id.
    """
    table = SCHEMA_TO_TABLE.get(schema)
    if not table:
        return {}

    query = text(f"""
        SELECT code, name_short_en, product_level, product_id
        FROM {table}
        WHERE code IS NOT NULL AND name_short_en IS NOT NULL
        ORDER BY code
    """)

    products: dict[str, dict[str, str]] = {}
    with engine.connect() as conn:
        rows = conn.execute(query).fetchall()
        for code, name, level, pid in rows:
            products[str(code)] = {
                "code": str(code),
                "name": str(name),
                "level": str(level),
                "product_id": str(pid),
            }
    return products


# ---------------------------------------------------------------------------
# Phase 2: Strategic product sampling
# ---------------------------------------------------------------------------

# HS chapter groupings for diverse sampling
HS_SECTIONS = {
    "agriculture": range(1, 25),
    "minerals": range(25, 28),
    "chemicals": range(28, 39),
    "plastics_rubber": range(39, 41),
    "textiles": range(41, 64),
    "metals": range(72, 84),
    "machinery_electronics": range(84, 86),
    "transport": range(86, 90),
    "miscellaneous": range(90, 100),
}


def _get_hs_section(code: str) -> str:
    """Determine which HS section a product code belongs to."""
    try:
        chapter = int(code[:2])
    except (ValueError, IndexError):
        return "other"
    for section, chapters in HS_SECTIONS.items():
        if chapter in chapters:
            return section
    return "other"


def sample_products(
    products_by_schema: dict[str, dict[str, dict[str, str]]],
    target_count: int = 85,
    seed: int = 42,
) -> list[tuple[str, dict[str, str]]]:
    """Sample products across schemas, hierarchy levels, and HS sections.

    Returns list of (schema, product_info) tuples.
    """
    rng = random.Random(seed)
    sampled: list[tuple[str, dict[str, str]]] = []

    # --- HS12: ~55 products, stratified by section and code length ---
    hs12_catalog = products_by_schema.get("hs12", {})
    if hs12_catalog:
        # Group by section and code length
        section_level_groups: dict[str, dict[int, list[dict]]] = {}
        for product in hs12_catalog.values():
            section = _get_hs_section(product["code"])
            code_len = len(product["code"])
            section_level_groups.setdefault(section, {}).setdefault(
                code_len, []
            ).append(product)

        # Sample ~6 products per section, mixing hierarchy levels
        hs12_target = 55
        per_section = max(3, hs12_target // len(section_level_groups))

        for _section, levels in section_level_groups.items():
            section_pool: list[dict] = []
            # Prefer 4-digit codes (most common query target), some 2-digit and 6-digit
            for code_len in sorted(levels.keys()):
                pool = levels[code_len]
                if code_len <= 2:
                    n = max(1, per_section // 5)
                elif code_len <= 4:
                    n = max(1, per_section * 3 // 5)
                else:
                    n = max(1, per_section // 5)
                section_pool.extend(rng.sample(pool, min(n, len(pool))))

            # Trim to per-section target
            if len(section_pool) > per_section:
                section_pool = rng.sample(section_pool, per_section)

            for p in section_pool:
                sampled.append(("hs12", p))

    # --- HS92: ~12 products ---
    hs92_catalog = products_by_schema.get("hs92", {})
    if hs92_catalog:
        # Sample 4-digit codes primarily
        four_digit = [p for p in hs92_catalog.values() if len(p["code"]) == 4]
        two_digit = [p for p in hs92_catalog.values() if len(p["code"]) == 2]
        chosen = rng.sample(four_digit, min(9, len(four_digit)))
        chosen += rng.sample(two_digit, min(3, len(two_digit)))
        for p in chosen:
            sampled.append(("hs92", p))

    # --- SITC: ~10 products ---
    sitc_catalog = products_by_schema.get("sitc", {})
    if sitc_catalog:
        products = list(sitc_catalog.values())
        chosen = rng.sample(products, min(10, len(products)))
        for p in chosen:
            sampled.append(("sitc", p))

    # --- Services: ~8 products (only 16 total, take half) ---
    services_catalog = products_by_schema.get("services_unilateral", {})
    if services_catalog:
        products = list(services_catalog.values())
        chosen = rng.sample(products, min(8, len(products)))
        for p in chosen:
            sampled.append(("services_unilateral", p))

    logger.info(
        "Sampled %d products: hs12=%d, hs92=%d, sitc=%d, services=%d",
        len(sampled),
        sum(1 for s, _ in sampled if s == "hs12"),
        sum(1 for s, _ in sampled if s == "hs92"),
        sum(1 for s, _ in sampled if s == "sitc"),
        sum(1 for s, _ in sampled if s == "services_unilateral"),
    )

    return sampled


# ---------------------------------------------------------------------------
# Phase 3: LLM-generated query variants
# ---------------------------------------------------------------------------

VARIANT_GENERATION_PROMPT = """\
You are helping build a test dataset for a product code search engine used \
in international trade classification systems (HS, SITC, services).

Context: An upstream LLM extracts product entity names from user questions. \
For example, "What did Japan export in cars in 2020?" produces the extracted \
entity "cars". Your job is to generate realistic extracted entities for a \
given product.

Official product name: "{product_name}"
Product code: {product_code}
Classification system: {schema}

Generate 3 search query variants — each representing a plausible entity \
that an LLM would extract from a user's natural-language question:
1. SYNONYM: A common everyday name or synonym \
(e.g., "Motor cars" → "cars", "Petroleum oils, crude" → "crude oil")
2. PARAPHRASE: A different phrasing that does NOT share keywords with the \
official name (e.g., "Motor cars" → "passenger vehicles", \
"Footwear" → "shoes", "Rubber" → "elastomers")
3. PARTIAL: A partial, abbreviated, or informal term \
(e.g., "Salt, sulphur, lime, cement" → "cement", \
"Electrical machinery and equipment" → "electronics")

For each variant, rate the RETRIEVAL difficulty — how hard it is for a \
keyword-based search engine to find the correct product code:
- "easy": Query shares a stemmed keyword with the official name \
(e.g., "crude oil" for "Petroleum oils, crude" — "oil" overlaps)
- "medium": Query uses synonyms or abbreviations with NO keyword overlap \
(e.g., "cars" for "Motor cars" — a human knows it, but no shared keyword)
- "hard": Query requires domain expertise or significant semantic \
understanding to resolve (e.g., "elastomers" → Rubber)

Rules:
- Keep queries 1-4 words (realistic extracted entities, not full sentences)
- Think about what an LLM would extract, not what a user would type directly
- Each variant's query must be DIFFERENT from all others
- The paraphrase MUST NOT share any word with the official name

Respond in JSON:
{{"variants": [
    {{"query": "...", "type": "synonym", "difficulty": "..."}},
    {{"query": "...", "type": "paraphrase", "difficulty": "..."}},
    {{"query": "...", "type": "partial", "difficulty": "..."}}
]}}"""


async def generate_variants_for_product(
    client: AsyncOpenAI,
    product_name: str,
    product_code: str,
    schema: str,
    semaphore: asyncio.Semaphore,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Use LLM to generate user-query variants for a single product.

    Returns:
        Tuple of (variants, usage) where variants is a list of dicts with
        query/type/difficulty keys, and usage has input/output token counts.
    """
    prompt = VARIANT_GENERATION_PROMPT.format(
        product_name=product_name,
        product_code=product_code,
        schema=schema,
    )
    empty_usage: dict[str, int] = {"input": 0, "output": 0}

    async with semaphore:
        try:
            response = await client.chat.completions.create(
                model=VARIANT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            parsed = json.loads(content)

            usage: dict[str, int] = empty_usage
            if response.usage:
                usage = {
                    "input": response.usage.prompt_tokens,
                    "output": response.usage.completion_tokens,
                }

            return parsed.get("variants", []), usage
        except Exception as e:
            logger.warning(
                "LLM variant generation failed for %r (%s): %s",
                product_name,
                product_code,
                e,
            )
            return [], empty_usage


def find_related_codes(
    code: str,
    schema: str,
    products_by_schema: dict[str, dict[str, dict[str, str]]],
) -> list[str]:
    """Find related codes at parent/child hierarchy levels.

    For a 4-digit HS code, its 6-digit children and 2-digit parent
    are acceptable matches.
    """
    catalog = products_by_schema.get(schema, {})
    related: list[str] = []

    if schema.startswith("hs"):
        # Children: codes that start with this code
        if len(code) <= 4:
            for c in catalog:
                if c.startswith(code) and c != code:
                    related.append(c)

        # Parent: truncate to shorter code
        if len(code) >= 4:
            parent = code[:2]
            if parent in catalog and parent != code:
                related.append(parent)
        if len(code) >= 6:
            parent4 = code[:4]
            if parent4 in catalog and parent4 != code:
                related.append(parent4)

    elif schema == "sitc":
        # Similar hierarchy for SITC
        if len(code) <= 3:
            for c in catalog:
                if c.startswith(code) and c != code:
                    related.append(c)
        if len(code) >= 3:
            parent = code[:2]
            if parent in catalog and parent != code:
                related.append(parent)

    return related[:15]


def _build_exact_match_cases(
    sampled: list[tuple[str, dict[str, str]]],
    products_by_schema: dict[str, dict[str, dict[str, str]]],
) -> list[ProductSearchTestCase]:
    """Add exact-match test cases where query == official product name.

    These are trivially easy and require no LLM call. They cover the
    "exact match" category from the evaluation plan.
    """
    cases: list[ProductSearchTestCase] = []
    for schema, product in sampled:
        cases.append(
            ProductSearchTestCase(
                query=product["name"],
                schema=schema,
                expected_codes=[product["code"]],
                acceptable_codes=find_related_codes(
                    product["code"], schema, products_by_schema
                ),
                official_name=product["name"],
                difficulty="easy",
                notes=f"Exact match for '{product['name']}' [{product['code']}]",
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Curated test cases for categories LLM generation can't reliably produce
# ---------------------------------------------------------------------------

# Each entry: (query, schema, candidate_codes, difficulty, notes)
# candidate_codes are verified against the real catalog at runtime.
CURATED_TEST_CASES: list[dict] = [
    # =====================================================================
    # Ambiguous terms (same query, multiple valid interpretations)
    # =====================================================================
    {
        "query": "chips",
        "schema": "hs12",
        "candidate_codes": ["8542", "2005"],
        "difficulty": "hard",
        "notes": "Ambiguous: semiconductors (8542) vs potato chips (2005)",
    },
    {
        "query": "nuts",
        "schema": "hs12",
        "candidate_codes": ["0802", "7318"],
        "difficulty": "hard",
        "notes": "Ambiguous: edible nuts (0802) vs metal nuts/bolts (7318)",
    },
    {
        "query": "tanks",
        "schema": "hs12",
        "candidate_codes": ["8710", "7309", "7311"],
        "difficulty": "hard",
        "notes": "Ambiguous: military tanks (8710) vs storage tanks (7309/7311)",
    },
    {
        "query": "tablets",
        "schema": "hs12",
        "candidate_codes": ["8471", "3004"],
        "difficulty": "hard",
        "notes": "Ambiguous: tablet computers (8471) vs medicament tablets (3004)",
    },
    # =====================================================================
    # Broad categories (chapter-level queries)
    # =====================================================================
    {
        "query": "fruits",
        "schema": "hs12",
        "candidate_codes": ["08"],
        "difficulty": "medium",
        "notes": "Broad category: entire chapter 08 (edible fruit)",
    },
    {
        "query": "clothing",
        "schema": "hs12",
        "candidate_codes": ["61", "62"],
        "difficulty": "medium",
        "notes": "Broad category: knitted (61) + woven (62) clothing",
    },
    {
        "query": "electronics",
        "schema": "hs12",
        "candidate_codes": ["85"],
        "difficulty": "medium",
        "notes": "Broad category: chapter 85 (electrical machinery)",
    },
    {
        "query": "weapons",
        "schema": "hs12",
        "candidate_codes": ["93"],
        "difficulty": "medium",
        "notes": "Broad category: arms and ammunition chapter",
    },
    {
        "query": "machinery",
        "schema": "hs12",
        "candidate_codes": ["84"],
        "difficulty": "medium",
        "notes": "Broad category: chapter 84 (nuclear reactors, boilers, machinery)",
    },
    {
        "query": "chemicals",
        "schema": "hs12",
        "candidate_codes": ["28", "29"],
        "difficulty": "medium",
        "notes": "Broad category: inorganic (28) + organic (29) chemicals",
    },
    {
        "query": "textiles",
        "schema": "hs12",
        "candidate_codes": ["50", "51", "52", "53"],
        "difficulty": "medium",
        "notes": "Broad category: textile fibers chapters (silk, wool, cotton, etc.)",
    },
    {
        "query": "beverages",
        "schema": "hs12",
        "candidate_codes": ["22"],
        "difficulty": "medium",
        "notes": "Broad category: chapter 22 (beverages, spirits, vinegar)",
    },
    {
        "query": "vehicles",
        "schema": "hs12",
        "candidate_codes": ["87"],
        "difficulty": "easy",
        "notes": "Broad category: chapter 87 (vehicles other than railway)",
    },
    {
        "query": "furniture",
        "schema": "hs12",
        "candidate_codes": ["94"],
        "difficulty": "easy",
        "notes": "Broad category: chapter 94 (furniture, lighting, prefab buildings)",
    },
    # =====================================================================
    # Compound product phrases (realistic LLM extractions)
    # =====================================================================
    {
        "query": "crude petroleum",
        "schema": "hs12",
        "candidate_codes": ["2709"],
        "difficulty": "easy",
        "notes": "Compound: crude petroleum oil",
    },
    {
        "query": "palm oil",
        "schema": "hs12",
        "candidate_codes": ["1511"],
        "difficulty": "easy",
        "notes": "Compound: palm oil",
    },
    {
        "query": "frozen fish",
        "schema": "hs12",
        "candidate_codes": ["0303"],
        "difficulty": "easy",
        "notes": "Compound: frozen fish",
    },
    {
        "query": "iron ore",
        "schema": "hs12",
        "candidate_codes": ["2601"],
        "difficulty": "easy",
        "notes": "Compound: iron ore and concentrates",
    },
    {
        "query": "natural gas",
        "schema": "hs12",
        "candidate_codes": ["2711"],
        "difficulty": "easy",
        "notes": "Compound: petroleum gases and gaseous hydrocarbons",
    },
    {
        "query": "soy beans",
        "schema": "hs12",
        "candidate_codes": ["1201"],
        "difficulty": "easy",
        "notes": "Compound: soya beans",
    },
    {
        "query": "copper wire",
        "schema": "hs12",
        "candidate_codes": ["7408"],
        "difficulty": "easy",
        "notes": "Compound: copper wire",
    },
    {
        "query": "passenger vehicles",
        "schema": "hs12",
        "candidate_codes": ["8703"],
        "difficulty": "medium",
        "notes": "Compound: motor cars for transport of persons",
    },
    {
        "query": "printed circuits",
        "schema": "hs12",
        "candidate_codes": ["8534"],
        "difficulty": "easy",
        "notes": "Compound: printed circuits",
    },
    {
        "query": "cell phones",
        "schema": "hs12",
        "candidate_codes": ["8517"],
        "difficulty": "medium",
        "notes": "Compound: telephones for cellular networks",
    },
    {
        "query": "medical instruments",
        "schema": "hs12",
        "candidate_codes": ["9018"],
        "difficulty": "medium",
        "notes": "Compound: medical/surgical instruments and apparatus",
    },
    {
        "query": "solar panels",
        "schema": "hs12",
        "candidate_codes": ["8541"],
        "difficulty": "medium",
        "notes": "Compound: photovoltaic cells → semiconductor devices",
    },
    # =====================================================================
    # Hierarchical queries (specific item → heading)
    # =====================================================================
    {
        "query": "salmon",
        "schema": "hs12",
        "candidate_codes": ["0302", "0303", "0304", "03"],
        "difficulty": "medium",
        "notes": "Hierarchical: specific fish → fish chapter codes",
    },
    {
        "query": "whiskey",
        "schema": "hs12",
        "candidate_codes": ["2208"],
        "difficulty": "medium",
        "notes": "Hierarchical: specific spirit → spirits heading",
    },
    {
        "query": "cotton t-shirts",
        "schema": "hs12",
        "candidate_codes": ["6109"],
        "difficulty": "medium",
        "notes": "Hierarchical: specific garment combining material + type",
    },
    {
        "query": "lithium batteries",
        "schema": "hs12",
        "candidate_codes": ["8506", "8507"],
        "difficulty": "medium",
        "notes": "Hierarchical: specific battery type → battery headings",
    },
    # =====================================================================
    # Selected misspellings (realistic LLM extraction errors)
    # =====================================================================
    {
        "query": "livestok",
        "schema": "hs12",
        "candidate_codes": ["01"],
        "difficulty": "medium",
        "notes": "Misspelling: livestock → live animals",
    },
    {
        "query": "sulfer",
        "schema": "hs12",
        "candidate_codes": ["25"],
        "difficulty": "easy",
        "notes": "Misspelling: sulfer → sulphur (common US/UK variant)",
    },
    {
        "query": "alumnium",
        "schema": "hs12",
        "candidate_codes": ["76"],
        "difficulty": "easy",
        "notes": "Misspelling: alumnium → aluminium",
    },
    {
        "query": "petrolium",
        "schema": "hs12",
        "candidate_codes": ["2709", "2710"],
        "difficulty": "easy",
        "notes": "Misspelling: petrolium → petroleum",
    },
    {
        "query": "pharmceuticals",
        "schema": "hs12",
        "candidate_codes": ["30"],
        "difficulty": "easy",
        "notes": "Misspelling: pharmceuticals → pharmaceuticals",
    },
    {
        "query": "semi-conductors",
        "schema": "hs12",
        "candidate_codes": ["8541", "8542"],
        "difficulty": "easy",
        "notes": "Misspelling: hyphenated variant of semiconductors",
    },
    {
        "query": "automoblies",
        "schema": "hs12",
        "candidate_codes": ["8703"],
        "difficulty": "medium",
        "notes": "Misspelling: automoblies → automobiles",
    },
    {
        "query": "fertlizer",
        "schema": "hs12",
        "candidate_codes": ["31"],
        "difficulty": "easy",
        "notes": "Misspelling: fertlizer → fertilizer",
    },
    # =====================================================================
    # Acronyms and domain jargon (plausible LLM pass-throughs)
    # =====================================================================
    {
        "query": "LNG",
        "schema": "hs12",
        "candidate_codes": ["2711"],
        "difficulty": "hard",
        "notes": "Acronym: liquefied natural gas",
    },
    {
        "query": "LPG",
        "schema": "hs12",
        "candidate_codes": ["2711"],
        "difficulty": "hard",
        "notes": "Acronym: liquefied petroleum gas",
    },
    {
        "query": "PKO",
        "schema": "sitc",
        "candidate_codes": ["4244"],
        "difficulty": "hard",
        "notes": "Acronym: palm kernel oil",
    },
    {
        "query": "PVC",
        "schema": "sitc",
        "candidate_codes": ["5834"],
        "difficulty": "hard",
        "notes": "Acronym: polyvinyl chloride",
    },
    {
        "query": "elastomers",
        "schema": "hs12",
        "candidate_codes": ["40"],
        "difficulty": "hard",
        "notes": "Technical synonym: elastomers → rubber",
    },
    {
        "query": "H2SO4",
        "schema": "hs12",
        "candidate_codes": ["2807"],
        "difficulty": "hard",
        "notes": "Chemical formula: sulfuric acid",
    },
    {
        "query": "athleisure",
        "schema": "hs12",
        "candidate_codes": ["6112"],
        "difficulty": "hard",
        "notes": "Industry jargon: athleisure → activewear/sportswear",
    },
    {
        "query": "HS code for cars",
        "schema": "hs12",
        "candidate_codes": ["8703"],
        "difficulty": "easy",
        "notes": "HS code query format: cars",
    },
    {
        "query": "HS code for diamonds",
        "schema": "hs12",
        "candidate_codes": ["7102"],
        "difficulty": "easy",
        "notes": "HS code query format: diamonds",
    },
    # =====================================================================
    # Service sector names (how users refer to them vs catalog names)
    # =====================================================================
    {
        "query": "consulting",
        "schema": "services_unilateral",
        "candidate_codes": ["ict"],
        "difficulty": "medium",
        "notes": "Services: consulting → business/ICT services",
    },
    {
        "query": "tourism",
        "schema": "services_unilateral",
        "candidate_codes": ["travel"],
        "difficulty": "medium",
        "notes": "Services: tourism → travel services",
    },
    {
        "query": "freight",
        "schema": "services_unilateral",
        "candidate_codes": ["transport"],
        "difficulty": "medium",
        "notes": "Services: freight → transport services",
    },
    {
        "query": "IT services",
        "schema": "services_unilateral",
        "candidate_codes": ["ict"],
        "difficulty": "medium",
        "notes": "Services: IT services → ICT/business services",
    },
    {
        "query": "financial services",
        "schema": "services_unilateral",
        "candidate_codes": ["financial"],
        "difficulty": "easy",
        "notes": "Services: financial services → insurance & finance",
    },
    {
        "query": "shipping",
        "schema": "services_unilateral",
        "candidate_codes": ["transport"],
        "difficulty": "medium",
        "notes": "Services: shipping → transport services",
    },
    {
        "query": "banking",
        "schema": "services_unilateral",
        "candidate_codes": ["financial"],
        "difficulty": "medium",
        "notes": "Services: banking → insurance & finance",
    },
    {
        "query": "hospitality",
        "schema": "services_unilateral",
        "candidate_codes": ["travel"],
        "difficulty": "medium",
        "notes": "Services: hospitality → travel & tourism",
    },
    {
        "query": "software",
        "schema": "services_unilateral",
        "candidate_codes": ["ict"],
        "difficulty": "medium",
        "notes": "Services: software → ICT/business services",
    },
    {
        "query": "insurance",
        "schema": "services_unilateral",
        "candidate_codes": ["financial"],
        "difficulty": "easy",
        "notes": "Services: insurance → insurance & finance",
    },
]


def _build_curated_cases(
    products_by_schema: dict[str, dict[str, dict[str, str]]],
) -> list[ProductSearchTestCase]:
    """Build curated test cases, verifying codes against real catalog.

    For ambiguous cases, ALL candidate_codes that exist in the catalog become
    expected_codes (the search should surface all valid interpretations).
    """
    cases: list[ProductSearchTestCase] = []
    skipped = 0

    for entry in CURATED_TEST_CASES:
        catalog = products_by_schema.get(entry["schema"], {})
        # Keep only codes that actually exist in this catalog
        verified_codes = [c for c in entry["candidate_codes"] if c in catalog]

        if not verified_codes:
            logger.warning(
                "Skipping curated case %r: no candidate codes found in %s",
                entry["query"],
                entry["schema"],
            )
            skipped += 1
            continue

        cases.append(
            ProductSearchTestCase(
                query=entry["query"],
                schema=entry["schema"],
                expected_codes=verified_codes,
                acceptable_codes=find_related_codes(
                    verified_codes[0], entry["schema"], products_by_schema
                ),
                official_name=", ".join(
                    catalog[c]["name"] for c in verified_codes if c in catalog
                ),
                difficulty=entry["difficulty"],
                notes=f"Curated: {entry['notes']}",
            )
        )

    logger.info(
        "Built %d curated test cases (%d skipped — codes not in catalog)",
        len(cases),
        skipped,
    )
    return cases


async def build_gold_standard(
    products_by_schema: dict[str, dict[str, dict[str, str]]],
    target_products: int = 85,
) -> tuple[list[ProductSearchTestCase], dict]:
    """Build gold-standard test cases with LLM-generated query variants.

    Args:
        products_by_schema: Real catalog data fetched from Atlas DB.
        target_products: Number of products to sample.

    Returns:
        Tuple of (test_cases, cost_info) where cost_info has token counts
        and estimated cost.
    """
    client = AsyncOpenAI()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_LLM_CALLS)

    # Sample products
    sampled = sample_products(products_by_schema, target_products)

    # --- Exact-match cases (no LLM needed) ---
    exact_cases = _build_exact_match_cases(sampled, products_by_schema)
    logger.info("Added %d exact-match test cases", len(exact_cases))

    # --- Curated cases (no LLM needed) ---
    curated_cases = _build_curated_cases(products_by_schema)

    # --- LLM-generated variants ---
    logger.info(
        "Generating LLM variants for %d products (model=%s)...",
        len(sampled),
        VARIANT_MODEL,
    )
    tasks = [
        generate_variants_for_product(
            client, product["name"], product["code"], schema, semaphore
        )
        for schema, product in sampled
    ]
    all_results = await asyncio.gather(*tasks)

    # Separate variants and usage from results
    all_variants = [r[0] for r in all_results]
    all_usages = [r[1] for r in all_results]

    # Compute cost using MODEL_PRICING from model_config
    total_input_tokens = sum(u.get("input", 0) for u in all_usages)
    total_output_tokens = sum(u.get("output", 0) for u in all_usages)
    llm_calls = sum(1 for u in all_usages if u.get("input", 0) > 0)

    pricing = MODEL_PRICING.get(VARIANT_MODEL, DEFAULT_PRICING)
    input_cost = total_input_tokens * pricing.input / 1_000_000
    output_cost = total_output_tokens * pricing.output / 1_000_000
    total_cost = input_cost + output_cost

    cost_info = {
        "model": VARIANT_MODEL,
        "llm_calls": llm_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "estimated_cost_usd": round(total_cost, 6),
        "pricing_per_1m_tokens": {"input": pricing.input, "output": pricing.output},
    }
    logger.info(
        "LLM cost: %d calls, %d input tokens, %d output tokens, $%.6f",
        llm_calls,
        total_input_tokens,
        total_output_tokens,
        total_cost,
    )

    # Build LLM-generated test cases
    llm_cases: list[ProductSearchTestCase] = []
    products_with_variants = 0

    for (schema, product), variants in zip(sampled, all_variants):
        if not variants:
            continue

        products_with_variants += 1
        expected_codes = [product["code"]]
        acceptable_codes = find_related_codes(
            product["code"], schema, products_by_schema
        )

        for variant in variants:
            query = variant.get("query", "").strip()
            if not query:
                continue

            llm_cases.append(
                ProductSearchTestCase(
                    query=query,
                    schema=schema,
                    expected_codes=expected_codes,
                    acceptable_codes=acceptable_codes,
                    official_name=product["name"],
                    difficulty=variant.get("difficulty", "medium"),
                    notes=(
                        f"LLM-generated {variant.get('type', 'variant')} "
                        f"for '{product['name']}' [{product['code']}]"
                    ),
                )
            )

    logger.info(
        "Generated %d LLM test cases from %d products (%d had variants)",
        len(llm_cases),
        len(sampled),
        products_with_variants,
    )

    # Combine all sources
    test_cases = exact_cases + llm_cases + curated_cases
    return test_cases, cost_info


# ---------------------------------------------------------------------------
# Verification & output
# ---------------------------------------------------------------------------


def deduplicate_cases(
    test_cases: list[ProductSearchTestCase],
) -> list[ProductSearchTestCase]:
    """Remove duplicate (query, schema) pairs, keeping the first occurrence.

    Duplicates arise when the same product appears in multiple schemas
    (e.g., hs12 and hs92) and the LLM generates the same query text.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[ProductSearchTestCase] = []
    dupes = 0

    for tc in test_cases:
        key = (tc.query.lower().strip(), tc.schema)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(tc)

    if dupes:
        logger.info("Removed %d duplicate (query, schema) pairs", dupes)
    return unique


def verify_codes(
    test_cases: list[ProductSearchTestCase],
    products_by_schema: dict[str, dict[str, dict[str, str]]],
) -> list[str]:
    """Verify all expected and acceptable codes exist in the catalog.

    Returns list of error messages (empty if all valid).
    """
    errors: list[str] = []
    for tc in test_cases:
        catalog = products_by_schema.get(tc.schema, {})
        for code in tc.expected_codes + tc.acceptable_codes:
            if code not in catalog:
                errors.append(
                    f"Code {code!r} not in {tc.schema} catalog (query={tc.query!r})"
                )
    return errors


async def async_main() -> None:
    """Build and save gold-standard test cases."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    db_url = os.environ.get("ATLAS_DB_URL")
    if not db_url:
        logger.error(
            "ATLAS_DB_URL not set. Run with:\n"
            "  ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test "
            "PYTHONPATH=$(pwd) uv run python "
            "evaluation/product_search/build_gold_standard.py"
        )
        sys.exit(1)

    engine = create_engine(db_url)

    # Phase 1: Fetch real product catalogs
    logger.info("Fetching product catalogs from Atlas DB...")
    products_by_schema: dict[str, dict[str, dict[str, str]]] = {}
    for schema in SCHEMA_TO_TABLE:
        products = fetch_all_products(engine, schema)
        products_by_schema[schema] = products
        logger.info("  %s: %d products", schema, len(products))

    # Phase 2+3: Sample products, generate LLM variants, add curated cases
    test_cases, cost_info = await build_gold_standard(
        products_by_schema, target_products=85
    )

    if not test_cases:
        logger.error("No test cases generated. Check LLM API key and connectivity.")
        sys.exit(1)

    # Deduplicate
    test_cases = deduplicate_cases(test_cases)

    # Verify all codes
    errors = verify_codes(test_cases, products_by_schema)
    if errors:
        for err in errors[:20]:
            logger.error("  %s", err)
        if len(errors) > 20:
            logger.error("  ... and %d more errors", len(errors) - 20)
        logger.error("Fix the above errors before proceeding.")
        sys.exit(1)

    # Compute distributions
    difficulty_dist: dict[str, int] = {}
    schema_dist: dict[str, int] = {}
    source_dist: dict[str, int] = {}
    variant_type_dist: dict[str, int] = {}
    for tc in test_cases:
        difficulty_dist[tc.difficulty] = difficulty_dist.get(tc.difficulty, 0) + 1
        schema_dist[tc.schema] = schema_dist.get(tc.schema, 0) + 1

        # Classify source
        if tc.notes.startswith("Exact match"):
            source_dist["exact_match"] = source_dist.get("exact_match", 0) + 1
        elif tc.notes.startswith("Curated"):
            source_dist["curated"] = source_dist.get("curated", 0) + 1
        else:
            source_dist["llm_generated"] = source_dist.get("llm_generated", 0) + 1

        # Extract variant type from notes
        for vtype in [
            "synonym",
            "paraphrase",
            "partial",
            "misspelling",
            "technical",
            "exact_match",
            "curated",
        ]:
            if vtype in tc.notes.lower() or (
                vtype == "exact_match" and tc.notes.startswith("Exact")
            ):
                variant_type_dist[vtype] = variant_type_dist.get(vtype, 0) + 1
                break

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "metadata": {
            "description": "Gold-standard test cases for product search evaluation",
            "generation_method": (
                f"LLM-generated variants ({VARIANT_MODEL}) + exact matches "
                f"+ curated ambiguous/domain cases"
            ),
            "total_cases": len(test_cases),
            "unique_products": len(
                {(tc.schema, tc.expected_codes[0]) for tc in test_cases}
            ),
            "schemas": list(SCHEMA_TO_TABLE.keys()),
            "difficulty_distribution": difficulty_dist,
            "schema_distribution": schema_dist,
            "source_distribution": source_dist,
            "variant_type_distribution": variant_type_dist,
            "cost": cost_info,
        },
        "test_cases": [asdict(tc) for tc in test_cases],
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    logger.info(
        "Saved %d test cases (%d unique products) to %s",
        len(test_cases),
        output["metadata"]["unique_products"],
        OUTPUT_PATH,
    )
    logger.info("Difficulty: %s", difficulty_dist)
    logger.info("Schema: %s", schema_dist)
    logger.info("Source: %s", source_dist)
    logger.info("Variant types: %s", variant_type_dist)
    logger.info(
        "Cost: $%.6f (%d calls, %d input tokens, %d output tokens)",
        cost_info["estimated_cost_usd"],
        cost_info["llm_calls"],
        cost_info["total_input_tokens"],
        cost_info["total_output_tokens"],
    )


if __name__ == "__main__":
    asyncio.run(async_main())
