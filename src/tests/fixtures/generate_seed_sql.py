#!/usr/bin/env python3
"""Generate seed.sql from the production Atlas DB for integration tests.

Connects to the production database (via ATLAS_DB_URL) and extracts:
- Full copies of classification and public schema tables (lookup/reference data)
- Filtered samples of trade data for 10 countries over 3 years (2019-2021)
- DDL-only for very large tables not needed for tests (_6 level, group_group, etc.)

Usage:
    uv run python src/tests/fixtures/generate_seed_sql.py
"""

import json
import math
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

import psycopg2

# ── Configuration ─────────────────────────────────────────────────────────────

SAMPLE_COUNTRIES = [
    "BOL",
    "IND",
    "USA",
    "DEU",
    "FRA",
    "KEN",
    "CHN",
    "JPN",
    "BRA",
    "GBR",
    "NGA",  # Nigeria — needed for eval question 2 (crude oil exports)
]
YEAR_MIN, YEAR_MAX = 2019, 2021

FULL_COPY_SCHEMAS = ["public", "classification"]
TRADE_SCHEMAS = ["hs92", "hs12", "sitc", "services_unilateral", "services_bilateral"]
ALL_SCHEMAS = FULL_COPY_SCHEMAS + TRADE_SCHEMAS

# Only extract bilateral 4-digit data for these schemas (keeps file size down)
BILATERAL_DATA_SCHEMAS = {"hs92"}

INSERT_BATCH_SIZE = 100
OUTPUT_FILE = Path(__file__).parent / "seed.sql"
BASE_DIR = Path(__file__).resolve().parents[3]
STRUCTURE_FILE = BASE_DIR / "db_table_structure.json"

# Load environment
load_dotenv(BASE_DIR / ".env")

PROD_DB_URL = os.environ.get("ATLAS_DB_URL")
if not PROD_DB_URL:
    print(
        "ERROR: ATLAS_DB_URL not set. Add it to .env or set env var.", file=sys.stderr
    )
    sys.exit(1)


# ── Value formatting ──────────────────────────────────────────────────────────


def format_value(val: object) -> str:
    """Format a Python value as a SQL literal."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return "NULL"
        return repr(val)
    if isinstance(val, Decimal):
        if val.is_nan() or val.is_infinite():
            return "NULL"
        return str(val)
    if isinstance(val, int):
        return str(val)
    if isinstance(val, (date, datetime)):
        return f"'{val.isoformat()}'"
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return "'" + str(val).replace("'", "''") + "'"


def batch_inserts(qualified: str, col_names: list[str], rows: list[tuple]) -> list[str]:
    """Generate batch INSERT statements (INSERT_BATCH_SIZE rows each)."""
    if not rows:
        return []
    col_list = ", ".join(col_names)
    stmts: list[str] = []
    for i in range(0, len(rows), INSERT_BATCH_SIZE):
        batch = rows[i : i + INSERT_BATCH_SIZE]
        vals = ",\n".join(
            "(" + ", ".join(format_value(v) for v in row) + ")" for row in batch
        )
        stmts.append(f"INSERT INTO {qualified} ({col_list}) VALUES\n{vals};")
    return stmts


# ── Schema discovery ──────────────────────────────────────────────────────────


def get_tables_from_json() -> dict[str, list[str]]:
    """Get the canonical table list from db_table_structure.json.

    This ensures we only include Atlas tables (not langchain_pg_*, etc.).
    """
    with open(STRUCTURE_FILE) as f:
        structure = json.load(f)
    return {schema: sorted(tables.keys()) for schema, tables in structure.items()}


# ── DDL extraction ────────────────────────────────────────────────────────────


def extract_enums(cur) -> list[tuple[str, list[str]]]:
    """Get all custom ENUM type definitions from the database."""
    cur.execute("""
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_namespace n ON t.typnamespace = n.oid
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
        GROUP BY t.typname
        ORDER BY t.typname
    """)
    return cur.fetchall()


def extract_columns(cur, schema: str, table: str) -> list[tuple[str, str]]:
    """Get (column_name, ddl_type) pairs for a table, ordered by position."""
    cur.execute(
        """
        SELECT column_name,
               CASE
                 WHEN data_type = 'USER-DEFINED' THEN udt_name
                 WHEN data_type = 'character' THEN
                   'CHAR(' || character_maximum_length || ')'
                 WHEN data_type = 'character varying' THEN
                   CASE WHEN character_maximum_length IS NOT NULL
                        THEN 'VARCHAR(' || character_maximum_length || ')'
                        ELSE 'VARCHAR' END
                 WHEN data_type = 'integer' THEN 'INTEGER'
                 WHEN data_type = 'bigint' THEN 'BIGINT'
                 WHEN data_type = 'double precision' THEN 'DOUBLE PRECISION'
                 WHEN data_type = 'boolean' THEN 'BOOLEAN'
                 WHEN data_type = 'text' THEN 'TEXT'
                 WHEN data_type = 'real' THEN 'REAL'
                 WHEN data_type = 'smallint' THEN 'SMALLINT'
                 WHEN data_type = 'numeric' THEN 'NUMERIC'
                 ELSE data_type
               END AS col_type
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return cur.fetchall()


# ── Data extraction filtering ────────────────────────────────────────────────


def should_extract_data(schema: str, table: str) -> bool:
    """Determine if we should extract data (vs DDL-only) for a trade table."""
    if schema in FULL_COPY_SCHEMAS:
        return True

    # Skip 6-digit level tables (extremely large, not needed for tests)
    if table.endswith("_6"):
        return False

    # Skip group-group tables (large, not tested directly)
    if table.startswith("group_group_"):
        return False

    # Skip product similarity tables (not tested)
    if table == "product_product_4":
        return False

    # Skip bilateral lower-granularity tables
    if table in ("country_country_product_year_1", "country_country_product_year_2"):
        return False

    # Bilateral _4 only for primary test schemas (hs92)
    if (
        table == "country_country_product_year_4"
        and schema not in BILATERAL_DATA_SCHEMAS
    ):
        return False

    return True


def build_where(table: str, cid_list: str) -> str | None:
    """Build WHERE clause for a trade-schema table."""
    yr_filter = f"year BETWEEN {YEAR_MIN} AND {YEAR_MAX}"
    cid_filter = f"country_id IN ({cid_list})"

    if table.startswith("country_product_year_"):
        return f"{cid_filter} AND {yr_filter}"

    if table == "country_year":
        return f"{cid_filter} AND {yr_filter}"

    if table == "country_year_thresholds":
        return f"{cid_filter} AND {yr_filter}"

    if table.startswith("country_country_product_year_"):
        return f"{cid_filter} AND partner_id IN ({cid_list}) AND {yr_filter}"

    if table == "country_country_year":
        return f"{cid_filter} AND partner_id IN ({cid_list}) AND {yr_filter}"

    if table.startswith("country_product_lookback_"):
        return f"{cid_filter} AND lookback_year BETWEEN {YEAR_MIN} AND {YEAR_MAX}"

    if table.startswith("product_year_"):
        return yr_filter

    return None


def extract_data(
    cur,
    schema: str,
    table: str,
    col_names: list[str],
    cid_list: str,
) -> list[tuple]:
    """Fetch rows from a table with appropriate filtering."""
    qualified = f"{schema}.{table}" if schema != "public" else table
    col_sql = ", ".join(col_names)

    if schema in FULL_COPY_SCHEMAS:
        cur.execute(f"SELECT {col_sql} FROM {qualified}")
        return cur.fetchall()

    where = build_where(table, cid_list)
    if where:
        cur.execute(f"SELECT {col_sql} FROM {qualified} WHERE {where}")
    else:
        print(f"  WARNING: no filter for {qualified}, doing full copy", file=sys.stderr)
        cur.execute(f"SELECT {col_sql} FROM {qualified}")
    return cur.fetchall()


# ── Main generation ───────────────────────────────────────────────────────────


def generate() -> tuple[str, list[tuple[str, str, int]]]:
    """Generate seed SQL from production data."""
    lines: list[str] = []
    w = lines.append
    stats: list[tuple[str, str, int]] = []

    # Get canonical table list from JSON
    all_tables = get_tables_from_json()

    with psycopg2.connect(PROD_DB_URL) as conn:
        with conn.cursor() as cur:
            print("Discovering schema structure...")
            enums = extract_enums(cur)

            # Resolve sample country_ids
            placeholders = ",".join(["%s"] * len(SAMPLE_COUNTRIES))
            cur.execute(
                f"SELECT country_id FROM classification.location_country "
                f"WHERE iso3_code IN ({placeholders})",
                SAMPLE_COUNTRIES,
            )
            country_ids = [r[0] for r in cur.fetchall()]
            cid_list = ",".join(str(c) for c in country_ids)
            print(f"  Sample country_ids: {country_ids}")

            # ── 1. Header ─────────────────────────────────────────
            w(
                "-- =========================================================================="
            )
            w("-- Seed SQL for ask-atlas integration tests")
            w(
                "-- Extracted from production DB by: src/tests/fixtures/generate_seed_sql.py"
            )
            w(f"-- Countries: {', '.join(SAMPLE_COUNTRIES)}")
            w(f"-- Years: {YEAR_MIN}-{YEAR_MAX}")
            w(
                "-- =========================================================================="
            )
            w("")

            # ── 2. Extensions ─────────────────────────────────────
            w("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            w("")

            # ── 3. Schemas ────────────────────────────────────────
            for schema in ALL_SCHEMAS:
                if schema != "public" and schema in all_tables:
                    w(f"CREATE SCHEMA IF NOT EXISTS {schema};")
            w("")

            # ── 4. ENUM types ─────────────────────────────────────
            w("-- ENUM types")
            for type_name, values in enums:
                quoted = ", ".join(f"'{v}'" for v in values)
                w(
                    f"DO $$ BEGIN CREATE TYPE {type_name} AS ENUM ({quoted}); "
                    f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
                )
            w("")

            # ── 5. Table DDL ──────────────────────────────────────
            schema_order = ["public", "classification"] + TRADE_SCHEMAS
            for schema in schema_order:
                if schema not in all_tables:
                    continue
                w(f"-- ── Schema: {schema} ──")
                for table in all_tables[schema]:
                    cols = extract_columns(cur, schema, table)
                    if not cols:
                        print(
                            f"  WARNING: no columns found for {schema}.{table}, skipping DDL"
                        )
                        continue
                    qualified = f"{schema}.{table}" if schema != "public" else table
                    col_defs = ",\n".join(f"    {cn} {ct}" for cn, ct in cols)
                    w(f"CREATE TABLE IF NOT EXISTS {qualified} (")
                    w(col_defs)
                    w(");")
                    w("")

            # ── 6. Seed data ──────────────────────────────────────
            w(
                "-- =========================================================================="
            )
            w("-- SEED DATA")
            w(
                "-- =========================================================================="
            )
            w("")

            for schema in schema_order:
                if schema not in all_tables:
                    continue
                for table in all_tables[schema]:
                    qualified = f"{schema}.{table}" if schema != "public" else table
                    cols = extract_columns(cur, schema, table)
                    if not cols:
                        continue
                    col_names = [c[0] for c in cols]

                    # Check if we should extract data or just DDL
                    if not should_extract_data(schema, table):
                        stats.append((schema, table, 0))
                        continue

                    print(f"  Extracting {qualified}...", end=" ", flush=True)
                    rows = extract_data(cur, schema, table, col_names, cid_list)
                    stats.append((schema, table, len(rows)))
                    print(f"{len(rows)} rows")

                    if rows:
                        w(f"-- {qualified} ({len(rows)} rows)")
                        for stmt in batch_inserts(qualified, col_names, rows):
                            w(stmt)
                        w("")

    return "\n".join(lines), stats


if __name__ == "__main__":
    print("Connecting to production DB and extracting data...")
    sql, stats = generate()

    OUTPUT_FILE.write_text(sql)
    total_bytes = len(sql.encode())

    # Print summary
    print(f"\n{'Schema':<25} {'Table':<40} {'Rows':>8}")
    print("-" * 75)
    total_rows = 0
    for schema, table, count in stats:
        label = "DDL only" if count == 0 else f"{count:>8,}"
        print(f"{schema:<25} {table:<40} {label}")
        total_rows += count
    print("-" * 75)
    print(f"{'TOTAL':<65} {total_rows:>8,}")
    print(
        f"\nWritten to {OUTPUT_FILE} ({total_bytes:,} bytes, {total_bytes / 1024 / 1024:.1f} MB)"
    )
