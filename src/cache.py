"""In-process caching for expensive deterministic operations.

Provides TTL-based caches for product details lookups, text search, and
table DDL reflection.  The ``CacheRegistry`` tracks hit/miss counters for
observability (exposed via ``/debug/caches``).

Async cached functions use ``cachetools-async`` which includes built-in
request deduplication (stampede prevention): concurrent identical lookups
trigger only one underlying call.
"""

import logging
from typing import Any

from cachetools import TTLCache
from cachetools_async import cached as async_cached
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------

PRODUCT_DETAILS_MAXSIZE = 512
PRODUCT_DETAILS_TTL = 86400  # 24 hours

TEXT_SEARCH_MAXSIZE = 1024
TEXT_SEARCH_TTL = 21600  # 6 hours

TABLE_INFO_MAXSIZE = 32
TABLE_INFO_TTL = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Key normalization helpers
# ---------------------------------------------------------------------------


def product_details_key(codes: list[str], schema: str) -> tuple:
    """Normalize product detail lookup key — order-independent."""
    return (frozenset(sorted(codes)), schema)


def text_search_key(product_to_search: str, schema: str) -> tuple:
    """Normalize text search key — case- and whitespace-insensitive."""
    return (product_to_search.strip().lower(), schema)


def table_info_key(schemas: list[str]) -> frozenset:
    """Normalize table info key — order-independent."""
    return frozenset(schemas)


# ---------------------------------------------------------------------------
# CacheRegistry — manages named caches with hit/miss tracking
# ---------------------------------------------------------------------------


class CacheRegistry:
    """Registry of named TTLCache instances with hit/miss counters."""

    def __init__(self) -> None:
        self._caches: dict[str, TTLCache] = {}
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}
        self._config: dict[str, dict[str, Any]] = {}

    def create(self, name: str, *, maxsize: int, ttl: int) -> TTLCache:
        """Create and register a new TTLCache."""
        cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._caches[name] = cache
        self._hits[name] = 0
        self._misses[name] = 0
        self._config[name] = {"maxsize": maxsize, "ttl": ttl}
        return cache

    def record_hit(self, name: str) -> None:
        """Increment hit counter for *name*."""
        self._hits[name] = self._hits.get(name, 0) + 1

    def record_miss(self, name: str) -> None:
        """Increment miss counter for *name*."""
        self._misses[name] = self._misses.get(name, 0) + 1

    def stats(self) -> dict[str, dict[str, Any]]:
        """Return per-cache stats: size, maxsize, ttl, hits, misses, hit_rate."""
        result: dict[str, dict[str, Any]] = {}
        for name, cache in self._caches.items():
            hits = self._hits.get(name, 0)
            misses = self._misses.get(name, 0)
            total = hits + misses
            result[name] = {
                "hits": hits,
                "hit_rate": hits / total if total else 0.0,
                "maxsize": self._config[name]["maxsize"],
                "misses": misses,
                "size": len(cache),
                "ttl": self._config[name]["ttl"],
            }
        return result

    def clear(self, name: str) -> None:
        """Clear a single cache and reset its counters."""
        if name in self._caches:
            self._caches[name].clear()
            self._hits[name] = 0
            self._misses[name] = 0

    def clear_all(self) -> None:
        """Clear every registered cache and reset all counters."""
        for name in self._caches:
            self._caches[name].clear()
            self._hits[name] = 0
            self._misses[name] = 0


# ---------------------------------------------------------------------------
# Module-level singleton registry with pre-created caches
# ---------------------------------------------------------------------------

registry = CacheRegistry()

product_details_cache = registry.create(
    "product_details",
    maxsize=PRODUCT_DETAILS_MAXSIZE,
    ttl=PRODUCT_DETAILS_TTL,
)

text_search_cache = registry.create(
    "text_search",
    maxsize=TEXT_SEARCH_MAXSIZE,
    ttl=TEXT_SEARCH_TTL,
)

table_info_cache = registry.create(
    "table_info",
    maxsize=TABLE_INFO_MAXSIZE,
    ttl=TABLE_INFO_TTL,
)


# ---------------------------------------------------------------------------
# Cached async DB query functions (with stampede prevention)
# ---------------------------------------------------------------------------

# Lazy import to avoid circular dependency at module level
SCHEMA_TO_PRODUCTS_TABLE_MAP: dict[str, str] = {
    "hs92": "classification.product_hs92",
    "hs12": "classification.product_hs12",
    "sitc": "classification.product_sitc",
    "services_unilateral": "classification.product_services_unilateral",
    "services_bilateral": "classification.product_services_bilateral",
}


@async_cached(
    cache=product_details_cache,
    key=lambda codes_tuple, schema, async_engine: product_details_key(
        list(codes_tuple), schema
    ),
)
async def cached_product_details(
    codes_tuple: tuple[str, ...], schema: str, async_engine: Any
) -> list[dict[str, Any]]:
    """Execute the actual DB query for product details. Cached with stampede prevention."""
    registry.record_miss("product_details")
    products_table = SCHEMA_TO_PRODUCTS_TABLE_MAP[schema]
    query = text(f"""
        SELECT DISTINCT
            code as product_code,
            name_short_en as product_name,
            product_id,
            product_level
        FROM {products_table}
        WHERE code = ANY(:codes)
    """)
    try:
        async with async_engine.connect() as conn:
            result = await conn.execute(query, {"codes": list(codes_tuple)})
            rows = result.fetchall()
            return [
                {
                    "product_code": str(r[0]),
                    "product_name": str(r[1]),
                    "product_id": str(r[2]),
                    "product_level": str(r[3]),
                }
                for r in rows
            ]
    except SQLAlchemyError as e:
        logger.error("Database error during cached code verification: %s", e)
        return []


@async_cached(
    cache=text_search_cache,
    key=lambda product_to_search, schema, async_engine: text_search_key(
        product_to_search, schema
    ),
)
async def cached_text_search(
    product_to_search: str, schema: str, async_engine: Any
) -> list[dict[str, Any]]:
    """Execute the actual DB query for text search. Cached with stampede prevention."""
    registry.record_miss("text_search")
    products_table = SCHEMA_TO_PRODUCTS_TABLE_MAP[schema]

    ts_query = text(f"""
        SELECT DISTINCT
            name_short_en as product_name,
            code as product_code,
            product_id,
            product_level,
            ts_rank_cd(to_tsvector('english', name_short_en),
                    plainto_tsquery('english', :product_to_search)) as rank
        FROM {products_table}
        WHERE to_tsvector('english', name_short_en) @@
            plainto_tsquery('english', :product_to_search)
        ORDER BY rank DESC
        LIMIT 5
    """)

    fuzzy_query = text(f"""
        SELECT DISTINCT
            name_short_en as product_name,
            code as product_code,
            product_id,
            product_level,
            similarity(LOWER(name_short_en), LOWER(:product_to_search)) as sim
        FROM {products_table}
        WHERE similarity(LOWER(name_short_en), LOWER(:product_to_search)) > 0.3
        ORDER BY sim DESC
        LIMIT 5
    """)

    try:
        async with async_engine.connect() as conn:
            result = await conn.execute(
                ts_query, {"product_to_search": product_to_search}
            )
            ts_results = result.fetchall()

            if ts_results:
                return [
                    {
                        "product_name": str(r[0]),
                        "product_code": str(r[1]),
                        "product_id": str(r[2]),
                        "product_level": str(r[3]),
                    }
                    for r in ts_results
                ]

            result = await conn.execute(
                fuzzy_query, {"product_to_search": product_to_search}
            )
            fuzzy_results = result.fetchall()

            return [
                {
                    "product_name": str(r[0]),
                    "product_code": str(r[1]),
                    "product_id": str(r[2]),
                    "product_level": str(r[3]),
                }
                for r in fuzzy_results
            ]
    except SQLAlchemyError as e:
        logger.error("Database error during cached text search: %s", e)
        return []
