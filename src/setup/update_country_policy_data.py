#!/usr/bin/env python3
"""Fetch country policy data from the Atlas GraphQL API and write to CSV.

Queries the Country Pages API for policyRecommendation, ECI, and GDP per
capita for all ~145 countries, then writes the result to
``src/data/country_policy_data.csv``.

This data is used by the growth opportunity composite scoring in
``graphql_pipeline.py`` to select per-country weighting strategies.

Usage:
    uv run python src/setup/update_country_policy_data.py

Run quarterly (or after each Atlas data ingestion) to keep weights and
PCI ceiling thresholds current.  See the GitHub issue for cron automation.

Rate limits:
    The Atlas GraphQL API allows ~120 req/min.  This script stays well
    under that by batching 10 countryProfile lookups into a single
    aliased query and sleeping 1s between batches (~10 total requests).
"""

import csv
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

COUNTRY_PAGES_URL = "https://atlas.hks.harvard.edu/api/countries/graphql"
OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "country_policy_data.csv"
)

# Batch 10 countryProfile queries per request via aliases.
# 145 countries / 10 = 15 requests + 1 for allCountryProfiles = 16 total.
BATCH_SIZE = 10
# Sleep between API requests to stay under 2 req/s / 120 req/min.
REQUEST_DELAY_S = 1.0


def _graphql_request(query: str) -> dict:
    """Send a GraphQL query and return the parsed response."""
    req = urllib.request.Request(
        COUNTRY_PAGES_URL,
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _batch_country_profiles(location_ids: list[str]) -> dict[str, dict]:
    """Fetch countryProfile for many countries using GraphQL aliases.

    Each batch packs up to BATCH_SIZE countryProfile queries into a
    single GraphQL request using aliases, keeping total request count low.

    Returns:
        Dict mapping location ID to {"eci": float|None, "gdppc": int|None}.
    """
    results: dict[str, dict] = {}
    total_batches = (len(location_ids) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx, batch_start in enumerate(range(0, len(location_ids), BATCH_SIZE)):
        batch = location_ids[batch_start : batch_start + BATCH_SIZE]
        logger.info(
            "  Batch %d/%d (%d countries)...", batch_idx + 1, total_batches, len(batch)
        )

        # Build aliased query: c404: countryProfile(location: "location-404") { ... }
        parts = []
        for loc_id in batch:
            m49 = loc_id.replace("location-", "")
            alias = f"c{m49}"
            parts.append(
                f'{alias}: countryProfile(location: "{loc_id}") '
                "{ latestEci latestGdpPerCapita { quantity } }"
            )
        query = "{\n" + "\n".join(parts) + "\n}"

        try:
            resp = _graphql_request(query)
            if "errors" in resp:
                logger.warning(
                    "GraphQL errors in batch %d: %s", batch_idx + 1, resp["errors"]
                )
            data = resp.get("data", {})
            for loc_id in batch:
                m49 = loc_id.replace("location-", "")
                alias = f"c{m49}"
                cp = data.get(alias) or {}
                gdppc_obj = cp.get("latestGdpPerCapita")
                results[loc_id] = {
                    "eci": cp.get("latestEci"),
                    "gdppc": gdppc_obj["quantity"] if gdppc_obj else None,
                }
        except Exception as e:
            logger.warning("Batch %d request failed: %s", batch_idx + 1, e)
            for loc_id in batch:
                results[loc_id] = {"eci": None, "gdppc": None}

        # Respect rate limits
        if batch_start + BATCH_SIZE < len(location_ids):
            time.sleep(REQUEST_DELAY_S)

    return results


def fetch_country_policy_data() -> list[dict]:
    """Fetch policy recommendations, ECI, and GDP per capita for all countries.

    Returns:
        List of dicts with keys: country_id, country_name,
        policy_recommendation, eci, gdp_per_capita.
    """
    query_profiles = """
    {
      allCountryProfiles {
        location { id shortName }
        policyRecommendation
      }
    }
    """

    logger.info("Querying allCountryProfiles...")
    result_profiles = _graphql_request(query_profiles)
    if "errors" in result_profiles:
        logger.error("GraphQL errors: %s", result_profiles["errors"])
        sys.exit(1)

    profiles = result_profiles["data"]["allCountryProfiles"]
    location_ids = [p["location"]["id"] for p in profiles]

    # Wait before next request
    time.sleep(REQUEST_DELAY_S)

    logger.info(
        "Fetching ECI + GDPPC for %d countries in batches of %d...",
        len(location_ids),
        BATCH_SIZE,
    )
    country_data = _batch_country_profiles(location_ids)

    rows = []
    for p in profiles:
        loc_id = p["location"]["id"]
        m49 = int(loc_id.replace("location-", ""))
        name = p["location"]["shortName"]
        policy = p["policyRecommendation"]
        cd = country_data.get(loc_id, {})
        eci = cd.get("eci")
        gdppc = cd.get("gdppc")

        rows.append(
            {
                "country_id": m49,
                "country_name": name,
                "policy_recommendation": policy,
                "eci": round(eci, 6) if eci is not None else "",
                "gdp_per_capita": int(gdppc) if gdppc is not None else "",
            }
        )

    rows.sort(key=lambda r: r["country_id"])
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    """Write country policy data to CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "country_id",
        "country_name",
        "policy_recommendation",
        "eci",
        "gdp_per_capita",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


def main() -> None:
    rows = fetch_country_policy_data()

    # Summary statistics
    from collections import Counter

    policies = Counter(r["policy_recommendation"] for r in rows)
    logger.info("Policy recommendation distribution:")
    for policy, count in policies.most_common():
        logger.info("  %s: %d countries", policy, count)

    missing_eci = sum(1 for r in rows if r["eci"] == "")
    missing_gdppc = sum(1 for r in rows if r["gdp_per_capita"] == "")
    if missing_eci or missing_gdppc:
        logger.warning(
            "Missing data: %d without ECI, %d without GDPPC",
            missing_eci,
            missing_gdppc,
        )

    write_csv(rows, OUTPUT_PATH)
    logger.info("Done. CSV is ready for use by graphql_pipeline.py.")


if __name__ == "__main__":
    main()
