"""Product search experiment framework.

Evaluates 8 product code search approaches against the gold-standard
test corpus.  Each approach implements the ``ProductSearchApproach`` protocol.

All non-baseline approaches receive **synonym-enriched** product text
(official name + LLM-generated synonyms) so that queries like "cars" can
match "Motor cars and other motor vehicles | cars automobiles sedans SUVs".

Approaches:
    1. baseline      — PostgreSQL FTS + trigram (current production code)
    2. minhash       — MinHash LSH + re-ranking via rapidfuzz (enriched)
    3. bm25          — bm25s with Snowball stemming (enriched)
    4. embedding     — OpenAI text-embedding-3-small + cosine similarity (enriched)
    5. rapidfuzz     — In-memory rapidfuzz WRatio matching (enriched)
    6. hybrid_emb_bm25 — Embedding + BM25 fused via RRF
    7. hybrid_local  — MinHash + BM25 + RapidFuzz fused via RRF (zero-API-cost)
    8. llm_only      — LLM zero-shot code prediction (no DB verification)

Usage:
    # Run all approaches against all test cases:
    ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \\
        PYTHONPATH=$(pwd) uv run python evaluation/product_search/eval.py \\
        --approaches all

    # Run specific approaches:
    ... --approaches baseline,rapidfuzz,hybrid_emb_bm25

    # Filter to one schema:
    ... --schema hs12

    # Rebuild synonym enrichment cache:
    ... --rebuild-enrichment

    # List available approaches:
    ... --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.product_enrichment import build_enrichment as _build_enrichment
from src.product_enrichment import load_enrichment_cache as _load_enrichment_cache_impl
from src.product_enrichment import save_enrichment_cache as _save_enrichment_cache_impl

# Load .env for API keys
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

GOLD_STANDARD_PATH = Path(__file__).parent / "data" / "gold_standard.json"
RESULTS_DIR = Path(__file__).parent / "results"
ENRICHMENT_CACHE_PATH = Path(__file__).parent / "data" / "enrichment_cache.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ProductSearchTestCase:
    """A single gold-standard test case."""

    query: str
    schema: str
    expected_codes: list[str]
    acceptable_codes: list[str] = field(default_factory=list)
    official_name: str = ""
    difficulty: str = "easy"
    notes: str = ""


@dataclass
class SearchResult:
    """Result from a single search query."""

    query: str
    schema: str
    returned_codes: list[str]
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class ApproachMetrics:
    """Aggregate metrics for one approach."""

    name: str
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    precision_at_5: float = 0.0
    mrr: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    total_cases: int = 0
    successful_cases: int = 0
    error_count: int = 0
    per_difficulty: dict[str, dict[str, float]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract base for search approaches
# ---------------------------------------------------------------------------


class ProductSearchApproach(ABC):
    """Protocol for a product search approach."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this approach."""

    @abstractmethod
    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        """One-time setup (build indexes, connect to DB, etc.).

        Args:
            db_url: Database connection string for loading product catalogs.
            enrichment: Optional synonym-enriched text keyed by
                ``{schema: {product_code: enriched_text}}``.
        """

    @abstractmethod
    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        """Search for products matching the query.

        Returns list of dicts with at least ``product_code`` key, ordered by
        relevance (best match first).
        """

    async def teardown(self) -> None:  # noqa: B027
        """Cleanup resources (optional override for subclasses)."""


# ---------------------------------------------------------------------------
# Shared: load catalog from DB (used by in-memory approaches)
# ---------------------------------------------------------------------------


def _load_catalogs(db_url: str) -> dict[str, list[dict[str, Any]]]:
    """Load product catalogs from the Atlas DB into memory."""
    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    from src.product_and_schema_lookup import SCHEMA_TO_PRODUCTS_TABLE_MAP

    catalogs: dict[str, list[dict[str, Any]]] = {}
    engine = create_engine(db_url)
    for schema, table in SCHEMA_TO_PRODUCTS_TABLE_MAP.items():
        query = sa_text(f"""
            SELECT code, name_short_en, product_id, product_level
            FROM {table}
            WHERE code IS NOT NULL AND name_short_en IS NOT NULL
        """)
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
            catalogs[schema] = [
                {
                    "product_code": str(r[0]),
                    "product_name": str(r[1]),
                    "product_id": str(r[2]),
                    "product_level": str(r[3]),
                }
                for r in rows
            ]
    engine.dispose()
    return catalogs


def _tokenize(text_str: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer with lowercasing."""
    return re.findall(r"[a-z0-9]+", text_str.lower())


def _char_ngrams(text_str: str, n: int = 3) -> set[str]:
    """Generate character n-grams (shingles) from text."""
    text_str = text_str.lower().strip()
    if len(text_str) < n:
        return {text_str}
    return {text_str[i : i + n] for i in range(len(text_str) - n + 1)}


# ---------------------------------------------------------------------------
# Enrichment infrastructure (delegated to src.product_enrichment)
# ---------------------------------------------------------------------------


def _load_enrichment_cache() -> dict[str, dict[str, str]] | None:
    """Load enrichment cache from the eval-local path."""
    return _load_enrichment_cache_impl(ENRICHMENT_CACHE_PATH)


def _save_enrichment_cache(enrichment: dict[str, dict[str, str]]) -> None:
    """Save enrichment cache to the eval-local path."""
    _save_enrichment_cache_impl(enrichment, ENRICHMENT_CACHE_PATH)


def _get_enriched_text(
    enrichment: dict[str, dict[str, str]] | None,
    schema: str,
    product_code: str,
    fallback_name: str,
) -> str:
    """Get enriched text for a product, falling back to raw name."""
    if enrichment and schema in enrichment:
        return enrichment[schema].get(product_code, fallback_name)
    return fallback_name


# ---------------------------------------------------------------------------
# Approach 1: Baseline — PostgreSQL FTS + Trigram (current implementation)
# ---------------------------------------------------------------------------


class BaselineFTSApproach(ProductSearchApproach):
    """Current implementation: PostgreSQL full-text search + trigram similarity."""

    def __init__(self) -> None:
        self._engine = None

    @property
    def name(self) -> str:
        return "baseline_fts"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        from sqlalchemy.ext.asyncio import create_async_engine

        async_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
        self._engine = create_async_engine(async_url)

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text as sa_text

        from src.product_and_schema_lookup import SCHEMA_TO_PRODUCTS_TABLE_MAP

        table = SCHEMA_TO_PRODUCTS_TABLE_MAP.get(schema)
        if not table or not self._engine:
            return []

        ts_query = sa_text(f"""
            SELECT DISTINCT
                name_short_en as product_name,
                code as product_code,
                product_id,
                product_level,
                ts_rank_cd(to_tsvector('english', name_short_en),
                        plainto_tsquery('english', :query)) as rank
            FROM {table}
            WHERE to_tsvector('english', name_short_en) @@
                plainto_tsquery('english', :query)
            ORDER BY rank DESC
            LIMIT :limit
        """)

        fuzzy_query = sa_text(f"""
            SELECT DISTINCT
                name_short_en as product_name,
                code as product_code,
                product_id,
                product_level,
                similarity(LOWER(name_short_en), LOWER(:query)) as sim
            FROM {table}
            WHERE similarity(LOWER(name_short_en), LOWER(:query)) > 0.3
            ORDER BY sim DESC
            LIMIT :limit
        """)

        async with self._engine.connect() as conn:
            result = await conn.execute(ts_query, {"query": query, "limit": top_k})
            rows = result.fetchall()

            if not rows:
                result = await conn.execute(
                    fuzzy_query, {"query": query, "limit": top_k}
                )
                rows = result.fetchall()

        return [
            {
                "product_code": str(r[1]),
                "product_name": str(r[0]),
                "product_id": str(r[2]),
                "product_level": str(r[3]),
            }
            for r in rows
        ]

    async def teardown(self) -> None:
        if self._engine:
            await self._engine.dispose()


# ---------------------------------------------------------------------------
# Approach 2: MinHash LSH — enriched + re-ranking with rapidfuzz
# ---------------------------------------------------------------------------


class MinHashLSHApproach(ProductSearchApproach):
    """MinHash LSH with character 3-gram shingling + rapidfuzz re-ranking.

    Based on LAIA-SQL (2025): MinHash for candidate retrieval, then re-rank
    with normalized edit distance (rapidfuzz ratio) for precision.

    Uses synonym-enriched text so "cars" shingles overlap with
    "Motor cars ... | cars automobiles sedans SUVs".
    """

    def __init__(self) -> None:
        self._lsh_indexes: dict[str, Any] = {}
        self._minhashes: dict[str, dict[str, Any]] = {}
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._code_to_entry: dict[str, dict[str, dict[str, Any]]] = {}

    @property
    def name(self) -> str:
        return "minhash_lsh"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        from datasketch import MinHash, MinHashLSH

        catalogs = _load_catalogs(db_url)

        for schema, entries in catalogs.items():
            self._entries[schema] = entries
            self._code_to_entry[schema] = {e["product_code"]: e for e in entries}
            lsh = MinHashLSH(threshold=0.3, num_perm=128)
            minhashes: dict[str, Any] = {}

            for entry in entries:
                code = entry["product_code"]
                # Use enriched text (name + synonyms) for shingling
                text_to_shingle = _get_enriched_text(
                    enrichment, schema, code, entry["product_name"]
                )

                m = MinHash(num_perm=128)
                for shingle in _char_ngrams(text_to_shingle, n=3):
                    m.update(shingle.encode("utf-8"))
                minhashes[code] = m

                try:
                    lsh.insert(code, m)
                except ValueError:
                    pass  # Duplicate key

            self._lsh_indexes[schema] = lsh
            self._minhashes[schema] = minhashes

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        from datasketch import MinHash
        from rapidfuzz import fuzz

        lsh = self._lsh_indexes.get(schema)
        entries = self._entries.get(schema, [])
        code_to_entry = self._code_to_entry.get(schema, {})
        if not lsh or not entries:
            return []

        # Build query MinHash
        query_mh = MinHash(num_perm=128)
        for shingle in _char_ngrams(query, n=3):
            query_mh.update(shingle.encode("utf-8"))

        # LSH candidate retrieval
        candidate_codes = set(lsh.query(query_mh))
        if not candidate_codes:
            return []

        # Re-rank by rapidfuzz ratio (normalized edit distance)
        scored: list[tuple[float, dict[str, Any]]] = []
        for code in candidate_codes:
            entry = code_to_entry.get(code)
            if entry:
                name = entry["product_name"]
                score = fuzz.ratio(query.lower(), name.lower()) / 100.0
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]


# ---------------------------------------------------------------------------
# Approach 3: BM25 with Snowball stemming (bm25s)
# ---------------------------------------------------------------------------


class BM25Approach(ProductSearchApproach):
    """In-memory BM25 with Snowball stemming on enriched product text.

    Uses bm25s library with PyStemmer for proper stemming, so
    "vehicles" matches "vehicle". Low length normalization (b=0.1)
    since product descriptions are short and similar length.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, Any] = {}
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._stemmer = None

    @property
    def name(self) -> str:
        return "bm25"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        import bm25s
        import Stemmer

        self._stemmer = Stemmer.Stemmer("english")
        catalogs = _load_catalogs(db_url)

        for schema, entries in catalogs.items():
            self._entries[schema] = entries
            # Use enriched text (name + synonyms) for indexing
            texts = [
                _get_enriched_text(
                    enrichment, schema, e["product_code"], e["product_name"]
                )
                for e in entries
            ]
            tokens = bm25s.tokenize(texts, stopwords="en", stemmer=self._stemmer)
            retriever = bm25s.BM25(k1=1.5, b=0.1)
            retriever.index(tokens)
            self._indexes[schema] = (retriever, tokens)

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        import bm25s

        index_data = self._indexes.get(schema)
        entries = self._entries.get(schema, [])
        if not index_data or not entries:
            return []

        retriever, _corpus_tokens = index_data
        query_tokens = bm25s.tokenize([query], stopwords="en", stemmer=self._stemmer)
        results, scores = retriever.retrieve(query_tokens, k=min(top_k, len(entries)))

        # results shape: (1, k) — indices; scores shape: (1, k) — scores
        output: list[dict[str, Any]] = []
        for i in range(results.shape[1]):
            idx = int(results[0, i])
            score = float(scores[0, i])
            if score > 0 and idx < len(entries):
                output.append(entries[idx])
        return output[:top_k]


# ---------------------------------------------------------------------------
# Approach 4: Context-enriched embedding search
# ---------------------------------------------------------------------------


class EmbeddingSearchApproach(ProductSearchApproach):
    """Embedding-based semantic search using OpenAI text-embedding-3-small.

    Embeds enriched product text (name + synonyms, same format as production)
    at setup time, then does cosine similarity search at query time.

    Uses dimensions=512 to match production (src/product_search.py).
    """

    _EMBEDDING_MODEL = "text-embedding-3-small"
    _EMBEDDING_DIM = 768

    def __init__(self) -> None:
        self._embeddings: dict[str, Any] = {}  # schema → numpy array
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._client = None

    @property
    def name(self) -> str:
        return "embedding"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        import numpy as np
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI()
        catalogs = _load_catalogs(db_url)

        for schema, entries in catalogs.items():
            self._entries[schema] = entries
            # Use enriched text like production does
            texts = [
                _get_enriched_text(
                    enrichment, schema, e["product_code"], e["product_name"]
                )
                for e in entries
            ]

            # Batch embed all product texts
            all_embeddings: list[list[float]] = []
            batch_size = 2048
            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                response = await self._client.embeddings.create(
                    input=batch,
                    model=self._EMBEDDING_MODEL,
                    dimensions=self._EMBEDDING_DIM,
                )
                batch_embs = [d.embedding for d in response.data]
                all_embeddings.extend(batch_embs)

            # Store as normalized numpy array for fast cosine similarity
            emb_array = np.array(all_embeddings, dtype=np.float32)
            norms = np.linalg.norm(emb_array, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._embeddings[schema] = emb_array / norms

            logger.info(
                "  Embedded %d products for %s (%d dims)",
                len(entries),
                schema,
                emb_array.shape[1],
            )

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        import numpy as np

        emb_matrix = self._embeddings.get(schema)
        entries = self._entries.get(schema, [])
        if emb_matrix is None or not entries or not self._client:
            return []

        # Embed query
        response = await self._client.embeddings.create(
            input=[query],
            model=self._EMBEDDING_MODEL,
            dimensions=self._EMBEDDING_DIM,
        )
        query_emb = np.array(response.data[0].embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_emb)
        if query_norm > 0:
            query_emb = query_emb / query_norm

        # Cosine similarity (dot product of normalized vectors)
        similarities = emb_matrix @ query_emb
        top_indices = np.argsort(similarities)[::-1][:top_k]

        return [entries[i] for i in top_indices if similarities[i] > 0]


# ---------------------------------------------------------------------------
# Approach 5: RapidFuzz with enriched text
# ---------------------------------------------------------------------------


class RapidFuzzApproach(ProductSearchApproach):
    """In-memory fuzzy matching using rapidfuzz WRatio scorer on enriched text.

    Matches against enriched text (name + synonyms) so "cars" can match
    "Motor cars and other motor vehicles | cars automobiles sedans SUVs".
    """

    def __init__(self) -> None:
        self._catalogs: dict[str, list[dict[str, Any]]] = {}
        self._enriched_names: dict[str, list[str]] = {}

    @property
    def name(self) -> str:
        return "rapidfuzz"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._catalogs = _load_catalogs(db_url)
        for schema, entries in self._catalogs.items():
            self._enriched_names[schema] = [
                _get_enriched_text(
                    enrichment, schema, e["product_code"], e["product_name"]
                )
                for e in entries
            ]

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        from rapidfuzz import fuzz, process

        entries = self._catalogs.get(schema, [])
        names = self._enriched_names.get(schema, [])
        if not entries or not names:
            return []

        results = process.extract(
            query,
            names,
            scorer=fuzz.WRatio,
            limit=top_k,
            score_cutoff=55,
        )

        return [entries[idx] for _, score, idx in results]


# ---------------------------------------------------------------------------
# Approach 6: Hybrid Embedding + BM25 via RRF (production architecture)
# ---------------------------------------------------------------------------


def _rrf_fuse(
    *result_lists: list[dict[str, Any]],
    k: int = 60,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    Args:
        result_lists: Multiple ranked lists of product dicts.
        k: RRF constant (standard = 60).
        top_k: Maximum results to return.

    Returns:
        Fused and re-ranked list of product dicts.
    """
    rrf_scores: dict[str, float] = {}
    code_to_entry: dict[str, dict[str, Any]] = {}

    for result_list in result_lists:
        for rank, entry in enumerate(result_list, 1):
            code = entry["product_code"]
            code_to_entry[code] = entry
            rrf_scores[code] = rrf_scores.get(code, 0.0) + 1.0 / (k + rank)

    sorted_codes = sorted(rrf_scores, key=rrf_scores.get, reverse=True)
    return [code_to_entry[code] for code in sorted_codes[:top_k]]


class HybridEmbeddingBM25Approach(ProductSearchApproach):
    """Embedding + BM25 fused via RRF.

    Matches the production architecture in ``src/product_search.py``:
    embedding similarity + BM25 keyword search, combined with Reciprocal
    Rank Fusion for best-of-both-worlds retrieval.
    """

    def __init__(self) -> None:
        self._embedding = EmbeddingSearchApproach()
        self._bm25 = BM25Approach()

    @property
    def name(self) -> str:
        return "hybrid_emb_bm25"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        await asyncio.gather(
            self._embedding.setup(db_url, enrichment),
            self._bm25.setup(db_url, enrichment),
        )

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        fetch_k = max(top_k * 3, 30)
        emb_results, bm25_results = await asyncio.gather(
            self._embedding.search(query, schema, top_k=fetch_k),
            self._bm25.search(query, schema, top_k=fetch_k),
        )
        return _rrf_fuse(emb_results, bm25_results, top_k=top_k)

    async def teardown(self) -> None:
        await asyncio.gather(
            self._embedding.teardown(),
            self._bm25.teardown(),
        )


# ---------------------------------------------------------------------------
# Approach 7: Hybrid Local — MinHash + BM25 + RapidFuzz (zero-API-cost)
# ---------------------------------------------------------------------------


class HybridLocalApproach(ProductSearchApproach):
    """Hybrid retrieval fusing MinHash + BM25 + RapidFuzz with RRF.

    All sub-approaches use synonym-enriched text. No external API calls
    at search time (only CPU), making this the fastest hybrid option.
    """

    def __init__(self) -> None:
        self._minhash = MinHashLSHApproach()
        self._bm25 = BM25Approach()
        self._rapidfuzz = RapidFuzzApproach()

    @property
    def name(self) -> str:
        return "hybrid_local"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        await asyncio.gather(
            self._minhash.setup(db_url, enrichment),
            self._bm25.setup(db_url, enrichment),
            self._rapidfuzz.setup(db_url, enrichment),
        )

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        fetch_k = 50
        minhash_results, bm25_results, fuzz_results = await asyncio.gather(
            self._minhash.search(query, schema, top_k=fetch_k),
            self._bm25.search(query, schema, top_k=fetch_k),
            self._rapidfuzz.search(query, schema, top_k=fetch_k),
        )
        return _rrf_fuse(minhash_results, bm25_results, fuzz_results, top_k=top_k)

    async def teardown(self) -> None:
        await asyncio.gather(
            self._minhash.teardown(),
            self._bm25.teardown(),
            self._rapidfuzz.teardown(),
        )


# ---------------------------------------------------------------------------
# Approach 8: LLM-only (zero-shot, no DB verification)
# ---------------------------------------------------------------------------


class LLMOnlyApproach(ProductSearchApproach):
    """LLM's own code guesses without any DB verification."""

    def __init__(self) -> None:
        self._llm = None

    @property
    def name(self) -> str:
        return "llm_only"

    async def setup(
        self,
        db_url: str,
        enrichment: dict[str, dict[str, str]] | None = None,
    ) -> None:
        from src.config import create_llm, get_settings

        settings = get_settings()
        self._llm = create_llm(
            settings.lightweight_model,
            settings.lightweight_model_provider,
            temperature=0,
        )

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        from langchain_core.prompts import ChatPromptTemplate
        from pydantic import BaseModel, Field

        class ProductGuess(BaseModel):
            codes: list[str] = Field(
                description="Product codes in the classification system"
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are an expert in international trade classification systems. "
                    "Given a product name and classification schema, return the most "
                    "likely product codes. Schema: {schema}",
                ),
                ("human", "What are the product codes for: {query}"),
            ]
        )

        llm = self._llm.with_structured_output(ProductGuess, method="function_calling")
        chain = prompt | llm
        result = await chain.ainvoke({"query": query, "schema": schema})

        return [{"product_code": code} for code in result.codes[:top_k]]


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------


def compute_metrics(
    approach_name: str,
    results: list[SearchResult],
    test_cases: list[ProductSearchTestCase],
) -> ApproachMetrics:
    """Compute precision, recall, MRR, and latency metrics."""
    metrics = ApproachMetrics(name=approach_name, total_cases=len(test_cases))

    recall_5_scores: list[float] = []
    recall_10_scores: list[float] = []
    precision_5_scores: list[float] = []
    mrr_scores: list[float] = []
    latencies: list[float] = []
    per_difficulty: dict[str, list[dict[str, float]]] = {}

    for tc, result in zip(test_cases, results, strict=True):
        if result.error:
            metrics.error_count += 1
            continue

        metrics.successful_cases += 1
        latencies.append(result.latency_ms)

        all_correct = set(tc.expected_codes + tc.acceptable_codes)
        returned = result.returned_codes

        # Recall@5: any correct code in top 5?
        top5_codes = set(returned[:5])
        r5 = 1.0 if top5_codes & all_correct else 0.0
        recall_5_scores.append(r5)

        # Recall@10: any correct code in top 10?
        top10_codes = set(returned[:10])
        r10 = 1.0 if top10_codes & all_correct else 0.0
        recall_10_scores.append(r10)

        # Precision@5: fraction of top 5 that are correct
        if top5_codes:
            p5 = len(top5_codes & all_correct) / len(top5_codes)
        else:
            p5 = 0.0
        precision_5_scores.append(p5)

        # MRR: reciprocal rank of first correct code
        rr = 0.0
        for rank, code in enumerate(returned, 1):
            if code in all_correct:
                rr = 1.0 / rank
                break
        mrr_scores.append(rr)

        # Per-difficulty tracking
        if tc.difficulty not in per_difficulty:
            per_difficulty[tc.difficulty] = []
        per_difficulty[tc.difficulty].append({"recall_5": r5, "mrr": rr})

    # Aggregate
    if recall_5_scores:
        metrics.recall_at_5 = sum(recall_5_scores) / len(recall_5_scores)
    if recall_10_scores:
        metrics.recall_at_10 = sum(recall_10_scores) / len(recall_10_scores)
    if precision_5_scores:
        metrics.precision_at_5 = sum(precision_5_scores) / len(precision_5_scores)
    if mrr_scores:
        metrics.mrr = sum(mrr_scores) / len(mrr_scores)

    if latencies:
        latencies.sort()
        n = len(latencies)
        metrics.latency_p50_ms = latencies[n // 2]
        metrics.latency_p95_ms = latencies[int(n * 0.95)]

    # Per-difficulty aggregation
    for diff, scores in per_difficulty.items():
        if scores:
            metrics.per_difficulty[diff] = {
                "count": len(scores),
                "recall_5": sum(s["recall_5"] for s in scores) / len(scores),
                "mrr": sum(s["mrr"] for s in scores) / len(scores),
            }

    return metrics


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------


APPROACH_REGISTRY: dict[str, type[ProductSearchApproach]] = {
    "baseline": BaselineFTSApproach,
    "minhash": MinHashLSHApproach,
    "bm25": BM25Approach,
    "embedding": EmbeddingSearchApproach,
    "rapidfuzz": RapidFuzzApproach,
    "hybrid_emb_bm25": HybridEmbeddingBM25Approach,
    "hybrid_local": HybridLocalApproach,
    "llm_only": LLMOnlyApproach,
}

# Concurrency for approaches that make external API calls
ASYNC_CONCURRENCY = 30


async def run_experiment(
    approach: ProductSearchApproach,
    test_cases: list[ProductSearchTestCase],
) -> list[SearchResult]:
    """Run a single approach against all test cases concurrently.

    Uses asyncio.gather with a semaphore to limit concurrency for
    API-calling approaches (embedding, llm_only). Logs progress
    every 10% of test cases.
    """
    total = len(test_cases)
    semaphore = asyncio.Semaphore(ASYNC_CONCURRENCY)
    completed = 0
    errors = 0
    lock = asyncio.Lock()

    async def _run_one(idx: int, tc: ProductSearchTestCase) -> tuple[int, SearchResult]:
        nonlocal completed, errors
        async with semaphore:
            t0 = time.monotonic()
            try:
                raw_results = await approach.search(tc.query, tc.schema)
                latency_ms = (time.monotonic() - t0) * 1000
                returned_codes = [r["product_code"] for r in raw_results]
                result = SearchResult(
                    query=tc.query,
                    schema=tc.schema,
                    returned_codes=returned_codes,
                    latency_ms=latency_ms,
                )
            except Exception as e:
                latency_ms = (time.monotonic() - t0) * 1000
                result = SearchResult(
                    query=tc.query,
                    schema=tc.schema,
                    returned_codes=[],
                    latency_ms=latency_ms,
                    error=str(e),
                )

            # Progress update
            async with lock:
                completed += 1
                if result.error:
                    errors += 1
                if completed % max(1, total // 10) == 0 or completed == total:
                    pct = completed * 100 // total
                    err_str = f", {errors} errors" if errors else ""
                    logger.info(
                        "  [%s] %d/%d (%d%%%s)",
                        approach.name,
                        completed,
                        total,
                        pct,
                        err_str,
                    )

            return idx, result

    # Launch all concurrently
    indexed_results = await asyncio.gather(
        *[_run_one(i, tc) for i, tc in enumerate(test_cases)]
    )

    # Re-order by original index
    indexed_results.sort(key=lambda x: x[0])
    return [r for _, r in indexed_results]


def format_report(all_metrics: list[ApproachMetrics]) -> str:
    """Format a comparison report as a markdown table."""
    lines = [
        "# Product Search Approach Comparison",
        "",
        "| Approach | Recall@5 | Recall@10 | Precision@5 | MRR | p50 (ms) | p95 (ms) | Errors |",
        "|----------|----------|-----------|-------------|-----|----------|----------|--------|",
    ]

    for m in sorted(all_metrics, key=lambda x: x.recall_at_5, reverse=True):
        lines.append(
            f"| {m.name} | {m.recall_at_5:.1%} | {m.recall_at_10:.1%} | "
            f"{m.precision_at_5:.1%} | {m.mrr:.3f} | "
            f"{m.latency_p50_ms:.1f} | {m.latency_p95_ms:.1f} | {m.error_count} |"
        )

    # Per-difficulty breakdown
    lines.extend(["", "## Per-Difficulty Breakdown", ""])
    for m in sorted(all_metrics, key=lambda x: x.recall_at_5, reverse=True):
        lines.append(f"### {m.name}")
        for diff, stats in sorted(m.per_difficulty.items()):
            lines.append(
                f"  - {diff}: recall@5={stats['recall_5']:.1%}, "
                f"MRR={stats['mrr']:.3f} (n={stats['count']:.0f})"
            )
        lines.append("")

    return "\n".join(lines)


async def main() -> None:
    """Run product search experiments."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Product search evaluation")
    parser.add_argument(
        "--approaches",
        default="all",
        help="Comma-separated approach names, or 'all'",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Filter test cases to a specific schema (e.g., 'hs12')",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available approaches and exit",
    )
    parser.add_argument(
        "--rebuild-enrichment",
        action="store_true",
        help="Force regeneration of the synonym enrichment cache",
    )
    args = parser.parse_args()

    if args.list:
        for name in sorted(APPROACH_REGISTRY):
            print(f"  {name}")  # noqa: T201
        return

    # Load gold standard
    if not GOLD_STANDARD_PATH.exists():
        logger.error(
            "Gold standard not found at %s. "
            "Run evaluation/product_search/build_gold_standard.py first.",
            GOLD_STANDARD_PATH,
        )
        sys.exit(1)

    with open(GOLD_STANDARD_PATH) as f:
        data = json.load(f)

    test_cases = [ProductSearchTestCase(**tc) for tc in data["test_cases"]]
    logger.info("Loaded %d test cases", len(test_cases))

    # Filter by schema if specified
    if args.schema:
        test_cases = [tc for tc in test_cases if tc.schema == args.schema]
        logger.info("Filtered to %d cases for schema %s", len(test_cases), args.schema)

    if not test_cases:
        logger.error("No test cases remaining after filtering.")
        sys.exit(1)

    # Select approaches
    if args.approaches == "all":
        approach_names = list(APPROACH_REGISTRY.keys())
    else:
        approach_names = [a.strip() for a in args.approaches.split(",")]

    db_url = os.environ.get("ATLAS_DB_URL")
    if not db_url:
        logger.error("ATLAS_DB_URL not set.")
        sys.exit(1)

    # Build or load enrichment
    enrichment: dict[str, dict[str, str]] | None = None
    if not args.rebuild_enrichment:
        enrichment = _load_enrichment_cache()
        if enrichment:
            logger.info(
                "Loaded enrichment cache (%d schemas)",
                len(enrichment),
            )

    if enrichment is None:
        logger.info("Building synonym enrichment (this makes LLM API calls)...")
        catalogs = _load_catalogs(db_url)
        enrichment = await _build_enrichment(catalogs)
        _save_enrichment_cache(enrichment)

    # Run each approach
    all_metrics: list[ApproachMetrics] = []

    for aname in approach_names:
        cls = APPROACH_REGISTRY.get(aname)
        if not cls:
            logger.warning(
                "Unknown approach: %s (available: %s)",
                aname,
                list(APPROACH_REGISTRY.keys()),
            )
            continue

        approach = cls()
        logger.info("Setting up %s...", approach.name)
        try:
            await approach.setup(db_url, enrichment)
        except Exception as e:
            logger.error("Failed to set up %s: %s", approach.name, e)
            continue

        logger.info(
            "Running %s against %d test cases...", approach.name, len(test_cases)
        )
        results = await run_experiment(approach, test_cases)
        metrics = compute_metrics(approach.name, results, test_cases)
        all_metrics.append(metrics)

        logger.info(
            "  %s: recall@5=%.1f%%, recall@10=%.1f%%, MRR=%.3f, p50=%.1fms",
            approach.name,
            metrics.recall_at_5 * 100,
            metrics.recall_at_10 * 100,
            metrics.mrr,
            metrics.latency_p50_ms,
        )

        await approach.teardown()

    # Generate report
    report = format_report(all_metrics)
    print(report)  # noqa: T201

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_file = RESULTS_DIR / "product_search_comparison.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "metrics": [
                    {
                        "name": m.name,
                        "mrr": m.mrr,
                        "precision_at_5": m.precision_at_5,
                        "recall_at_10": m.recall_at_10,
                        "recall_at_5": m.recall_at_5,
                        "latency_p50_ms": m.latency_p50_ms,
                        "latency_p95_ms": m.latency_p95_ms,
                        "error_count": m.error_count,
                        "per_difficulty": m.per_difficulty,
                        "successful_cases": m.successful_cases,
                        "total_cases": m.total_cases,
                    }
                    for m in all_metrics
                ],
                "schema_filter": args.schema,
                "total_test_cases": len(test_cases),
            },
            f,
            indent=2,
        )
    logger.info("Results saved to %s", results_file)

    report_file = RESULTS_DIR / "product_search_comparison.md"
    with open(report_file, "w") as f:
        f.write(report)
    logger.info("Report saved to %s", report_file)


if __name__ == "__main__":
    asyncio.run(main())
