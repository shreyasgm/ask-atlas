#!/usr/bin/env python3
"""
Layer 1: GraphQL API collection script for Atlas Explore page eval data.

Queries the Atlas Explore GraphQL API for product-level, bilateral,
time-series, and regional data points that country pages don't cover.
IDs start at 170 (1-169 are existing questions).

Usage:
    uv run python evaluation/collect_explore_page_data.py
    uv run python evaluation/collect_explore_page_data.py --product-class HS92
    uv run python evaluation/collect_explore_page_data.py --questions 170 171
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENDPOINT = "https://atlas.hks.harvard.edu/api/graphql"
BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"
TIMESTAMP = datetime.now(timezone.utc).isoformat()

# Countries — integer IDs used by the Explore API
COUNTRIES: dict[str, int] = {
    "Kenya": 404,
    "Spain": 724,
    "Brazil": 76,
    "Germany": 276,
    "India": 356,
    "USA": 840,
    "Turkiye": 792,
    "Ethiopia": 231,
    "China": 156,  # bilateral partner only
}

# Products: display name → HS92 4-digit code (product IDs resolved at runtime)
PRODUCT_CODES: dict[str, str] = {
    "Coffee": "0901",
    "Cars": "8703",
    "Petroleum": "2710",
    "Electronic integrated circuits": "8542",
    "Medicaments": "3004",
    "T-shirts": "6109",
    "Iron ores": "2601",
}

BILATERAL_PAIRS: list[tuple[str, str]] = [
    ("Brazil", "China"),
    ("Kenya", "USA"),
    ("Germany", "USA"),
    ("India", "China"),
    ("Turkiye", "Germany"),
]

# Group API names → display names for questions
TARGET_GROUPS: dict[str, str] = {
    "Sub-Saharan Africa": "Sub-Saharan Africa",
    "East Asia & Pacific": "East Asia & Pacific",
    "European Union": "the European Union",
    "low": "Low Income countries",
}

PRODUCT_CLASS = "HS12"  # overridden by --product-class CLI arg
QUESTION_FILTER: set[int] | None = None  # overridden by --questions CLI arg

RATE_DELAY = 0.5  # seconds between requests

# Runtime-populated catalogs
PRODUCT_MAP: dict[str, dict] = {}  # HS code → product info
PRODUCT_ID_MAP: dict[str, dict] = {}  # string productId ("product-HS92-726") → info
COUNTRY_NAMES: dict[str, str] = {}  # string countryId ("country-404") → name
GROUP_MAP: dict[str, dict] = {}  # groupName → group info
DATA_AVAILABILITY: list[dict] = []

# Phase 2 data stores
country_product_data: dict[str, list[dict]] = {}
product_exporters: dict[str, list[dict]] = {}
all_product_year: dict[str, dict] = {}  # string productId → ProductYear row
bilateral_total: dict[tuple[str, str], dict] = {}
bilateral_products: dict[tuple[str, str], list[dict]] = {}
time_series: dict[str, list[dict]] = {}
conversion_result: list[dict] = []
import_sources: dict[tuple[str, str], list[dict]] = {}
group_year_data: dict[str, list[dict]] = {}  # api_name → list of GroupYear rows

# Semaphore — set inside main() for Python 3.12 compatibility
SEM: asyncio.Semaphore

# ---------------------------------------------------------------------------
# GraphQL Queries
# ---------------------------------------------------------------------------

_CATALOG_QUERY_NAMES = {
    "HS92": "productHs92",
    "HS12": "productHs12",
    "HS22": "productHs22",
    "SITC": "productSitc",
}


def product_catalog_query() -> str:
    """Return the product catalog query for the active PRODUCT_CLASS."""
    qname = _CATALOG_QUERY_NAMES[PRODUCT_CLASS]
    return f"""
query ProductCatalog {{
  {qname}(productLevel: 4) {{
    productId code
    nameEn nameShortEn
    productType
    naturalResource greenProduct
  }}
}}
"""


LOCATION_COUNTRY_QUERY = """
query LocationCountry {
  locationCountry {
    countryId
    nameEn nameShortEn
    iso3Code
    incomelevelEnum
  }
}
"""

LOCATION_GROUP_QUERY = """
query LocationGroup($groupType: GroupType!) {
  locationGroup(groupType: $groupType) {
    groupId groupName groupType
    members
    exportValueSum
    exportValueCagr5
    exportValueNonOilCagr5
  }
}
"""

DATA_AVAILABILITY_QUERY = """
query DataAvailability {
  dataAvailability {
    productClassification
    yearMin yearMax
  }
}
"""


def country_product_year_query() -> str:
    """All products for a given country (one year)."""
    return f"""
query CountryProductYear($countryId: Int!, $yearMin: Int!, $yearMax: Int!) {{
  countryProductYear(
    productClass: {PRODUCT_CLASS},
    productLevel: 4,
    countryId: $countryId,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    countryId productId year
    exportValue importValue globalMarketShare
    exportRca distance cog
    normalizedPci productStatus
  }}
}}
"""


def product_all_countries_query() -> str:
    """All countries for a given product (for top-exporter questions)."""
    return f"""
query ProductAllCountries($productId: Int!, $yearMin: Int!, $yearMax: Int!) {{
  countryProductYear(
    productClass: {PRODUCT_CLASS},
    productLevel: 4,
    productId: $productId,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    countryId productId year
    exportValue
  }}
}}
"""


def product_year_all_query() -> str:
    """Global product stats (all products, one year)."""
    return f"""
query ProductYearAll($yearMin: Int!, $yearMax: Int!) {{
  productYear(
    productClass: {PRODUCT_CLASS},
    productLevel: 4,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    productId year
    exportValue importValue
    pci complexityEnum
    exportValueConstCagr5
  }}
}}
"""


def country_country_year_query() -> str:
    return f"""
query CountryCountryYear(
  $countryId: Int!, $partnerCountryId: Int!,
  $yearMin: Int!, $yearMax: Int!
) {{
  countryCountryYear(
    productClass: {PRODUCT_CLASS},
    countryId: $countryId,
    partnerCountryId: $partnerCountryId,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    countryId partnerCountryId year
    exportValue importValue
  }}
}}
"""


def country_country_product_year_query() -> str:
    return f"""
query CountryCountryProductYear(
  $countryId: Int!, $partnerCountryId: Int!,
  $yearMin: Int!, $yearMax: Int!
) {{
  countryCountryProductYear(
    countryId: $countryId,
    partnerCountryId: $partnerCountryId,
    productClass: {PRODUCT_CLASS},
    productLevel: 4,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    countryId partnerCountryId productId year
    exportValue importValue
  }}
}}
"""


def country_year_query() -> str:
    return f"""
query CountryYear($countryId: Int!, $yearMin: Int!, $yearMax: Int!) {{
  countryYear(
    countryId: $countryId,
    productClass: {PRODUCT_CLASS},
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    countryId year
    exportValue importValue
    gdppc eci
    population
  }}
}}
"""


def import_sources_query() -> str:
    return f"""
query ImportSources(
  $countryId: Int!, $productId: Int!,
  $yearMin: Int!, $yearMax: Int!
) {{
  countryCountryProductYear(
    countryId: $countryId,
    productClass: {PRODUCT_CLASS},
    productLevel: 4,
    productId: $productId,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    partnerCountryId year
    importValue
  }}
}}
"""


def group_year_query() -> str:
    return f"""
query GroupYear($groupId: Int!, $yearMin: Int!, $yearMax: Int!) {{
  groupYear(
    productClass: {PRODUCT_CLASS},
    groupId: $groupId,
    yearMin: $yearMin,
    yearMax: $yearMax
  ) {{
    groupId year exportValue importValue
  }}
}}
"""


CONVERSION_PATH_QUERY = """
query ConversionPath(
  $sourceCode: String!,
  $sourceClassification: ClassificationEnum!,
  $targetClassification: ClassificationEnum!
) {
  conversionPath(
    sourceCode: $sourceCode,
    sourceClassification: $sourceClassification,
    targetClassification: $targetClassification
  ) {
    fromClassification toClassification
    codes { sourceCode targetCodes }
  }
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_usd(value: float) -> str:
    """Format a number as USD string."""
    if abs(value) >= 1e12:
        return f"${value / 1e12:,.2f} trillion"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f} billion"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.2f} million"
    return f"${value:,.0f}"


def pct_str(value: float) -> str:
    """Format a fraction as percentage string."""
    return f"{value * 100:.1f}%"


def write_result(qid: int, result: dict) -> None:
    d = RESULTS_DIR / str(qid) / "ground_truth"
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(result, indent=2) + "\n")


def make_result(qid: int, atlas_url: str, data: list[dict]) -> dict:
    return {
        "question_id": str(qid),
        "execution_timestamp": TIMESTAMP,
        "source": "atlas_explore_page",
        "atlas_url": atlas_url,
        "results": {"data": data},
    }


def explore_url(viz: str, **params: str | int) -> str:
    """Build an Atlas Explore page URL."""
    base = f"https://atlas.hks.harvard.edu/explore/{viz}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}?{qs}"
    return base


def parse_product_id(pid_str: str) -> int:
    """Extract integer from 'product-HS92-726' → 726."""
    return int(pid_str.rsplit("-", 1)[-1])


def get_product_id(hs_code: str) -> int:
    """Get the integer product ID for use in query parameters."""
    return parse_product_id(PRODUCT_MAP[hs_code]["productId"])


def get_product_str_id(hs_code: str) -> str:
    """Get the full string product ID for matching response data."""
    return PRODUCT_MAP[hs_code]["productId"]


def product_label(hs_code: str) -> str:
    """Return 'Product Name (XXXX HS12)' (or whichever PRODUCT_CLASS is active)."""
    name = PRODUCT_MAP[hs_code].get("nameShortEn") or hs_code
    return f"{name} ({hs_code} {PRODUCT_CLASS})"


def get_cpd(country: str, hs_code: str) -> dict | None:
    """Get country-product-year row for a specific country and product."""
    target = get_product_str_id(hs_code)
    for row in country_product_data.get(country, []):
        if row["productId"] == target:
            return row
    return None


def get_py(hs_code: str) -> dict | None:
    """Get global product-year data for a product."""
    return all_product_year.get(get_product_str_id(hs_code))


def get_year_row(series: list[dict], year: int) -> dict | None:
    for row in series:
        if row["year"] == year:
            return row
    return None


def product_name(hs_code: str) -> str:
    """Short display name for a product."""
    return PRODUCT_MAP[hs_code].get("nameShortEn") or hs_code


def parse_group_id(gid_str: str) -> int:
    """Extract integer from 'group-947' → 947."""
    return int(gid_str.rsplit("-", 1)[-1])


def compute_cagr(start_val: float, end_val: float, years: int) -> float:
    """Compute compound annual growth rate."""
    if start_val <= 0 or end_val <= 0:
        return 0.0
    return (end_val / start_val) ** (1.0 / years) - 1.0


def product_name_by_id(pid_str: str) -> str:
    """Given a string productId like 'product-HS92-726', return short name."""
    info = PRODUCT_ID_MAP.get(pid_str, {})
    return info.get("nameShortEn") or info.get("nameEn") or pid_str


def product_code_by_id(pid_str: str) -> str:
    """Given a string productId, return the HS code."""
    return PRODUCT_ID_MAP.get(pid_str, {}).get("code", "")


# ---------------------------------------------------------------------------
# Rate-limited GraphQL client
# ---------------------------------------------------------------------------


async def gql(
    client: httpx.AsyncClient, query: str, variables: dict | None = None
) -> dict:
    """Execute a GraphQL query with rate limiting."""
    async with SEM:
        resp = await client.post(
            ENDPOINT,
            json={"query": query, "variables": variables or {}},
            headers={"User-Agent": "ask-atlas-gt"},
            timeout=30,
        )
        await asyncio.sleep(RATE_DELAY)
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {json.dumps(body['errors'], indent=2)}")
    return body["data"]


# ---------------------------------------------------------------------------
# Question generator state
# ---------------------------------------------------------------------------

QID = [170]
ALL_QUESTIONS: list[dict] = []


def next_id() -> int:
    qid = QID[0]
    QID[0] += 1
    return qid


def emit(
    text: str,
    category_id: str,
    category_name: str,
    difficulty: str,
    url: str,
    data: list[dict],
) -> None:
    """Write ground truth result and track question for eval_questions.json."""
    qid = next_id()
    if QUESTION_FILTER is not None and qid not in QUESTION_FILTER:
        return
    r = make_result(qid, url, data)
    write_result(qid, r)
    ALL_QUESTIONS.append(
        {
            "id": qid,
            "category_id": category_id,
            "difficulty": difficulty,
            "text": text,
            "source": "atlas_explore_page",
            "atlas_url": url,
        }
    )


# ---------------------------------------------------------------------------
# Phase 1: Catalog resolution
# ---------------------------------------------------------------------------


async def resolve_catalogs(client: httpx.AsyncClient) -> None:
    """Populate PRODUCT_MAP, COUNTRY_NAMES, GROUP_MAP, DATA_AVAILABILITY."""
    global DATA_AVAILABILITY

    results = await asyncio.gather(
        gql(client, product_catalog_query()),
        gql(client, LOCATION_COUNTRY_QUERY),
        gql(client, DATA_AVAILABILITY_QUERY),
        gql(client, LOCATION_GROUP_QUERY, {"groupType": "wdi_region"}),
        gql(client, LOCATION_GROUP_QUERY, {"groupType": "wdi_income_level"}),
        gql(client, LOCATION_GROUP_QUERY, {"groupType": "trade"}),
        gql(client, LOCATION_GROUP_QUERY, {"groupType": "political"}),
    )

    # Product catalog — keyed by HS code and by string productId
    catalog_key = _CATALOG_QUERY_NAMES[PRODUCT_CLASS]
    for p in results[0][catalog_key]:
        PRODUCT_MAP[p["code"]] = p
        PRODUCT_ID_MAP[p["productId"]] = p

    # Country names — keyed by string countryId ("country-404")
    for c in results[1]["locationCountry"]:
        COUNTRY_NAMES[c["countryId"]] = c.get("nameShortEn") or c["nameEn"]

    # Data availability
    DATA_AVAILABILITY = results[2]["dataAvailability"]

    # Groups (results[3:] are the group queries)
    for group_result in results[3:]:
        for g in group_result["locationGroup"]:
            GROUP_MAP[g["groupName"]] = g

    # Verify target products
    for name, code in PRODUCT_CODES.items():
        assert code in PRODUCT_MAP, f"Product {name} ({code}) not found"

    # Verify target groups
    for api_name in TARGET_GROUPS:
        if api_name not in GROUP_MAP:
            print(
                f"  WARNING: Group '{api_name}' not found. Available: "
                f"{sorted(GROUP_MAP.keys())}"
            )

    print(
        f"  Products: {len(PRODUCT_MAP)}, Countries: {len(COUNTRY_NAMES)}, "
        f"Groups: {len(GROUP_MAP)}"
    )


# ---------------------------------------------------------------------------
# Phase 2: Targeted data fetching
# ---------------------------------------------------------------------------


async def fetch_phase2(client: httpx.AsyncClient) -> None:
    """Fetch all targeted data concurrently (rate-limited by semaphore)."""

    async def fetch_cpd(country: str) -> None:
        data = await gql(
            client,
            country_product_year_query(),
            {
                "countryId": COUNTRIES[country],
                "yearMin": 2024,
                "yearMax": 2024,
            },
        )
        country_product_data[country] = data["countryProductYear"]
        print(
            f"    country products: {country} ({len(data['countryProductYear'])} rows)"
        )

    async def fetch_exporters(code: str) -> None:
        data = await gql(
            client,
            product_all_countries_query(),
            {
                "productId": get_product_id(code),
                "yearMin": 2024,
                "yearMax": 2024,
            },
        )
        product_exporters[code] = data["countryProductYear"]
        print(
            f"    top exporters: {code} ({len(data['countryProductYear'])} countries)"
        )

    async def fetch_all_product_year() -> None:
        data = await gql(
            client,
            product_year_all_query(),
            {
                "yearMin": 2024,
                "yearMax": 2024,
            },
        )
        for row in data["productYear"]:
            all_product_year[row["productId"]] = row
        print(f"    product year: {len(all_product_year)} products")

    async def fetch_bilateral(pair: tuple[str, str]) -> None:
        exp, imp = pair
        data = await gql(
            client,
            country_country_year_query(),
            {
                "countryId": COUNTRIES[exp],
                "partnerCountryId": COUNTRIES[imp],
                "yearMin": 2024,
                "yearMax": 2024,
            },
        )
        rows = data["countryCountryYear"]
        bilateral_total[pair] = rows[0] if rows else {}
        print(f"    bilateral: {exp} -> {imp}")

    async def fetch_bilateral_prods(pair: tuple[str, str]) -> None:
        exp, imp = pair
        data = await gql(
            client,
            country_country_product_year_query(),
            {
                "countryId": COUNTRIES[exp],
                "partnerCountryId": COUNTRIES[imp],
                "yearMin": 2024,
                "yearMax": 2024,
            },
        )
        bilateral_products[pair] = data["countryCountryProductYear"]
        print(
            f"    bilateral products: {exp} -> {imp} ({len(data['countryCountryProductYear'])} products)"
        )

    async def fetch_ts(country: str) -> None:
        data = await gql(
            client,
            country_year_query(),
            {
                "countryId": COUNTRIES[country],
                "yearMin": 2000,
                "yearMax": 2024,
            },
        )
        time_series[country] = sorted(data["countryYear"], key=lambda x: x["year"])
        print(f"    time series: {country} ({len(data['countryYear'])} years)")

    async def fetch_conversion() -> None:
        try:
            data = await gql(
                client,
                CONVERSION_PATH_QUERY,
                {
                    "sourceCode": "0901",
                    "sourceClassification": "HS1992",
                    "targetClassification": "HS2012",
                },
            )
            conversion_result.extend(data.get("conversionPath", []))
            print(f"    conversion: {len(conversion_result)} mappings")
        except Exception as e:
            print(f"    WARNING: conversion query failed: {e}")

    async def fetch_group_yr(api_name: str) -> None:
        g = GROUP_MAP.get(api_name)
        if not g:
            print(f"    WARNING: group '{api_name}' not in catalog, skipping")
            return
        gid = parse_group_id(g["groupId"])
        data = await gql(
            client,
            group_year_query(),
            {
                "groupId": gid,
                "yearMin": 2019,
                "yearMax": 2024,
            },
        )
        group_year_data[api_name] = sorted(data["groupYear"], key=lambda x: x["year"])
        print(f"    group year: {api_name} ({len(data['groupYear'])} years)")

    async def fetch_import_src(country: str, hs_code: str) -> None:
        try:
            data = await gql(
                client,
                import_sources_query(),
                {
                    "countryId": COUNTRIES[country],
                    "productId": get_product_id(hs_code),
                    "yearMin": 2024,
                    "yearMax": 2024,
                },
            )
            import_sources[(country, hs_code)] = data["countryCountryProductYear"]
            print(
                f"    import sources: {country} x {hs_code} ({len(data['countryCountryProductYear'])} partners)"
            )
        except Exception as e:
            print(f"    WARNING: import sources query failed: {e}")

    await asyncio.gather(
        # Country product data (5)
        *[fetch_cpd(c) for c in ["Kenya", "India", "USA", "Ethiopia", "Turkiye"]],
        # Top exporters per product (5)
        *[fetch_exporters(c) for c in ["0901", "8703", "8542", "3004", "2601"]],
        # All product year data (1)
        fetch_all_product_year(),
        # Bilateral totals (5)
        *[fetch_bilateral(p) for p in BILATERAL_PAIRS],
        # Bilateral by product (4)
        *[
            fetch_bilateral_prods(p)
            for p in [
                ("Germany", "USA"),
                ("India", "China"),
                ("Kenya", "USA"),
                ("Brazil", "China"),
            ]
        ],
        # Time series (3)
        *[fetch_ts(c) for c in ["Brazil", "Turkiye", "Kenya"]],
        # Group year data (4)
        *[fetch_group_yr(name) for name in TARGET_GROUPS],
        # Conversion path (1)
        fetch_conversion(),
        # Import sources (1)
        fetch_import_src("USA", "2710"),
    )


# ---------------------------------------------------------------------------
# Question generators
# ---------------------------------------------------------------------------


def gen_product_complexity() -> None:
    """Category: explore_product_complexity (~18 questions)."""
    cat_id = "explore_product_complexity"
    cat_name = "Product-Level Complexity (Explore Page)"

    # Template 1: RCA
    for country, code in [("Kenya", "0901"), ("India", "6109"), ("India", "8542")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("exportRca") is None:
            continue
        url = explore_url(
            "treemap", year=2024, exporter=f"country-{COUNTRIES[country]}"
        )
        emit(
            f"What is {country}'s Revealed Comparative Advantage (RCA) in {product_name(code)}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "RCA",
                    "country": country,
                    "product": product_label(code),
                    "value": round(cpd["exportRca"], 2),
                    "year": "2024",
                }
            ],
        )

    # Template 2: Distance
    for country, code in [("Kenya", "0901"), ("India", "6109"), ("Kenya", "8542")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("distance") is None:
            continue
        url = explore_url(
            "treemap", year=2024, exporter=f"country-{COUNTRIES[country]}"
        )
        emit(
            f"What is {country}'s distance to {product_name(code)} in the product space?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Distance",
                    "country": country,
                    "product": product_label(code),
                    "value": round(cpd["distance"], 4),
                    "year": "2024",
                }
            ],
        )

    # Template 3: PCI (product-level, not country-specific)
    for code in ["0901", "6109", "8542"]:
        py = get_py(code)
        if not py or py.get("pci") is None:
            continue
        url = explore_url("treemap", year=2024)
        emit(
            f"What is the Product Complexity Index (PCI) of {product_name(code)}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "PCI",
                    "product": product_label(code),
                    "value": round(py["pci"], 4),
                    "year": "2024",
                }
            ],
        )

    # Template 4: COG
    for country, code in [("Kenya", "0901"), ("India", "8542"), ("Kenya", "6109")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("cog") is None:
            continue
        url = explore_url(
            "feasibility", year=2024, exporter=f"country-{COUNTRIES[country]}"
        )
        emit(
            f"What is {country}'s Complexity Outlook Gain (COG) for {product_name(code)}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "COG",
                    "country": country,
                    "product": product_label(code),
                    "value": round(cpd["cog"], 4),
                    "year": "2024",
                }
            ],
        )

    # Template 5: Market share
    for country, code in [("India", "0901"), ("Kenya", "8542"), ("India", "6109")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("globalMarketShare") is None:
            continue
        url = explore_url(
            "treemap", year=2024, exporter=f"country-{COUNTRIES[country]}"
        )
        emit(
            f"What is {country}'s global market share in {product_name(code)}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Global market share",
                    "country": country,
                    "product": product_label(code),
                    "value": pct_str(cpd["globalMarketShare"]),
                    "raw_value": cpd["globalMarketShare"],
                    "year": "2024",
                }
            ],
        )

    # Template 6: Product status
    for country, code in [("Kenya", "0901"), ("India", "6109"), ("Kenya", "8542")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("productStatus") is None:
            continue
        url = explore_url(
            "treemap", year=2024, exporter=f"country-{COUNTRIES[country]}"
        )
        emit(
            f"Is {product_name(code)} classified as a new, present, lost, or absent export for {country}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Product status",
                    "country": country,
                    "product": product_label(code),
                    "value": cpd["productStatus"],
                    "year": "2024",
                }
            ],
        )


def gen_global_product_stats() -> None:
    """Category: explore_global_product_stats (~13 questions)."""
    cat_id = "explore_global_product_stats"
    cat_name = "Global Product Statistics (Explore Page)"

    # Template 7: Global export value (5 products)
    for code in ["8703", "8542", "3004", "0901", "2601"]:
        py = get_py(code)
        if not py or py.get("exportValue") is None:
            continue
        url = explore_url("treemap", year=2024)
        emit(
            f"What is the total global export value of {product_name(code)}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Global export value",
                    "product": product_label(code),
                    "value": format_usd(py["exportValue"]),
                    "raw_value": py["exportValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 8: 5-year CAGR (3 products)
    for code in ["8703", "8542", "0901"]:
        py = get_py(code)
        if not py or py.get("exportValueConstCagr5") is None:
            continue
        url = explore_url("feasibility/table", year=2024, productLevel=4)
        emit(
            f"What is the 5-year export growth rate (CAGR) for {product_name(code)} globally?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "5-year export CAGR (constant USD)",
                    "product": product_label(code),
                    "value": pct_str(py["exportValueConstCagr5"]),
                    "raw_value": py["exportValueConstCagr5"],
                    "year": "2024",
                }
            ],
        )

    # Template 9: Complexity classification (3 products)
    for code in ["8703", "3004", "2601"]:
        py = get_py(code)
        if not py or py.get("complexityEnum") is None:
            continue
        url = explore_url("treemap", year=2024)
        emit(
            f"What is the complexity classification (low/moderate/high) of {product_name(code)}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Complexity classification",
                    "product": product_label(code),
                    "value": py["complexityEnum"],
                    "year": "2024",
                }
            ],
        )

    # Template 10: Largest exporter (1 product)
    for code in ["0901"]:
        rows = product_exporters.get(code, [])
        if not rows:
            continue
        top = max(rows, key=lambda r: r.get("exportValue") or 0)
        name = COUNTRY_NAMES.get(top["countryId"], str(top["countryId"]))
        url = explore_url(
            "treemap",
            year=2024,
            view="markets",
            product=f"product-{PRODUCT_CLASS}-{get_product_id(code)}",
        )
        emit(
            f"Which country is the largest exporter of {product_name(code)}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Largest exporter",
                    "product": product_label(code),
                    "country": name,
                    "export_value": format_usd(top["exportValue"]),
                    "raw_export_value": top["exportValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 11: Top 3 exporters (1 product)
    for code in ["8542"]:
        rows = product_exporters.get(code, [])
        if not rows:
            continue
        sorted_rows = sorted(
            rows, key=lambda r: r.get("exportValue") or 0, reverse=True
        )[:3]
        url = explore_url(
            "treemap",
            year=2024,
            view="markets",
            product=f"product-{PRODUCT_CLASS}-{get_product_id(code)}",
        )
        data = []
        for i, r in enumerate(sorted_rows):
            name = COUNTRY_NAMES.get(r["countryId"], str(r["countryId"]))
            data.append(
                {
                    "rank": i + 1,
                    "country": name,
                    "export_value": format_usd(r["exportValue"]),
                    "raw_export_value": r["exportValue"],
                }
            )
        emit(
            f"What are the top 3 exporters of {product_name(code)} by value?",
            cat_id,
            cat_name,
            "medium",
            url,
            data,
        )


def gen_bilateral_trade() -> None:
    """Category: explore_bilateral_trade (~13 questions)."""
    cat_id = "explore_bilateral_trade"
    cat_name = "Bilateral Trade (Explore Page)"

    # Template 12: Total exports (all 5 pairs)
    for exp, imp in BILATERAL_PAIRS:
        bt = bilateral_total.get((exp, imp), {})
        if not bt or bt.get("exportValue") is None:
            continue
        url = explore_url(
            "treemap",
            year=2024,
            view="markets",
            exporter=f"country-{COUNTRIES[exp]}",
            importer=f"country-{COUNTRIES[imp]}",
        )
        emit(
            f"What is the total export value from {exp} to {imp}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Total bilateral exports",
                    "exporter": exp,
                    "importer": imp,
                    "value": format_usd(bt["exportValue"]),
                    "raw_value": bt["exportValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 13: Total imports (2 pairs)
    for exp, imp in [("Brazil", "China"), ("India", "China")]:
        bt = bilateral_total.get((exp, imp), {})
        if not bt or bt.get("importValue") is None:
            continue
        url = explore_url(
            "treemap",
            year=2024,
            view="markets",
            exporter=f"country-{COUNTRIES[exp]}",
            importer=f"country-{COUNTRIES[imp]}",
            tradeDirection="imports",
        )
        emit(
            f"What is the total import value of {exp} from {imp}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Total bilateral imports",
                    "importer": exp,
                    "exporter": imp,
                    "value": format_usd(bt["importValue"]),
                    "raw_value": bt["importValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 14: Trade balance (2 pairs)
    for exp, imp in [("Germany", "USA"), ("Turkiye", "Germany")]:
        bt = bilateral_total.get((exp, imp), {})
        ev = bt.get("exportValue")
        iv = bt.get("importValue")
        if ev is None or iv is None:
            continue
        balance = ev - iv
        url = explore_url(
            "treemap",
            year=2024,
            view="markets",
            exporter=f"country-{COUNTRIES[exp]}",
            importer=f"country-{COUNTRIES[imp]}",
        )
        emit(
            f"What is the trade balance between {exp} and {imp}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Trade balance",
                    "country": exp,
                    "partner": imp,
                    "value": format_usd(balance),
                    "raw_value": balance,
                    "status": "surplus" if balance > 0 else "deficit",
                    "exports": format_usd(ev),
                    "imports": format_usd(iv),
                    "year": "2024",
                }
            ],
        )

    # Template 15: Product-level bilateral (2 pairs)
    bilat_product_combos = [
        ("Kenya", "USA", "0901"),
        ("Brazil", "China", "2601"),
    ]
    for exp, imp, code in bilat_product_combos:
        rows = bilateral_products.get((exp, imp), [])
        pid_str = get_product_str_id(code)
        pid_int = get_product_id(code)
        match = next((r for r in rows if r["productId"] == pid_str), None)
        if not match or match.get("exportValue") is None:
            continue
        url = explore_url(
            "treemap",
            year=2024,
            exporter=f"country-{COUNTRIES[exp]}",
            importer=f"country-{COUNTRIES[imp]}",
            product=f"product-{PRODUCT_CLASS}-{pid_int}",
        )
        emit(
            f"What is the value of {product_name(code)} exports from {exp} to {imp}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Bilateral product exports",
                    "exporter": exp,
                    "importer": imp,
                    "product": product_label(code),
                    "value": format_usd(match["exportValue"]),
                    "raw_value": match["exportValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 16: Top 3 products (2 pairs)
    for exp, imp in [("Germany", "USA"), ("India", "China")]:
        rows = bilateral_products.get((exp, imp), [])
        if not rows:
            continue
        sorted_rows = sorted(
            rows, key=lambda r: r.get("exportValue") or 0, reverse=True
        )[:3]
        url = explore_url(
            "treemap",
            year=2024,
            exporter=f"country-{COUNTRIES[exp]}",
            importer=f"country-{COUNTRIES[imp]}",
        )
        data = []
        for i, r in enumerate(sorted_rows):
            pname = product_name_by_id(r["productId"])
            pcode = product_code_by_id(r["productId"])
            data.append(
                {
                    "rank": i + 1,
                    "product": f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname,
                    "export_value": format_usd(r["exportValue"]),
                    "raw_export_value": r["exportValue"],
                }
            )
        emit(
            f"What are the top 3 products {exp} exports to {imp}?",
            cat_id,
            cat_name,
            "hard",
            url,
            data,
        )


def gen_import_composition() -> None:
    """Category: explore_import_composition (~6 questions)."""
    cat_id = "explore_import_composition"
    cat_name = "Import Composition (Explore Page)"

    # Template 17: Top imported product (2 countries)
    for country in ["USA", "Ethiopia"]:
        rows = country_product_data.get(country, [])
        imports = [r for r in rows if r.get("importValue") and r["importValue"] > 0]
        if not imports:
            continue
        top = max(imports, key=lambda r: r["importValue"])
        pname = product_name_by_id(top["productId"])
        pcode = product_code_by_id(top["productId"])
        url = explore_url(
            "treemap",
            year=2024,
            exporter=f"country-{COUNTRIES[country]}",
            tradeDirection="imports",
        )
        emit(
            f"What is the top imported product for {country}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Top imported product",
                    "country": country,
                    "product": f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname,
                    "import_value": format_usd(top["importValue"]),
                    "raw_import_value": top["importValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 18: Top 3 imports (USA only)
    rows = country_product_data.get("USA", [])
    imports = sorted(
        [r for r in rows if r.get("importValue") and r["importValue"] > 0],
        key=lambda r: r["importValue"],
        reverse=True,
    )[:3]
    if imports:
        url = explore_url(
            "treemap", year=2024, exporter="country-840", tradeDirection="imports"
        )
        data = []
        for i, r in enumerate(imports):
            pname = product_name_by_id(r["productId"])
            pcode = product_code_by_id(r["productId"])
            data.append(
                {
                    "rank": i + 1,
                    "product": f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname,
                    "import_value": format_usd(r["importValue"]),
                    "raw_import_value": r["importValue"],
                }
            )
        emit(
            "What are the top 3 imported products for the USA by value?",
            cat_id,
            cat_name,
            "medium",
            url,
            data,
        )

    # Template 19: Product import value (2 combos)
    for country, code in [("USA", "2710"), ("Ethiopia", "3004")]:
        cpd = get_cpd(country, code)
        if not cpd or cpd.get("importValue") is None:
            continue
        url = explore_url(
            "treemap",
            year=2024,
            exporter=f"country-{COUNTRIES[country]}",
            tradeDirection="imports",
        )
        emit(
            f"What is {country}'s import value for {product_name(code)}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Product import value",
                    "country": country,
                    "product": product_label(code),
                    "value": format_usd(cpd["importValue"]),
                    "raw_value": cpd["importValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 20: Largest source of imports (USA x Petroleum)
    src_rows = import_sources.get(("USA", "2710"), [])
    if src_rows:
        valid = [r for r in src_rows if r.get("importValue") and r["importValue"] > 0]
        if valid:
            top = max(valid, key=lambda r: r["importValue"])
            name = COUNTRY_NAMES.get(
                top["partnerCountryId"], str(top["partnerCountryId"])
            )
            pid_int = get_product_id("2710")
            url = explore_url(
                "treemap",
                year=2024,
                exporter="country-840",
                tradeDirection="imports",
                product=f"product-{PRODUCT_CLASS}-{pid_int}",
            )
            emit(
                f"From which country does the USA import the most {product_name('2710')}?",
                cat_id,
                cat_name,
                "hard",
                url,
                [
                    {
                        "metric": "Largest import source",
                        "country": "USA",
                        "product": product_label("2710"),
                        "source_country": name,
                        "import_value": format_usd(top["importValue"]),
                        "raw_import_value": top["importValue"],
                        "year": "2024",
                    }
                ],
            )


def gen_trade_time_series() -> None:
    """Category: explore_trade_time_series (~10 questions)."""
    cat_id = "explore_trade_time_series"
    cat_name = "Trade Time Series (Explore Page)"

    # Template 21: Exports in specific year (3)
    for country, year in [("Brazil", 2020), ("Turkiye", 2015), ("Kenya", 2020)]:
        row = get_year_row(time_series.get(country, []), year)
        if not row or row.get("exportValue") is None:
            continue
        url = explore_url(
            "overtime",
            year=year,
            exporter=f"country-{COUNTRIES[country]}",
            startYear=2000,
            endYear=2024,
        )
        emit(
            f"What was {country}'s total export value in {year}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "Total exports",
                    "country": country,
                    "value": format_usd(row["exportValue"]),
                    "raw_value": row["exportValue"],
                    "year": str(year),
                }
            ],
        )

    # Template 22: Export change between years (2)
    for country, y1, y2 in [("Brazil", 2015, 2024), ("Kenya", 2010, 2024)]:
        r1 = get_year_row(time_series.get(country, []), y1)
        r2 = get_year_row(time_series.get(country, []), y2)
        if not r1 or not r2:
            continue
        ev1 = r1.get("exportValue")
        ev2 = r2.get("exportValue")
        if ev1 is None or ev2 is None or ev1 == 0:
            continue
        change = (ev2 - ev1) / ev1
        url = explore_url(
            "overtime",
            exporter=f"country-{COUNTRIES[country]}",
            startYear=y1,
            endYear=y2,
            year=y2,
        )
        emit(
            f"How have {country}'s exports changed from {y1} to {y2}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "Export change",
                    "country": country,
                    "start_year": str(y1),
                    "end_year": str(y2),
                    "start_value": format_usd(ev1),
                    "end_value": format_usd(ev2),
                    "change_pct": pct_str(change),
                    "raw_change": change,
                }
            ],
        )

    # Template 23: GDP per capita in year (2)
    for country, year in [("Turkiye", 2024), ("Kenya", 2024)]:
        row = get_year_row(time_series.get(country, []), year)
        if not row or row.get("gdppc") is None:
            continue
        url = explore_url(
            "overtime",
            year=year,
            exporter=f"country-{COUNTRIES[country]}",
            startYear=2000,
            endYear=2024,
        )
        emit(
            f"What was {country}'s GDP per capita in {year}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "GDP per capita",
                    "country": country,
                    "value": f"${row['gdppc']:,.0f}",
                    "raw_value": row["gdppc"],
                    "year": str(year),
                }
            ],
        )

    # Template 24: ECI in year (1)
    for country, year in [("Brazil", 2024)]:
        row = get_year_row(time_series.get(country, []), year)
        if not row or row.get("eci") is None:
            continue
        url = explore_url(
            "overtime",
            year=year,
            exporter=f"country-{COUNTRIES[country]}",
            startYear=2000,
            endYear=2024,
        )
        emit(
            f"What was {country}'s ECI in {year}?",
            cat_id,
            cat_name,
            "easy",
            url,
            [
                {
                    "metric": "ECI",
                    "country": country,
                    "value": f"{row['eci']:.4f}",
                    "raw_value": row["eci"],
                    "year": str(year),
                }
            ],
        )

    # Template 25: ECI change between years (2)
    for country, y1, y2 in [("Turkiye", 2015, 2024), ("Kenya", 2010, 2024)]:
        r1 = get_year_row(time_series.get(country, []), y1)
        r2 = get_year_row(time_series.get(country, []), y2)
        if not r1 or not r2:
            continue
        e1 = r1.get("eci")
        e2 = r2.get("eci")
        if e1 is None or e2 is None:
            continue
        url = explore_url(
            "overtime",
            exporter=f"country-{COUNTRIES[country]}",
            startYear=y1,
            endYear=y2,
            year=y2,
        )
        emit(
            f"How has {country}'s ECI changed from {y1} to {y2}?",
            cat_id,
            cat_name,
            "medium",
            url,
            [
                {
                    "metric": "ECI change",
                    "country": country,
                    "start_year": str(y1),
                    "end_year": str(y2),
                    "start_value": f"{e1:.4f}",
                    "end_value": f"{e2:.4f}",
                    "change": f"{e2 - e1:+.4f}",
                }
            ],
        )


def gen_feasibility() -> None:
    """Category: explore_feasibility (~6 questions)."""
    cat_id = "explore_feasibility"
    cat_name = "Growth Opportunities (Explore Page)"

    for country in ["Kenya", "Turkiye"]:
        rows = country_product_data.get(country, [])
        # Filter to products without RCA, with positive COG
        opps = [
            r
            for r in rows
            if r.get("cog") is not None
            and r["cog"] > 0
            and (r.get("exportRca") is None or r["exportRca"] < 1)
        ]
        opps.sort(key=lambda r: r["cog"], reverse=True)
        top5 = opps[:5]
        if not top5:
            continue

        url = explore_url(
            "feasibility/table",
            year=2024,
            productLevel=4,
            exporter=f"country-{COUNTRIES[country]}",
        )

        # Template 26: Top 5 by COG
        data = []
        for i, r in enumerate(top5):
            pname = product_name_by_id(r["productId"])
            pcode = product_code_by_id(r["productId"])
            data.append(
                {
                    "rank": i + 1,
                    "product": f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname,
                    "cog": round(r["cog"], 4),
                    "distance": round(r["distance"], 4) if r.get("distance") else None,
                }
            )
        emit(
            f"What are the top 5 growth opportunity products for {country} ranked by opportunity gain?",
            cat_id,
            cat_name,
            "hard",
            url,
            data,
        )

        # Template 27: Global size of top opportunity product
        top_prod = top5[0]
        py = all_product_year.get(top_prod["productId"])
        if py and py.get("exportValue") is not None:
            pname = product_name_by_id(top_prod["productId"])
            pcode = product_code_by_id(top_prod["productId"])
            emit(
                f"What is the global market size of {country}'s top growth opportunity product?",
                cat_id,
                cat_name,
                "medium",
                url,
                [
                    {
                        "metric": "Global export value of top opportunity",
                        "country": country,
                        "product": (
                            f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname
                        ),
                        "value": format_usd(py["exportValue"]),
                        "raw_value": py["exportValue"],
                        "year": "2024",
                    }
                ],
            )

        # Template 28: 5yr growth of top opportunity product
        if py and py.get("exportValueConstCagr5") is not None:
            pname = product_name_by_id(top_prod["productId"])
            pcode = product_code_by_id(top_prod["productId"])
            emit(
                f"What is the 5-year growth rate of {country}'s top growth opportunity product globally?",
                cat_id,
                cat_name,
                "medium",
                url,
                [
                    {
                        "metric": "5-year CAGR of top opportunity",
                        "country": country,
                        "product": (
                            f"{pname} ({pcode} {PRODUCT_CLASS})" if pcode else pname
                        ),
                        "value": pct_str(py["exportValueConstCagr5"]),
                        "raw_value": py["exportValueConstCagr5"],
                        "year": "2024",
                    }
                ],
            )


def gen_regional_aggregates() -> None:
    """Category: explore_regional_aggregates (~12 questions)."""
    cat_id = "explore_regional_aggregates"
    cat_name = "Regional Aggregates (Explore Page)"

    # Template 29: Export value (4 groups) — from groupYear data
    for api_name, display_name in TARGET_GROUPS.items():
        rows = group_year_data.get(api_name, [])
        row_2024 = get_year_row(rows, 2024)
        if not row_2024 or row_2024.get("exportValue") is None:
            continue
        emit(
            f"What is the total export value of {display_name} according to the Atlas?",
            cat_id,
            cat_name,
            "medium",
            explore_url("treemap", year=2024),
            [
                {
                    "metric": "Total export value",
                    "group": api_name,
                    "value": format_usd(row_2024["exportValue"]),
                    "raw_value": row_2024["exportValue"],
                    "year": "2024",
                }
            ],
        )

    # Template 30: 5-year CAGR (4 groups) — computed from groupYear
    for api_name, display_name in TARGET_GROUPS.items():
        rows = group_year_data.get(api_name, [])
        row_2019 = get_year_row(rows, 2019)
        row_2024 = get_year_row(rows, 2024)
        if (
            not row_2019
            or not row_2024
            or not row_2019.get("exportValue")
            or not row_2024.get("exportValue")
        ):
            continue
        cagr = compute_cagr(row_2019["exportValue"], row_2024["exportValue"], 5)
        emit(
            f"What is the 5-year export growth rate for {display_name}?",
            cat_id,
            cat_name,
            "medium",
            explore_url("treemap", year=2024),
            [
                {
                    "metric": "5-year export CAGR",
                    "group": api_name,
                    "value": pct_str(cagr),
                    "raw_value": cagr,
                    "start_year": "2019",
                    "end_year": "2024",
                }
            ],
        )

    # Template 32: Members (2 groups)
    for api_name in ["European Union", "low"]:
        g = GROUP_MAP.get(api_name)
        display_name = TARGET_GROUPS.get(api_name, api_name)
        if not g or not g.get("members"):
            continue
        # Group members are string IDs like "country-404"
        member_names = sorted(COUNTRY_NAMES.get(mid, str(mid)) for mid in g["members"])
        emit(
            f"Which countries belong to the {display_name} group according to the Atlas?",
            cat_id,
            cat_name,
            "easy",
            explore_url("treemap", year=2024),
            [
                {
                    "metric": "Group members",
                    "group": api_name,
                    "count": len(member_names),
                    "members": member_names,
                }
            ],
        )


def gen_product_metadata() -> None:
    """Category: explore_product_metadata (~7 questions)."""
    cat_id = "explore_product_metadata"
    cat_name = "Product Classification & Metadata (Explore Page)"

    # Template 33: Natural resource flag (2 products)
    for code in ["2601", "0901"]:
        info = PRODUCT_MAP.get(code)
        if not info:
            continue
        emit(
            f"Is {product_name(code)} classified as a natural resource on the Atlas?",
            cat_id,
            cat_name,
            "easy",
            explore_url("treemap", year=2024),
            [
                {
                    "metric": "Natural resource classification",
                    "product": product_label(code),
                    "value": bool(info.get("naturalResource")),
                }
            ],
        )

    # Template 34: Green product flag (2 products)
    for code in ["8703", "2710"]:
        info = PRODUCT_MAP.get(code)
        if not info:
            continue
        emit(
            f"Is {product_name(code)} classified as a green product on the Atlas?",
            cat_id,
            cat_name,
            "easy",
            explore_url("treemap", year=2024),
            [
                {
                    "metric": "Green product classification",
                    "product": product_label(code),
                    "value": bool(info.get("greenProduct")),
                }
            ],
        )

    # Templates 35-37 are inherently HS92-specific (code conversion,
    # HS92 product count, HS92 data years). Only generate when running HS92.
    if PRODUCT_CLASS == "HS92":
        # Template 35: HS conversion
        # conversionPath returns steps with codes[].targetCodes lists
        # If codes is empty at each step, the code is unchanged (0901 → 0901)
        if conversion_result:
            # Check if any step has actual code changes
            all_codes_empty = all(not step.get("codes") for step in conversion_result)
            if all_codes_empty:
                # Code unchanged through all revisions
                emit(
                    "What HS 2012 code corresponds to Coffee (HS 1992 code 0901)?",
                    cat_id,
                    cat_name,
                    "hard",
                    explore_url("treemap", year=2024),
                    [
                        {
                            "metric": "HS code conversion",
                            "source_code": "0901",
                            "source_classification": "HS 1992",
                            "target_classification": "HS 2012",
                            "target_codes": ["0901"],
                            "note": "Code unchanged across revisions",
                        }
                    ],
                )
            else:
                # Collect target codes from the final step
                final_step = conversion_result[-1]
                targets = []
                for code_entry in final_step.get("codes", []):
                    targets.extend(code_entry.get("targetCodes", []))
                if targets:
                    emit(
                        "What HS 2012 code corresponds to Coffee (HS 1992 code 0901)?",
                        cat_id,
                        cat_name,
                        "hard",
                        explore_url("treemap", year=2024),
                        [
                            {
                                "metric": "HS code conversion",
                                "source_code": "0901",
                                "source_classification": "HS 1992",
                                "target_classification": "HS 2012",
                                "target_codes": targets,
                            }
                        ],
                    )

        # Template 36: Product count
        hs4_count = len(PRODUCT_MAP)
        emit(
            "How many 4-digit HS92 products does the Atlas track?",
            cat_id,
            cat_name,
            "easy",
            explore_url("treemap", year=2024),
            [{"metric": "HS92 4-digit product count", "value": hs4_count}],
        )

        # Template 37: Data years available
        hs92_avail = next(
            (d for d in DATA_AVAILABILITY if d.get("productClassification") == "HS92"),
            None,
        )
        if hs92_avail:
            emit(
                "What years of trade data are available for HS 1992 on the Atlas?",
                cat_id,
                cat_name,
                "easy",
                explore_url("treemap", year=2024),
                [
                    {
                        "metric": "Data availability",
                        "classification": "HS 1992",
                        "year_min": hs92_avail["yearMin"],
                        "year_max": hs92_avail["yearMax"],
                    }
                ],
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    global SEM, PRODUCT_CLASS, QUESTION_FILTER

    parser = argparse.ArgumentParser(
        description="Collect Explore page ground truth data"
    )
    parser.add_argument(
        "--product-class",
        choices=["HS92", "HS12", "HS22", "SITC"],
        default="HS12",
        help="Product classification to use (default: HS12)",
    )
    parser.add_argument(
        "--questions",
        type=int,
        nargs="+",
        metavar="ID",
        help="Only regenerate specific question IDs (e.g. --questions 244 245 246)",
    )
    args = parser.parse_args()
    PRODUCT_CLASS = args.product_class
    if args.questions:
        QUESTION_FILTER = set(args.questions)

    SEM = asyncio.Semaphore(2)

    print("Phase 1: Resolving catalogs...")
    async with httpx.AsyncClient() as client:
        await resolve_catalogs(client)

        print("\nPhase 2: Fetching targeted data...")
        await fetch_phase2(client)

    print(f"\nGenerating questions (starting at ID {QID[0]})...\n")

    gen_product_complexity()
    gen_global_product_stats()
    gen_bilateral_trade()
    gen_import_composition()
    gen_trade_time_series()
    gen_feasibility()
    gen_regional_aggregates()
    gen_product_metadata()

    print(f"Generated {len(ALL_QUESTIONS)} questions (IDs 170-{QID[0] - 1})")

    # Write integration file
    new_categories = [
        {
            "id": "explore_product_complexity",
            "name": "Product-Level Complexity (Explore Page)",
            "description": "Product-level RCA, PCI, distance, COG from Atlas Explore pages",
        },
        {
            "id": "explore_global_product_stats",
            "name": "Global Product Statistics (Explore Page)",
            "description": "Global export values, growth rates, and complexity for specific products",
        },
        {
            "id": "explore_bilateral_trade",
            "name": "Bilateral Trade (Explore Page)",
            "description": "Country-to-country trade flows, total and by product",
        },
        {
            "id": "explore_import_composition",
            "name": "Import Composition (Explore Page)",
            "description": "Product-level import breakdown for countries",
        },
        {
            "id": "explore_trade_time_series",
            "name": "Trade Time Series (Explore Page)",
            "description": "Year-by-year trade data, GDP, ECI time series",
        },
        {
            "id": "explore_feasibility",
            "name": "Growth Opportunities (Explore Page)",
            "description": "Feasibility metrics: opportunity gain, distance, global size, growth",
        },
        {
            "id": "explore_regional_aggregates",
            "name": "Regional Aggregates (Explore Page)",
            "description": "Regional and group-level trade data and growth rates",
        },
        {
            "id": "explore_product_metadata",
            "name": "Product Classification & Metadata (Explore Page)",
            "description": "Product catalog details, natural resource flags, classification conversion",
        },
    ]

    out_path = BASE_DIR / "new_explore_page_questions.json"
    output = {
        "new_categories": new_categories,
        "new_questions": ALL_QUESTIONS,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\nWrote integration file: {out_path}")

    # Category breakdown
    cats = Counter(q["category_id"] for q in ALL_QUESTIONS)
    print("\nQuestions by category:")
    for cat_id, count in cats.most_common():
        print(f"  {cat_id}: {count}")


if __name__ == "__main__":
    asyncio.run(main())
