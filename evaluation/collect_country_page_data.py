#!/usr/bin/env python3
"""
Layer 1: GraphQL API collection script for Atlas country page eval data.

Queries the Atlas GraphQL API for all 8 countries and generates
results.json ground truth files for API-sourced data points.
IDs start at 61 (1-60 are existing DB-query questions).

Usage:
    uv run python evaluation/collect_country_page_data.py
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENDPOINT = "https://atlas.hks.harvard.edu/api/countries/graphql"
BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

RATE_DELAY = 0.5  # seconds between requests

# Semaphore — set inside main() for Python 3.12 compatibility
SEM: asyncio.Semaphore

COUNTRIES: dict[str, dict] = {
    "Kenya": {"id": "location-404", "iso": 404},
    "Spain": {"id": "location-724", "iso": 724},
    "Brazil": {"id": "location-76", "iso": 76},
    "Germany": {"id": "location-276", "iso": 276},
    "India": {"id": "location-356", "iso": 356},
    "USA": {"id": "location-840", "iso": 840},
    "Turkiye": {"id": "location-792", "iso": 792},
    "Ethiopia": {"id": "location-231", "iso": 231},
}

TIMESTAMP = datetime.now(timezone.utc).isoformat()

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

COUNTRY_PROFILE_QUERY = """
query CountryProfile($loc: ID!) {
  countryProfile(location: $loc) {
    location { id shortName longName }
    latestPopulation { quantity year }
    latestGdp { quantity year }
    latestGdpRank { quantity year }
    latestGdpPerCapita { quantity year }
    latestGdpPerCapitaRank { quantity year }
    latestGdpPerCapitaPpp { quantity year }
    latestGdpPerCapitaPppRank { quantity year }
    incomeClassification
    exportValue importValue exportValueRank
    newProductExportValue newProductExportValuePerCapita
    currentAccount { quantity year }
    latestEci latestEciRank
    latestCoi latestCoiRank
    coiClassification
    growthProjection growthProjectionRank
    growthProjectionClassification
    diversity diversityRank diversificationGrade
    marketShareMainSector { shortName code }
    marketShareMainSectorDirection
    marketShareMainSectorPositiveGrowth
    structuralTransformationStep
    policyRecommendation
  }
}
"""

TREEMAP_EXPORTS_QUERY = """
query TreeMapExports($loc: ID!) {
  treeMap(facet: CPY_C, productClass: HS, year: 2024,
          productLevel: fourDigit, locationLevel: country,
          location: $loc) {
    ... on TreeMapProduct {
      product { shortName code }
      exportValue
      pci
    }
  }
}
"""

TREEMAP_PARTNERS_QUERY = """
query TreeMapPartners($loc: ID!) {
  treeMap(facet: CCY_C, productClass: HS, year: 2024,
          productLevel: fourDigit, locationLevel: country,
          location: $loc) {
    ... on TreeMapLocation {
      location { shortName id }
      exportValue
    }
  }
}
"""

NEW_PRODUCTS_QUERY = """
query NewProducts($loc: ID!) {
  newProductsCountry(location: $loc, year: 2024) {
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
    newProducts { shortName code }
  }
}
"""

NEW_PRODUCTS_COMPARISON_QUERY = """
query NewProductsComparison($loc: ID!) {
  newProductsComparisonCountries(location: $loc, year: 2024, quantity: 3) {
    location { shortName id }
    newProductCount
    newProductExportValue
    newProductExportValuePerCapita
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


def ordinal(n: int) -> str:
    """Convert int to ordinal string (1 -> '1st', 2 -> '2nd', etc.)."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def rank_str(n: int, total: int = 145) -> str:
    return f"{ordinal(n)} of {total}"


def pct_str(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_result(qid: int, result: dict) -> None:
    """Write evaluation/results/{qid}/ground_truth/results.json."""
    d = RESULTS_DIR / str(qid) / "ground_truth"
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.json").write_text(json.dumps(result, indent=2) + "\n")


def make_result(qid: int, atlas_url: str, data: list[dict]) -> dict:
    return {
        "question_id": str(qid),
        "execution_timestamp": TIMESTAMP,
        "source": "atlas_country_page",
        "atlas_url": atlas_url,
        "results": {"data": data},
    }


def atlas_url(iso: int, subpage: str = "") -> str:
    base = f"https://atlas.hks.harvard.edu/countries/{iso}"
    return f"{base}/{subpage}" if subpage else base


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def gql(client: httpx.AsyncClient, query: str, variables: dict) -> dict:
    """Execute a GraphQL query with rate limiting."""
    async with SEM:
        resp = await client.post(
            ENDPOINT,
            json={"query": query, "variables": variables},
            headers={"User-Agent": "ask-atlas-gt"},
            timeout=30,
        )
        await asyncio.sleep(RATE_DELAY)
    resp.raise_for_status()
    body = resp.json()
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {json.dumps(body['errors'], indent=2)}")
    return body["data"]


async def fetch_all_data(client: httpx.AsyncClient, name: str, loc_id: str) -> dict:
    """Fetch all GraphQL data for a single country."""
    profile, exports, partners, new_prods, comparison = await asyncio.gather(
        gql(client, COUNTRY_PROFILE_QUERY, {"loc": loc_id}),
        gql(client, TREEMAP_EXPORTS_QUERY, {"loc": loc_id}),
        gql(client, TREEMAP_PARTNERS_QUERY, {"loc": loc_id}),
        gql(client, NEW_PRODUCTS_QUERY, {"loc": loc_id}),
        gql(client, NEW_PRODUCTS_COMPARISON_QUERY, {"loc": loc_id}),
    )
    return {
        "name": name,
        "profile": profile["countryProfile"],
        "exports": sorted(
            exports["treeMap"], key=lambda x: x["exportValue"], reverse=True
        ),
        "partners": sorted(
            partners["treeMap"], key=lambda x: x["exportValue"], reverse=True
        ),
        "new_products": new_prods["newProductsCountry"],
        "comparison": comparison["newProductsComparisonCountries"],
    }


# ---------------------------------------------------------------------------
# Question generators
# ---------------------------------------------------------------------------
# Each generator yields (question_dict, result_dict) tuples.
# They receive the full data dict for the assigned country(ies).
# `qid` is a mutable list holding [next_id] so generators can increment it.

QID = [61]  # mutable counter


def next_id() -> int:
    qid = QID[0]
    QID[0] += 1
    return qid


# Track all questions for eval_questions.json update
ALL_QUESTIONS: list[dict] = []


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
    r = make_result(qid, url, data)
    write_result(qid, r)
    ALL_QUESTIONS.append(
        {
            "id": qid,
            "category_id": category_id,
            "difficulty": difficulty,
            "text": text,
            "source": "atlas_country_page",
            "atlas_url": url,
        }
    )


def gen_country_profile_overview(country_data: dict) -> None:
    """Section 3.1 — Country Profile Overview questions (API-sourced only)."""
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    url = atlas_url(iso)
    cat_id = "country_profile_overview"
    cat_name = "Country Profile Overview"

    # Q1: GDP per capita
    gdp_pc = p["latestGdpPerCapita"]
    emit(
        f"What is the GDP per capita of {name}?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "GDP per capita",
                "value": f"${gdp_pc['quantity']:,}",
                "year": str(gdp_pc["year"]),
            }
        ],
    )

    # Q2: GDP per capita (PPP)
    gdp_ppp = p["latestGdpPerCapitaPpp"]
    emit(
        f"What is the GDP per capita (PPP) of {name}?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "GDP per capita (PPP)",
                "value": f"${gdp_ppp['quantity']:,}",
                "year": str(gdp_ppp["year"]),
            }
        ],
    )

    # Q3: GDP per capita rank
    gdp_rank = p["latestGdpPerCapitaRank"]
    emit(
        f"What is the GDP per capita rank of {name}?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "GDP per capita rank",
                "value": rank_str(gdp_rank["quantity"]),
                "year": str(gdp_rank["year"]),
            }
        ],
    )

    # Q4: Income classification
    emit(
        f"What income classification does {name} have on the Atlas?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "Income classification", "value": p["incomeClassification"]}],
    )

    # Q5: Population
    pop = p["latestPopulation"]
    emit(
        f"What is the population of {name}?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Population",
                "value": f"{pop['quantity']:,}",
                "year": str(pop["year"]),
            }
        ],
    )

    # Q8: Growth projection
    emit(
        f"What is the projected GDP per capita growth rate of {name} over the next decade?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Growth projection",
                "value": pct_str(p["growthProjection"]),
                "raw_value": p["growthProjection"],
            }
        ],
    )

    # Q9: Growth projection rank
    emit(
        f"What is {name}'s growth projection rank?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Growth projection rank",
                "value": rank_str(p["growthProjectionRank"]),
            }
        ],
    )


def gen_total_export_values(country_data: dict) -> None:
    """Section 3.2 — Total Export Values (API-sourced only)."""
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    url = atlas_url(iso, "export-basket")
    cat_id = "cp_total_export_values"
    cat_name = "Total Export Values (Country Page)"

    # Q12: Total exports
    emit(
        f"What is the total value of exports for {name} according to the Atlas country page?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Total exports",
                "value": format_usd(p["exportValue"]),
                "raw_value": p["exportValue"],
            }
        ],
    )

    # Q13: Exporter rank
    emit(
        f"What is {name}'s exporter rank?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "Exporter rank", "value": rank_str(p["exportValueRank"])}],
    )

    # Q14: Current account balance
    ca = p["currentAccount"]
    ca_val = ca["quantity"]
    emit(
        f"What is {name}'s current account balance?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Current account balance",
                "value": format_usd(ca_val),
                "raw_value": ca_val,
                "year": str(ca["year"]),
                "status": "deficit" if ca_val < 0 else "surplus",
            }
        ],
    )

    # Q17: Total imports
    emit(
        f"What is the total value of imports for {name} according to the Atlas?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Total imports",
                "value": format_usd(p["importValue"]),
                "raw_value": p["importValue"],
            }
        ],
    )

    # Q18: Trade surplus/deficit
    surplus = p["exportValue"] > p["importValue"]
    emit(
        f"Does {name} have a trade surplus or trade deficit?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Trade balance",
                "value": "trade surplus" if surplus else "trade deficit",
                "export_value": p["exportValue"],
                "import_value": p["importValue"],
            }
        ],
    )


def gen_sectoral_export_composition(country_data: dict) -> None:
    """Section 3.3 — Sectoral Export Composition."""
    exports = country_data["exports"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    url = atlas_url(iso, "export-basket")
    cat_id = "cp_sectoral_composition"
    cat_name = "Sectoral Export Composition (Country Page)"

    total_export = sum(e["exportValue"] for e in exports)

    # Q19: Top product + share
    top = exports[0]
    share = top["exportValue"] / total_export
    emit(
        f"What is the top product in {name}'s export basket and what share does it represent?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "product_name": top["product"]["shortName"],
                "hs92_code": top["product"]["code"],
                "export_value": format_usd(top["exportValue"]),
                "raw_export_value": top["exportValue"],
                "share": pct_str(share),
            }
        ],
    )

    # Q20: Top 3 products
    top3 = exports[:3]
    data = []
    for i, e in enumerate(top3):
        s = e["exportValue"] / total_export
        data.append(
            {
                "rank": i + 1,
                "product_name": e["product"]["shortName"],
                "hs92_code": e["product"]["code"],
                "export_value": format_usd(e["exportValue"]),
                "raw_export_value": e["exportValue"],
                "share": pct_str(s),
            }
        )
    emit(
        f"What are the top 3 products in {name}'s export basket by share?",
        cat_id,
        cat_name,
        "medium",
        url,
        data,
    )

    # Q21: Gross export value of top product
    emit(
        f"What is the gross export value of {top['product']['shortName']} from {name}?",
        cat_id,
        cat_name,
        "medium",
        url,
        [
            {
                "product_name": top["product"]["shortName"],
                "hs92_code": top["product"]["code"],
                "export_value": format_usd(top["exportValue"]),
                "raw_export_value": top["exportValue"],
            }
        ],
    )

    # Q22: HS92 code of top product
    emit(
        f"What is the HS92 code for {top['product']['shortName']} exported by {name}?",
        cat_id,
        cat_name,
        "hard",
        url,
        [
            {
                "product_name": top["product"]["shortName"],
                "hs92_code": top["product"]["code"],
            }
        ],
    )


def gen_trade_partners(country_data: dict) -> None:
    """Section 3.4 — Trade Partners & Market Position (API-sourced only)."""
    partners = country_data["partners"]
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_trade_partners"
    cat_name = "Trade Partners and Market Position (Country Page)"

    # Q23: Top 3 export destinations
    url_eb = atlas_url(iso, "export-basket")
    total = sum(pt["exportValue"] for pt in partners)
    top3 = partners[:3]
    data = []
    for i, pt in enumerate(top3):
        share = pt["exportValue"] / total if total > 0 else 0
        data.append(
            {
                "rank": i + 1,
                "country": pt["location"]["shortName"],
                "share": pct_str(share),
                "export_value": format_usd(pt["exportValue"]),
            }
        )
    emit(
        f"What are the top 3 export destination countries for {name}?",
        cat_id,
        cat_name,
        "medium",
        url_eb,
        data,
    )

    # Q25: Largest market share sector
    url_ms = atlas_url(iso, "market-share")
    sector = p["marketShareMainSector"]
    emit(
        f"In which sector does {name} have the largest global market share?",
        cat_id,
        cat_name,
        "easy",
        url_ms,
        [
            {
                "metric": "Largest market share sector",
                "sector": sector["shortName"],
                "sector_code": sector["code"],
            }
        ],
    )


def gen_growth_performance(country_data: dict) -> None:
    """Section 3.5 — Growth & Performance (API-sourced only)."""
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_growth_performance"
    cat_name = "Growth and Performance (Country Page)"

    # Q31: ECI value
    url_gd = atlas_url(iso, "growth-dynamics")
    emit(
        f"What is {name}'s ECI value according to the growth dynamics chart?",
        cat_id,
        cat_name,
        "medium",
        url_gd,
        [
            {
                "metric": "ECI value",
                "value": f"{p['latestEci']:.4f}",
                "raw_value": p["latestEci"],
            }
        ],
    )

    # Q34: Gross export value of top product (already covered in 3.3 with different product focus)
    exports = country_data["exports"]
    top = exports[0]
    url_eb = atlas_url(iso, "export-basket")
    emit(
        f"What is the gross country export value of {top['product']['shortName']} from {name}?",
        cat_id,
        cat_name,
        "hard",
        url_eb,
        [
            {
                "product_name": top["product"]["shortName"],
                "export_value": format_usd(top["exportValue"]),
                "raw_export_value": top["exportValue"],
            }
        ],
    )


def gen_economic_complexity(country_data: dict) -> None:
    """Section 3.6 — Economic Complexity (API-sourced only)."""
    p = country_data["profile"]
    exports = country_data["exports"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_economic_complexity"
    cat_name = "Economic Complexity (Country Page)"

    # Q37: ECI ranking
    url_main = atlas_url(iso)
    emit(
        f"What is {name}'s Economic Complexity Index (ECI) ranking?",
        cat_id,
        cat_name,
        "easy",
        url_main,
        [{"metric": "ECI ranking", "value": rank_str(p["latestEciRank"])}],
    )

    # Q40: ECI ranking (export complexity page)
    url_ec = atlas_url(iso, "export-complexity")
    emit(
        f"What is {name}'s ECI ranking according to the export complexity page?",
        cat_id,
        cat_name,
        "easy",
        url_ec,
        [{"metric": "ECI ranking", "value": rank_str(p["latestEciRank"])}],
    )

    # Q42: PCI of top product
    top = exports[0]
    emit(
        f"What is the Product Complexity Index (PCI) of {top['product']['shortName']} exported by {name}?",
        cat_id,
        cat_name,
        "hard",
        url_ec,
        [
            {
                "product_name": top["product"]["shortName"],
                "hs92_code": top["product"]["code"],
                "pci": f"{top['pci']:.4f}" if top["pci"] is not None else "N/A",
                "raw_pci": top["pci"],
            }
        ],
    )


def gen_diversification(country_data: dict) -> None:
    """Section 3.7 — Diversification Strategies."""
    p = country_data["profile"]
    np = country_data["new_products"]
    comp = country_data["comparison"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    url = atlas_url(iso, "new-products")
    cat_id = "cp_diversification"
    cat_name = "Diversification Strategies (Country Page)"

    # Q44: Diversification grade
    emit(
        f"What is {name}'s economic diversification grade?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "Diversification grade", "value": p["diversificationGrade"]}],
    )

    # Q45: Diversity rank
    emit(
        f"What is {name}'s diversity rank?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "Diversity rank", "value": rank_str(p["diversityRank"])}],
    )

    # Q47: New products count
    emit(
        f"How many new products has {name} started exporting in the last 15 years?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "New products count", "value": np["newProductCount"]}],
    )

    # Q48: Per-capita income contribution of new products
    emit(
        f"What is the per-capita income contribution of {name}'s new products?",
        cat_id,
        cat_name,
        "medium",
        url,
        [
            {
                "metric": "New product export value per capita",
                "value": f"${np['newProductExportValuePerCapita']}",
            }
        ],
    )

    # Q49: Total value of new products
    emit(
        f"What is the total value of {name}'s new export products?",
        cat_id,
        cat_name,
        "medium",
        url,
        [
            {
                "metric": "New product export value",
                "value": format_usd(np["newProductExportValue"]),
                "raw_value": np["newProductExportValue"],
            }
        ],
    )

    # Q50: Share of new products in export basket
    total_exports = p["exportValue"]
    new_val = np["newProductExportValue"]
    share = new_val / total_exports if total_exports > 0 else 0
    emit(
        f"What share of {name}'s export basket is made up of new products?",
        cat_id,
        cat_name,
        "medium",
        url,
        [
            {
                "metric": "New product share of exports",
                "value": pct_str(share),
                "new_product_export_value": new_val,
                "total_export_value": total_exports,
            }
        ],
    )

    # Q51: Peer country comparison
    data = [
        {
            "country": name,
            "new_products": np["newProductCount"],
            "usd_per_capita": f"${np['newProductExportValuePerCapita']}",
            "usd_total": format_usd(np["newProductExportValue"]),
        }
    ]
    for c in comp:
        data.append(
            {
                "country": c["location"]["shortName"],
                "new_products": c["newProductCount"],
                "usd_per_capita": f"${c['newProductExportValuePerCapita']}",
                "usd_total": format_usd(c["newProductExportValue"]),
            }
        )
    emit(
        f"How does {name}'s new product count compare to peer countries?",
        cat_id,
        cat_name,
        "hard",
        url,
        data,
    )


def gen_product_space_strategy(country_data: dict) -> None:
    """Section 3.8 — Product Space & Strategic Approach (API-sourced)."""
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_product_space"
    cat_name = "Product Space and Strategic Approach (Country Page)"

    # Q52: Diversity (RCA > 1 count)
    url_paths = atlas_url(iso, "paths")
    emit(
        f"How many products does {name} export with a revealed comparative advantage (RCA > 1)?",
        cat_id,
        cat_name,
        "easy",
        url_paths,
        [{"metric": "Products with RCA > 1", "value": p["diversity"]}],
    )

    # Q53: COI rank
    emit(
        f"What is {name}'s Complexity Outlook Index rank?",
        cat_id,
        cat_name,
        "easy",
        url_paths,
        [{"metric": "COI rank", "value": rank_str(p["latestCoiRank"])}],
    )

    # Q54: Strategic approach
    # Map coiClassification/policyRecommendation to human labels
    policy_labels = {
        "LightTouch": "Light Touch Approach",
        "Parsimonious": "Parsimonious Industrial Policy Approach",
        "StrategicBets": "Strategic Bets Approach",
        "TechnologicalFrontier": "Technological Frontier Approach",
    }
    url_sa = atlas_url(iso, "strategic-approach")
    approach = policy_labels.get(p["policyRecommendation"], p["policyRecommendation"])
    emit(
        f"What strategic approach does the Atlas recommend for {name}?",
        cat_id,
        cat_name,
        "easy",
        url_sa,
        [
            {
                "metric": "Recommended strategic approach",
                "value": approach,
                "raw_value": p["policyRecommendation"],
            }
        ],
    )


def gen_growth_opportunities(country_data: dict) -> None:
    """Section 3.9 — Growth Opportunities (non-frontier only)."""
    p = country_data["profile"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_growth_opportunities"
    cat_name = "Growth Opportunities (Country Page)"

    policy_labels = {
        "LightTouch": "Light Touch Approach",
        "Parsimonious": "Parsimonious Industrial Policy Approach",
        "StrategicBets": "Strategic Bets Approach",
        "TechnologicalFrontier": "Technological Frontier Approach",
    }

    # Q57: Default product selection strategy
    url_go = atlas_url(iso, "growth-opportunities")
    approach = policy_labels.get(p["policyRecommendation"], p["policyRecommendation"])
    emit(
        f"What product selection strategy is shown by default for {name}'s growth opportunities?",
        cat_id,
        cat_name,
        "easy",
        url_go,
        [
            {
                "metric": "Default strategy",
                "value": approach,
                "raw_value": p["policyRecommendation"],
            }
        ],
    )


def gen_frontier_edge_cases(country_data: dict) -> None:
    """Section 3.10 — Frontier Edge Cases (browser-verified but structurally known)."""
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    cat_id = "cp_frontier_edge_cases"
    cat_name = "Frontier Edge Cases (Country Page)"

    # Q61: Growth opportunities unavailable
    url_go = atlas_url(iso, "growth-opportunities")
    emit(
        f"Does the Atlas show growth opportunity products for {name}?",
        cat_id,
        cat_name,
        "easy",
        url_go,
        [
            {
                "available": False,
                "message": "Visualization not available for highest complexity countries",
            }
        ],
    )

    # Q62: Product table unavailable
    url_pt = atlas_url(iso, "product-table")
    emit(
        f"Does the Atlas show a product opportunities table for {name}?",
        cat_id,
        cat_name,
        "easy",
        url_pt,
        [
            {
                "available": False,
                "message": "Visualization not available for highest complexity countries",
            }
        ],
    )


def gen_summary_crosscheck(country_data: dict) -> None:
    """Section 3.11 — Summary Page Cross-Check (API-sourced)."""
    p = country_data["profile"]
    np = country_data["new_products"]
    name = country_data["name"]
    iso = COUNTRIES[name]["iso"]
    url = atlas_url(iso, "summary")
    cat_id = "cp_summary"
    cat_name = "Summary Page Cross-Check (Country Page)"

    # Q64: New products on summary
    emit(
        f"How many new products are shown on {name}'s summary page?",
        cat_id,
        cat_name,
        "easy",
        url,
        [{"metric": "New products count", "value": np["newProductCount"]}],
    )

    # Q65: Growth projection on summary
    emit(
        f"What growth projection is shown on {name}'s summary page?",
        cat_id,
        cat_name,
        "easy",
        url,
        [
            {
                "metric": "Growth projection",
                "value": pct_str(p["growthProjection"]),
                "raw_value": p["growthProjection"],
            }
        ],
    )

    # Q66: Strategic approach on summary
    policy_labels = {
        "LightTouch": "Light Touch Approach",
        "Parsimonious": "Parsimonious Industrial Policy Approach",
        "StrategicBets": "Strategic Bets Approach",
        "TechnologicalFrontier": "Technological Frontier Approach",
    }
    approach = policy_labels.get(p["policyRecommendation"], p["policyRecommendation"])
    emit(
        f"What strategic approach is described on {name}'s summary page?",
        cat_id,
        cat_name,
        "medium",
        url,
        [
            {
                "metric": "Strategic approach",
                "value": approach,
                "raw_value": p["policyRecommendation"],
            }
        ],
    )


# ---------------------------------------------------------------------------
# Country-to-category assignment (from guide section 2)
# ---------------------------------------------------------------------------

ASSIGNMENTS: dict[str, list[str]] = {
    # category_generator_name -> [country_names]
    "country_profile_overview": ["Kenya", "Spain"],
    "total_export_values": ["Brazil", "Germany"],
    "sectoral_export_composition": ["India", "USA"],
    "trade_partners": ["Turkiye", "Ethiopia"],
    "growth_performance": ["Spain", "India"],
    "economic_complexity": ["Brazil", "Turkiye"],
    "diversification": ["Kenya", "Ethiopia"],
    "product_space_strategy": ["Kenya", "Turkiye"],
    "growth_opportunities": ["Kenya", "India"],  # non-frontier only
    "frontier_edge_cases": ["USA", "Germany"],  # frontier only
    "summary_crosscheck": ["Turkiye", "Brazil"],
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    global SEM
    SEM = asyncio.Semaphore(2)

    print("Fetching data from Atlas GraphQL API...")

    async with httpx.AsyncClient() as client:
        # Fetch all 8 countries in parallel
        tasks = {
            name: fetch_all_data(client, name, info["id"])
            for name, info in COUNTRIES.items()
        }
        results = {}
        for name, task in tasks.items():
            results[name] = await task
            print(f"  ✓ {name}")

    print(f"\nGenerating questions (starting at ID {QID[0]})...\n")

    # Generate questions per assignment
    for countries in ASSIGNMENTS["country_profile_overview"]:
        gen_country_profile_overview(results[countries])

    for countries in ASSIGNMENTS["total_export_values"]:
        gen_total_export_values(results[countries])

    for countries in ASSIGNMENTS["sectoral_export_composition"]:
        gen_sectoral_export_composition(results[countries])

    for countries in ASSIGNMENTS["trade_partners"]:
        gen_trade_partners(results[countries])

    for countries in ASSIGNMENTS["growth_performance"]:
        gen_growth_performance(results[countries])

    for countries in ASSIGNMENTS["economic_complexity"]:
        gen_economic_complexity(results[countries])

    for countries in ASSIGNMENTS["diversification"]:
        gen_diversification(results[countries])

    for countries in ASSIGNMENTS["product_space_strategy"]:
        gen_product_space_strategy(results[countries])

    for countries in ASSIGNMENTS["growth_opportunities"]:
        gen_growth_opportunities(results[countries])

    for countries in ASSIGNMENTS["frontier_edge_cases"]:
        gen_frontier_edge_cases(results[countries])

    for countries in ASSIGNMENTS["summary_crosscheck"]:
        gen_summary_crosscheck(results[countries])

    # Print summary
    print(f"Generated {len(ALL_QUESTIONS)} questions (IDs 61-{QID[0] - 1})")

    # Write the questions list for later integration into eval_questions.json
    out_path = BASE_DIR / "new_country_page_questions.json"
    new_categories = [
        {
            "id": "country_profile_overview",
            "name": "Country Profile Overview",
            "description": "Questions about GDP, population, income classification, and growth projections from Atlas country pages",
        },
        {
            "id": "cp_total_export_values",
            "name": "Total Export Values (Country Page)",
            "description": "Questions about total export/import values and trade balance from Atlas country pages",
        },
        {
            "id": "cp_sectoral_composition",
            "name": "Sectoral Export Composition (Country Page)",
            "description": "Questions about top products, export shares, and HS codes from Atlas country page treemaps",
        },
        {
            "id": "cp_trade_partners",
            "name": "Trade Partners and Market Position (Country Page)",
            "description": "Questions about export destinations and market share sectors from Atlas country pages",
        },
        {
            "id": "cp_growth_performance",
            "name": "Growth and Performance (Country Page)",
            "description": "Questions about ECI values and product export values from Atlas country pages",
        },
        {
            "id": "cp_economic_complexity",
            "name": "Economic Complexity (Country Page)",
            "description": "Questions about ECI rankings and product complexity from Atlas country pages",
        },
        {
            "id": "cp_diversification",
            "name": "Diversification Strategies (Country Page)",
            "description": "Questions about diversification grades, new products, and peer comparisons from Atlas country pages",
        },
        {
            "id": "cp_product_space",
            "name": "Product Space and Strategic Approach (Country Page)",
            "description": "Questions about RCA counts, COI rankings, and recommended strategies from Atlas country pages",
        },
        {
            "id": "cp_growth_opportunities",
            "name": "Growth Opportunities (Country Page)",
            "description": "Questions about product opportunities and strategies from Atlas country pages (non-frontier only)",
        },
        {
            "id": "cp_frontier_edge_cases",
            "name": "Frontier Edge Cases (Country Page)",
            "description": "Questions verifying data unavailability for highest-complexity frontier countries",
        },
        {
            "id": "cp_summary",
            "name": "Summary Page Cross-Check (Country Page)",
            "description": "Questions cross-checking summary page stats against detail pages",
        },
    ]

    output = {
        "new_categories": new_categories,
        "new_questions": ALL_QUESTIONS,
    }
    out_path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"\nWrote integration file: {out_path}")

    # Print category breakdown
    from collections import Counter

    cats = Counter(q["category_id"] for q in ALL_QUESTIONS)
    print("\nQuestions by category:")
    for cat_id, count in cats.most_common():
        print(f"  {cat_id}: {count}")

    # Print browser-only questions summary (not generated here)
    print("\n--- Browser-only questions (Layer 2, not generated) ---")
    browser_templates = [
        "GDP per capita growth (5-year avg) — main page",
        "GDP growth vs regional average — main page",
        "ECI rank change description — main page",
        "Complexity trend driver — main page",
        "Complexity-income relationship — main page",
        "Projected growth speed — main page",
        "Export growth rate (5-year avg) — export-basket",
        "Non-oil export growth rate — export-basket",
        "Growth pattern description — growth-dynamics",
        "Sectors driving growth — growth-dynamics",
        "Structural transformation status — market-share",
        "Growth mechanism description — market-share",
        "Strategic approach description — strategic-approach",
        "Sectors driving export growth — market-share",
        "Complexity trend — export-complexity",
        "High-potential sectors — product-table",
    ]
    for t in browser_templates:
        print(f"  • {t}")


if __name__ == "__main__":
    asyncio.run(main())
