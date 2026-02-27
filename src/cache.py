"""In-process caching for expensive deterministic operations.

Provides two cache styles:

1. **Per-query TTLCache** — for product details lookups, text search, and
   table DDL reflection.  Uses ``cachetools-async`` with built-in stampede
   prevention (concurrent identical lookups trigger only one underlying call).

2. **CatalogCache** — lazy-loaded, TTL-based caches for entire GraphQL
   catalog datasets (countries, products, services).  Fetched once on first
   access, indexed for O(1) lookups by multiple keys, with stampede
   prevention via ``asyncio.Lock``.

The ``CacheRegistry`` tracks all caches for observability (``/debug/caches``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.graphql_client import AtlasGraphQLClient

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
    """Registry of named TTLCache and CatalogCache instances."""

    def __init__(self) -> None:
        self._caches: dict[str, TTLCache] = {}
        self._hits: dict[str, int] = {}
        self._misses: dict[str, int] = {}
        self._config: dict[str, dict[str, Any]] = {}
        self._catalog_caches: dict[str, "CatalogCache"] = {}

    def create(self, name: str, *, maxsize: int, ttl: int) -> TTLCache:
        """Create and register a new TTLCache."""
        cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._caches[name] = cache
        self._hits[name] = 0
        self._misses[name] = 0
        self._config[name] = {"maxsize": maxsize, "ttl": ttl}
        return cache

    def register_catalog(self, catalog: "CatalogCache") -> None:
        """Register a CatalogCache for observability and clear_all support."""
        self._catalog_caches[catalog.name] = catalog

    def record_hit(self, name: str) -> None:
        """Increment hit counter for *name*."""
        self._hits[name] = self._hits.get(name, 0) + 1

    def record_miss(self, name: str) -> None:
        """Increment miss counter for *name*."""
        self._misses[name] = self._misses.get(name, 0) + 1

    def stats(self) -> dict[str, dict[str, Any]]:
        """Return per-cache stats for both TTLCache and CatalogCache instances."""
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
        for name, catalog in self._catalog_caches.items():
            result[name] = catalog.stats()
        return result

    def clear(self, name: str) -> None:
        """Clear a single cache and reset its counters."""
        if name in self._caches:
            self._caches[name].clear()
            self._hits[name] = 0
            self._misses[name] = 0
        if name in self._catalog_caches:
            self._catalog_caches[name].clear()

    def clear_all(self) -> None:
        """Clear every registered cache and reset all counters."""
        for name in self._caches:
            self._caches[name].clear()
            self._hits[name] = 0
            self._misses[name] = 0
        for catalog in self._catalog_caches.values():
            catalog.clear()


# ---------------------------------------------------------------------------
# CatalogCache — lazy-loaded, TTL-based cache for entire catalog datasets
# ---------------------------------------------------------------------------


class _Index:
    """A named index over catalog entries for O(1) exact lookups."""

    __slots__ = ("data", "key_fn", "normalize_query")

    def __init__(
        self,
        key_fn: Callable[[dict[str, Any]], str | None],
        normalize_query: Callable[[str], str] | None = None,
    ) -> None:
        self.key_fn = key_fn
        self.normalize_query: Callable[[str], str] = normalize_query or (lambda q: q)
        self.data: dict[str, dict[str, Any]] = {}

    def build(self, entries: list[dict[str, Any]]) -> None:
        """Rebuild index from a full list of entries."""
        new_data: dict[str, dict[str, Any]] = {}
        for entry in entries:
            key = self.key_fn(entry)
            if key:
                new_data[key] = entry
        self.data = new_data

    def get(self, query: str) -> dict[str, Any] | None:
        """Exact lookup with query normalization."""
        return self.data.get(self.normalize_query(query))

    def clear(self) -> None:
        self.data = {}


class CatalogCache:
    """Lazy-loaded, TTL-based cache for a complete catalog dataset.

    Unlike per-query TTLCache caches, a CatalogCache stores an entire
    catalog (e.g. all countries, all products) fetched from an external
    source.  Supports:

    - **Lazy population** on first access (not at import/startup)
    - **Multiple named indexes** for O(1) exact lookups by different keys
    - **Text search** (case-insensitive substring match)
    - **TTL-based invalidation** — re-fetches from source after expiry
    - **Stampede prevention** — concurrent first-accesses trigger only one fetch
    - **Direct population** via ``populate()`` for testing / pre-warming
    """

    def __init__(
        self,
        name: str,
        *,
        ttl: int,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._ttl = ttl
        self._timer = timer
        self._entries: list[dict[str, Any]] = []
        self._indexes: dict[str, _Index] = {}
        self._populated_at: float | None = None
        self._fetcher: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # -- Configuration -------------------------------------------------------

    def add_index(
        self,
        name: str,
        *,
        key_fn: Callable[[dict[str, Any]], str | None],
        normalize_query: Callable[[str], str] | None = None,
    ) -> None:
        """Register a named index.

        Args:
            name: Index name used in ``lookup()`` calls.
            key_fn: Extracts the index key from a catalog entry.
                    Return ``None`` to exclude an entry from the index.
            normalize_query: Applied to the query string in ``lookup()``
                             so that lookups are case/whitespace-insensitive.
        """
        self._indexes[name] = _Index(key_fn, normalize_query)

    def set_fetcher(
        self, fetcher: Callable[[], Awaitable[list[dict[str, Any]]]]
    ) -> None:
        """Set the async function that fetches catalog data from the source."""
        self._fetcher = fetcher

    # -- Data access ---------------------------------------------------------

    async def lookup(self, index_name: str, key: str) -> dict[str, Any] | None:
        """Exact O(1) lookup by a named index.

        Raises:
            KeyError: If *index_name* was never registered via ``add_index()``.
            RuntimeError: If the cache has no fetcher and was never populated.
        """
        await self._ensure_populated()
        if index_name not in self._indexes:
            raise KeyError(
                f"CatalogCache '{self.name}' has no index named '{index_name}'"
            )
        return self._indexes[index_name].get(key)

    def lookup_sync(self, index_name: str, key: str) -> dict[str, Any] | None:
        """Synchronous O(1) lookup — requires cache to be already populated.

        Use this in synchronous post-processing code that runs after the cache
        has been populated by prior async pipeline nodes.

        Raises:
            KeyError: If *index_name* was never registered.
            RuntimeError: If the cache is not yet populated.
        """
        if not self.is_populated:
            raise RuntimeError(
                f"CatalogCache '{self.name}' is not populated — "
                f"call populate() or await lookup() first"
            )
        if index_name not in self._indexes:
            raise KeyError(
                f"CatalogCache '{self.name}' has no index named '{index_name}'"
            )
        return self._indexes[index_name].get(key)

    async def search(
        self, field: str, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Case-insensitive substring search on a field across all entries.

        Args:
            field: Entry dict key to search in (e.g. ``"nameShortEn"``).
            query: Substring to match (case-insensitive).
            limit: Maximum results to return.
        """
        await self._ensure_populated()
        query_lower = query.strip().lower()
        results: list[dict[str, Any]] = []
        for entry in self._entries:
            value = entry.get(field, "")
            if isinstance(value, str) and query_lower in value.lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    async def get_all(self) -> list[dict[str, Any]]:
        """Return all catalog entries, populating from source if needed."""
        await self._ensure_populated()
        return list(self._entries)

    # -- Direct population (testing / pre-warming) ---------------------------

    def populate(self, entries: list[dict[str, Any]]) -> None:
        """Directly load entries without a fetcher.

        Rebuilds all indexes.  Resets the TTL timer.
        """
        self._entries = list(entries)
        self._rebuild_indexes()
        self._populated_at = self._timer()

    # -- Observability -------------------------------------------------------

    @property
    def is_populated(self) -> bool:
        """Whether the cache currently holds data (ignores TTL)."""
        return self._populated_at is not None

    def stats(self) -> dict[str, Any]:
        """Return cache stats for the registry."""
        age: float | None = None
        if self._populated_at is not None:
            age = round(self._timer() - self._populated_at, 1)
        return {
            "populated": self.is_populated,
            "size": len(self._entries),
            "ttl": self._ttl,
            "age_seconds": age,
            "indexes": list(self._indexes.keys()),
        }

    # -- Cache management ----------------------------------------------------

    def clear(self) -> None:
        """Clear all data and reset the TTL timer."""
        self._entries = []
        self._populated_at = None
        for idx in self._indexes.values():
            idx.clear()

    # -- Internal ------------------------------------------------------------

    @property
    def _is_valid(self) -> bool:
        if self._populated_at is None:
            return False
        return (self._timer() - self._populated_at) < self._ttl

    async def _ensure_populated(self) -> None:
        """Populate from fetcher if cache is empty or TTL has expired.

        Uses ``asyncio.Lock`` for stampede prevention: if multiple coroutines
        call this concurrently, only the first actually fetches; the rest wait
        and then use the freshly cached data.
        """
        if self._is_valid:
            return

        async with self._lock:
            # Double-check after acquiring lock (another coroutine may have populated)
            if self._is_valid:
                return

            if self._fetcher is None:
                raise RuntimeError(
                    f"CatalogCache '{self.name}' has no fetcher and is not populated. "
                    "Call set_fetcher() or populate() before accessing data."
                )

            logger.info("Fetching catalog data for '%s'", self.name)
            entries = await self._fetcher()
            self._entries = list(entries)
            self._rebuild_indexes()
            self._populated_at = self._timer()
            logger.info(
                "Populated catalog '%s' with %d entries", self.name, len(self._entries)
            )

    def _rebuild_indexes(self) -> None:
        """Rebuild all registered indexes from the current entries."""
        for idx in self._indexes.values():
            idx.build(self._entries)


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
# GraphQL catalog caches (lazy-loaded on first access)
# ---------------------------------------------------------------------------

CATALOG_TTL = 86400  # 24 hours

_name_key = lambda e: (  # noqa: E731
    (e.get("nameShortEn") or e.get("nameEn", "")).strip().lower() or None
)
_name_normalize = lambda q: q.strip().lower()  # noqa: E731

# Country catalog: maps country names / ISO codes → Atlas country IDs
country_catalog = CatalogCache("country_catalog", ttl=CATALOG_TTL)
country_catalog.add_index(
    "iso3",
    key_fn=lambda e: (e.get("iso3Code") or "").upper() or None,
    normalize_query=lambda q: q.strip().upper(),
)
country_catalog.add_index("name", key_fn=_name_key, normalize_query=_name_normalize)
country_catalog.add_index(
    "id",
    key_fn=lambda e: str(e["countryId"]) if "countryId" in e else None,
)
registry.register_catalog(country_catalog)

# Product catalog: dual-indexed by HS code AND by name
product_catalog = CatalogCache("product_catalog", ttl=CATALOG_TTL)
product_catalog.add_index(
    "code",
    key_fn=lambda e: (e.get("code") or "").strip() or None,
    normalize_query=lambda q: q.strip(),
)
product_catalog.add_index("name", key_fn=_name_key, normalize_query=_name_normalize)
product_catalog.add_index(
    "id",
    key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
)
registry.register_catalog(product_catalog)

# Services catalog: service category names and IDs
services_catalog = CatalogCache("services_catalog", ttl=CATALOG_TTL)
services_catalog.add_index("name", key_fn=_name_key, normalize_query=_name_normalize)
services_catalog.add_index(
    "id",
    key_fn=lambda e: str(e["productId"]) if "productId" in e else None,
)
registry.register_catalog(services_catalog)


# ---------------------------------------------------------------------------
# Catalog fetcher wiring (called once at app startup)
# ---------------------------------------------------------------------------


def wire_catalog_fetchers(explore_client: AtlasGraphQLClient) -> None:
    """Wire async fetcher functions to the module-level catalog caches.

    Must be called once during app startup, after the Explore API
    GraphQL client is constructed.  Each fetcher executes a lightweight
    catalog query and returns the full list of entries.

    GraphQL field names verified against the official Atlas API docs
    (``evaluation/graphql_api_official_docs.md``).
    """

    async def _fetch_countries() -> list[dict[str, Any]]:
        query = "{ locationCountry { countryId iso3Code nameShortEn nameEn } }"
        data = await explore_client.execute(query)
        return data.get("locationCountry", [])

    async def _fetch_products() -> list[dict[str, Any]]:
        query = "{ productHs92(productLevel: 4) { productId code nameShortEn nameEn } }"
        data = await explore_client.execute(query)
        return data.get("productHs92", [])

    async def _fetch_services() -> list[dict[str, Any]]:
        query = (
            "{ productHs92(servicesClass: unilateral)"
            " { productId nameShortEn nameEn } }"
        )
        data = await explore_client.execute(query)
        return data.get("productHs92", [])

    country_catalog.set_fetcher(_fetch_countries)
    product_catalog.set_fetcher(_fetch_products)
    services_catalog.set_fetcher(_fetch_services)


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
