#!/usr/bin/env python3
"""Build the product search index for embedding-based product code lookup.

Connects to the Atlas database (or test DB), fetches all products across
classification schemas, embeds them with OpenAI text-embedding-3-small,
and stores in a SQLite database with FTS5 + sqlite-vec.

Usage::

    # Against test DB (docker-compose.test.yml)
    ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
        uv run python scripts/build_product_search_index.py

    # Custom output path
    uv run python scripts/build_product_search_index.py --output src/product_search.db

    # Force rebuild (ignore existing index)
    uv run python scripts/build_product_search_index.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import struct
import sys
from pathlib import Path

from dotenv import load_dotenv

# Add project root to sys.path so we can import src modules
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)

# Embedding settings (must match src/product_search.py)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 768
EMBED_CONCURRENCY = 50  # Max concurrent embedding API calls
EMBED_BATCH_SIZE = 100  # Texts per API call

SCHEMAS = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "hs22": "classification.product_hs22",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
}

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    schema TEXT NOT NULL,
    product_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    product_id TEXT NOT NULL,
    product_level TEXT NOT NULL,
    enriched_text TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_schema_code
    ON products(schema, product_code);

CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    product_name,
    enriched_text,
    content='products',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
    INSERT INTO products_fts(rowid, product_name, enriched_text)
    VALUES (new.rowid, new.product_name, new.enriched_text);
END;

CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, product_name, enriched_text)
    VALUES ('delete', old.rowid, old.product_name, old.enriched_text);
END;

CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, product_name, enriched_text)
    VALUES ('delete', old.rowid, old.product_name, old.enriched_text);
    INSERT INTO products_fts(rowid, product_name, enriched_text)
    VALUES (new.rowid, new.product_name, new.enriched_text);
END;

-- Metadata table for tracking build state
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize a float list to little-endian bytes for sqlite-vec."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


# ---------------------------------------------------------------------------
# Fetch products from Atlas DB
# ---------------------------------------------------------------------------


def fetch_products(db_url: str) -> list[dict]:
    """Fetch all products from all classification schemas."""
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    all_products = []

    for schema_name, table_name in SCHEMAS.items():
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text(f"""
                    SELECT DISTINCT
                        code AS product_code,
                        name_short_en AS product_name,
                        product_id,
                        product_level
                    FROM {table_name}
                    WHERE name_short_en IS NOT NULL
                      AND code IS NOT NULL
                    ORDER BY code
                    """)
                )
                rows = result.fetchall()
                for row in rows:
                    all_products.append(
                        {
                            "schema": schema_name,
                            "product_code": str(row[0]),
                            "product_name": str(row[1]),
                            "product_id": str(row[2]),
                            "product_level": str(row[3]),
                        }
                    )
                logger.info(
                    "Fetched %d products from %s (%s)",
                    len(rows),
                    table_name,
                    schema_name,
                )
        except Exception as e:
            logger.warning("Failed to fetch from %s: %s", table_name, e)

    logger.info("Total products fetched: %d", len(all_products))
    return all_products


def _build_enriched_text(
    product: dict,
    enrichment: dict[str, dict[str, str]] | None = None,
) -> str:
    """Build enriched text for embedding a product.

    Uses synonym-enriched text (name + LLM synonyms) when available,
    falling back to the raw product name.
    """
    code = product["product_code"]
    name = product["product_name"]
    schema = product["schema"]

    if enrichment and schema in enrichment:
        return enrichment[schema].get(code, name)
    return name


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts via OpenAI text-embedding-3-small.

    Batches requests to stay within API limits.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    all_embeddings: list[list[float] | None] = [None] * len(texts)
    semaphore = asyncio.Semaphore(EMBED_CONCURRENCY)

    async def _embed_batch(batch_texts: list[str], start_idx: int) -> None:
        async with semaphore:
            response = await client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch_texts,
                dimensions=EMBEDDING_DIM,
            )
            for i, item in enumerate(response.data):
                all_embeddings[start_idx + i] = item.embedding

    from tqdm.asyncio import tqdm as atqdm

    tasks = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        tasks.append(_embed_batch(batch, i))

    await atqdm.gather(*tasks, desc="Embedding products", unit="batch")

    return all_embeddings


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------


def create_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the SQLite database with required schema."""
    import sqlite_vec

    conn = sqlite3.connect(str(db_path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.executescript(SCHEMA_SQL)

    # Create the vector table (can't be in executescript due to virtual table)
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS product_embeddings
        USING vec0(embedding float[{EMBEDDING_DIM}])
    """)
    conn.commit()
    return conn


async def build_index(
    db_url: str,
    output_path: Path,
    force: bool = False,
    enrichment_cache_path: Path | None = None,
    rebuild_enrichment: bool = False,
) -> None:
    """Main build pipeline: fetch → enrich → embed → store."""
    from src.product_enrichment import (
        build_enrichment,
        load_enrichment_cache,
        save_enrichment_cache,
    )

    if output_path.exists() and not force:
        logger.info("Index already exists at %s. Use --force to rebuild.", output_path)
        return

    if output_path.exists():
        output_path.unlink()

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Fetch products
    logger.info("Fetching products from database...")
    products = fetch_products(db_url)
    if not products:
        logger.error("No products found. Check your ATLAS_DB_URL.")
        return

    # Step 1.5: Build or load synonym enrichment
    enrichment: dict[str, dict[str, str]] | None = None
    if enrichment_cache_path is not None:
        if not rebuild_enrichment:
            enrichment = load_enrichment_cache(enrichment_cache_path)
            if enrichment:
                logger.info("Loaded enrichment cache from %s", enrichment_cache_path)

        if enrichment is None:
            logger.info("Building synonym enrichment via LLM...")
            # Reorganize products into catalog format for enrichment
            catalogs: dict[str, list[dict]] = {}
            for p in products:
                catalogs.setdefault(p["schema"], []).append(p)
            enrichment = await build_enrichment(catalogs)
            save_enrichment_cache(enrichment, enrichment_cache_path)

    # Step 2: Build enriched text
    logger.info("Building enriched text for %d products...", len(products))
    for p in products:
        p["enriched_text"] = _build_enriched_text(p, enrichment)

    # Step 3: Embed
    logger.info("Embedding %d products...", len(products))
    texts = [p["enriched_text"] for p in products]
    embeddings = await embed_texts(texts)

    # Step 4: Store in SQLite
    logger.info("Writing to SQLite index at %s...", output_path)
    conn = create_db(output_path)

    # Insert products
    for p in products:
        conn.execute(
            """
            INSERT OR REPLACE INTO products
                (schema, product_code, product_name, product_id, product_level, enriched_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                p["schema"],
                p["product_code"],
                p["product_name"],
                p["product_id"],
                p["product_level"],
                p["enriched_text"],
            ),
        )
    conn.commit()

    # Insert embeddings (must match rowid order)
    cursor = conn.execute("SELECT rowid FROM products ORDER BY rowid")
    rowids = [row[0] for row in cursor.fetchall()]

    for rowid, embedding in zip(rowids, embeddings):
        if embedding is not None:
            conn.execute(
                "INSERT INTO product_embeddings (rowid, embedding) VALUES (?, ?)",
                (rowid, _serialize_embedding(embedding)),
            )
    conn.commit()

    # Store metadata
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("product_count", str(len(products))),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("embedding_model", EMBEDDING_MODEL),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("embedding_dim", str(EMBEDDING_DIM)),
    )
    conn.commit()
    conn.close()

    logger.info(
        "Done! Index built with %d products at %s (%.1f MB)",
        len(products),
        output_path,
        output_path.stat().st_size / 1024 / 1024,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build product search index for embedding-based lookup"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "src" / "product_search.db",
        help="Output SQLite database path (default: src/product_search.db)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even if index exists",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Atlas DB URL (default: from ATLAS_DB_URL env var)",
    )
    parser.add_argument(
        "--enrichment-cache",
        type=Path,
        default=PROJECT_ROOT / "data" / "product_enrichment_cache.json",
        help="Path to enrichment cache JSON (default: data/product_enrichment_cache.json)",
    )
    parser.add_argument(
        "--rebuild-enrichment",
        action="store_true",
        help="Force rebuild of synonym enrichment cache",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    import os

    db_url = args.db_url or os.environ.get("ATLAS_DB_URL")
    if not db_url:
        logger.error("ATLAS_DB_URL not set. Pass --db-url or set the env var.")
        sys.exit(1)

    asyncio.run(
        build_index(
            db_url,
            args.output,
            force=args.force,
            enrichment_cache_path=args.enrichment_cache,
            rebuild_enrichment=args.rebuild_enrichment,
        )
    )


if __name__ == "__main__":
    main()
