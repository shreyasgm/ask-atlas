"""Pluggable product search interface with embedding search backend.

Provides a uniform async API for the SQL pipeline to use. The winning
approach from Part B evaluation (embedding search with synonym-enriched
text, 86.4% recall@5 / 91.4% recall@10) is implemented here as
``EmbeddingProductSearch``.

The SQLite index is built offline by ``scripts/build_product_search_index.py``
and contains FTS5 full-text search + sqlite-vec embeddings for each product.
Search uses embedding-only (no hybrid RRF) because the evaluation showed
that hybrid embedding+BM25 *hurts* recall when the embedded text already
contains LLM-generated synonyms.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import struct
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Embedding settings — same model as docs pipeline
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 768


class ProductSearchBackend(Protocol):
    """Protocol for pluggable product search backends."""

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        """Search for products matching the query.

        Args:
            query: Natural language product name (e.g., "cars").
            schema: Classification schema (e.g., "hs12").
            top_k: Maximum results to return.

        Returns:
            List of dicts with at least ``product_code``, ``product_name``,
            ``product_id``, ``product_level`` keys, ordered by relevance.
        """
        ...

    async def verify_codes(self, codes: list[str], schema: str) -> list[dict[str, Any]]:
        """Verify that product codes exist in the catalog.

        Args:
            codes: List of product codes to verify.
            schema: Classification schema.

        Returns:
            List of verified product dicts (only codes that exist).
        """
        ...


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize a float list to little-endian bytes for sqlite-vec."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


async def _embed_query(text: str) -> list[float] | None:
    """Embed a single query string via OpenAI text-embedding-3-small.

    Returns None on failure (caller falls back to FTS-only).
    """
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text,
            dimensions=EMBEDDING_DIM,
        )
        return response.data[0].embedding
    except Exception:
        logger.warning("Embedding API failed; falling back to FTS-only", exc_info=True)
        return None


def _fts5_escape(query: str) -> str:
    """Build an FTS5 query from a natural language string.

    Splits into tokens and combines with OR for broad matching.
    Strips FTS5 special characters.
    """
    import re

    tokens = re.findall(r"\w+", query.lower())
    if not tokens:
        return query
    return " OR ".join(tokens)


def rrf_fuse(
    *ranked_lists: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked lists of IDs."""
    scores: dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, item_id in enumerate(ranked_list):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class EmbeddingProductSearch:
    """Embedding-based product search using a pre-built SQLite index.

    The index contains FTS5 full-text search and sqlite-vec embeddings
    for each product across classification schemas. Built offline by
    ``scripts/build_product_search_index.py``.

    Usage::

        search = EmbeddingProductSearch(Path("src/product_search.db"))
        results = await search.search("cars", "hs12", top_k=10)
    """

    def __init__(self, db_path: Path) -> None:
        """Open the SQLite index in read-only mode."""
        if not db_path.exists():
            raise FileNotFoundError(f"Product search index not found: {db_path}")
        self._db_path = db_path
        self._conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        # Load sqlite-vec extension
        import sqlite_vec

        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)

        # Verify embedding dimensions match
        self._check_embedding_dim()

    def _check_embedding_dim(self) -> None:
        """Verify the index embedding dimension matches the expected value."""
        try:
            cursor = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'embedding_dim'"
            )
            row = cursor.fetchone()
            if row:
                index_dim = int(row[0])
                if index_dim != EMBEDDING_DIM:
                    logger.error(
                        "Embedding dimension mismatch: index has %d, expected %d. "
                        "Rebuild the index with: uv run python scripts/build_product_search_index.py --force",
                        index_dim,
                        EMBEDDING_DIM,
                    )
                    raise ValueError(
                        f"Embedding dimension mismatch: index={index_dim}, expected={EMBEDDING_DIM}"
                    )
        except sqlite3.OperationalError:
            logger.warning("No meta table in index; skipping dimension check")

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    async def search(
        self, query: str, schema: str, top_k: int = 10
    ) -> list[dict[str, Any]]:
        """Embedding-only search via cosine similarity.

        Uses embedding search exclusively (no hybrid RRF with FTS5) because
        the evaluation showed that synonym-enriched embeddings outperform
        hybrid fusion.

        Args:
            query: Natural language product name.
            schema: Classification schema (e.g., "hs12").
            top_k: Maximum results to return.

        Returns:
            List of product dicts ordered by relevance.
        """
        return await self._vector_search(query, schema, top_k)

    async def verify_codes(self, codes: list[str], schema: str) -> list[dict[str, Any]]:
        """Verify that product codes exist in the index.

        Args:
            codes: List of product codes to verify.
            schema: Classification schema.

        Returns:
            List of verified product dicts (only codes that exist).
        """
        if not codes:
            return []

        def _verify() -> list[dict[str, Any]]:
            placeholders = ",".join("?" * len(codes))
            cursor = self._conn.execute(
                f"""
                SELECT product_code, product_name, product_id, product_level
                FROM products
                WHERE product_code IN ({placeholders}) AND schema = ?
                """,
                [*codes, schema],
            )
            return [dict(row) for row in cursor.fetchall()]

        return await asyncio.to_thread(_verify)

    async def _vector_search(
        self, query: str, schema: str, top_k: int
    ) -> list[dict[str, Any]]:
        """Semantic search via embedding similarity."""
        embedding = await _embed_query(query)
        if embedding is None:
            return []

        def _search() -> list[dict[str, Any]]:
            query_bytes = _serialize_embedding(embedding)
            cursor = self._conn.execute(
                """
                SELECT p.product_code, p.product_name, p.product_id,
                       p.product_level, v.distance
                FROM product_embeddings v
                JOIN products p ON v.rowid = p.rowid
                WHERE p.schema = ?
                  AND v.embedding MATCH ?
                  AND k = ?
                ORDER BY v.distance
                """,
                [schema, query_bytes, top_k * 2],
            )
            results = []
            seen = set()
            for row in cursor.fetchall():
                code = row[0]
                if code not in seen:
                    seen.add(code)
                    results.append(
                        {
                            "product_code": row[0],
                            "product_name": row[1],
                            "product_id": str(row[2]),
                            "product_level": str(row[3]),
                        }
                    )
            return results[:top_k]

        return await asyncio.to_thread(_search)

    def _fts_search(self, query: str, schema: str, top_k: int) -> list[dict[str, Any]]:
        """Full-text search via FTS5 BM25."""
        fts_query = _fts5_escape(query)
        try:
            cursor = self._conn.execute(
                """
                SELECT p.product_code, p.product_name, p.product_id,
                       p.product_level, fts.rank
                FROM products_fts fts
                JOIN products p ON fts.rowid = p.rowid
                WHERE products_fts MATCH ? AND p.schema = ?
                ORDER BY fts.rank
                LIMIT ?
                """,
                [fts_query, schema, top_k],
            )
            return [
                {
                    "product_code": row[0],
                    "product_name": row[1],
                    "product_id": str(row[2]),
                    "product_level": str(row[3]),
                }
                for row in cursor.fetchall()
            ]
        except sqlite3.OperationalError:
            logger.warning("FTS search failed for query: %s", query, exc_info=True)
            return []
