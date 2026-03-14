"""Hybrid retrieval module for documentation chunks.

Provides:
- ``DocChunk``: Dataclass representing a retrieved documentation chunk.
- ``DocsIndex``: SQLite-backed hybrid search (BM25 + vector) over pre-indexed docs.
- ``chunk_markdown_by_headers()``: Section-level markdown chunking utility.
- ``format_chunks_for_prompt()``: Format chunks as XML for system prompt injection.
- ``rrf_fuse()``: Reciprocal Rank Fusion across multiple ranked lists.

The SQLite index is built offline by ``scripts/build_docs_index.py`` and contains:
- FTS5 full-text index for BM25 keyword search
- sqlite-vec embeddings for semantic search on chunk content
- sqlite-vec embeddings for HyPE (hypothetical question) search

At runtime, ``DocsIndex.search()`` performs hybrid retrieval in ~100-200ms
with zero LLM calls, replacing the old select_docs + synthesize_docs pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "gemini-embedding-2-preview"
# MRL truncation: 768 dims is the sweet spot — near-peak quality at 1/4 storage.
# See https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2-preview
EMBEDDING_DIM = 768


@dataclass(frozen=True)
class DocChunk:
    """A single documentation chunk retrieved from the index."""

    chunk_id: str
    doc_filename: str
    doc_title: str
    section_title: str
    body: str
    score: float = 0.0


# ---------------------------------------------------------------------------
# Chunking utilities
# ---------------------------------------------------------------------------


def _count_tokens_approx(text: str) -> int:
    """Approximate token count (~4 chars per token for English text)."""
    return len(text) // 4


def chunk_markdown_by_headers(
    body: str,
    filename: str,
    doc_title: str,
    keywords: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    """Split markdown body into section-level chunks by ``##`` headers.

    Sections exceeding 1500 tokens are sub-split by ``###`` headers.
    Each chunk gets a deterministic ``chunk_id`` based on filename + section title.

    Args:
        body: Markdown body text (after frontmatter removal).
        filename: Source document filename.
        doc_title: Document title from frontmatter.
        keywords: Optional keywords from frontmatter.

    Returns:
        List of dicts with keys: chunk_id, doc_filename, doc_title,
        section_title, body.
    """
    if not body.strip():
        return []

    chunks: list[dict[str, str]] = []
    # Split by ## headers
    sections = _split_by_header(body, "## ")

    for section_title, section_body in sections:
        if not section_body.strip():
            continue

        # Use doc_title for preamble (content before first header)
        if not section_title:
            section_title = doc_title

        # Sub-split large sections by ### headers
        if _count_tokens_approx(section_body) > 1500:
            subsections = _split_by_header(section_body, "### ")
            for sub_title, sub_body in subsections:
                if not sub_body.strip():
                    continue
                full_title = (
                    f"{section_title} > {sub_title}" if sub_title else section_title
                )
                chunk_id = _make_chunk_id(filename, full_title)
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_filename": filename,
                        "doc_title": doc_title,
                        "section_title": full_title,
                        "body": sub_body.strip(),
                    }
                )
        else:
            chunk_id = _make_chunk_id(filename, section_title)
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "doc_filename": filename,
                    "doc_title": doc_title,
                    "section_title": section_title,
                    "body": section_body.strip(),
                }
            )

    return chunks


def _split_by_header(text: str, header_prefix: str) -> list[tuple[str, str]]:
    """Split text by markdown header lines.

    Returns list of (title, body) tuples. Content before the first header
    gets title="" (preamble).

    Args:
        text: The text to split.
        header_prefix: The header prefix to split on (e.g., "## ").

    Returns:
        List of (title, body) tuples.
    """
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in lines:
        if line.startswith(header_prefix):
            # Save previous section
            if current_lines or current_title:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = line[len(header_prefix) :].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_lines or current_title:
        sections.append((current_title, "\n".join(current_lines)))

    return sections


def _make_chunk_id(filename: str, section_title: str) -> str:
    """Create a deterministic chunk ID from filename and section title."""
    raw = f"{filename}::{section_title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Serialize a float list to little-endian bytes for sqlite-vec."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def _deserialize_embedding(data: bytes) -> list[float]:
    """Deserialize little-endian bytes to a float list."""
    n = len(data) // 4
    return list(struct.unpack(f"<{n}f", data))


def _create_genai_client() -> genai.Client:  # noqa: F821
    """Create a google-genai Client using Gemini Developer API credentials.

    Uses GOOGLE_API_KEY or GEMINI_API_KEY from the environment.
    If neither is set, creates a client that will attempt auto-detection.

    Returns:
        A configured genai.Client instance.
    """
    import os

    from google import genai

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)

    return genai.Client()


def _normalize_embedding(vec: list[float]) -> list[float]:
    """L2-normalize an embedding vector.

    MRL-truncated embeddings from gemini-embedding-2 require manual
    normalization (only the full 3072-dim output is pre-normalized).
    """
    import math

    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


async def _embed_query(text: str) -> list[float] | None:
    """Embed a single query string via the Gemini embedding model.

    Uses MRL truncation to EMBEDDING_DIM dimensions and normalizes
    the result. Returns None on failure (caller falls back to BM25-only).

    Args:
        text: The query text to embed.

    Returns:
        Normalized embedding vector as a list of floats, or None on error.
    """
    try:
        from google.genai import types

        client = _create_genai_client()
        response = await client.aio.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=EMBEDDING_DIM,
            ),
        )
        return _normalize_embedding(response.embeddings[0].values)
    except Exception:
        logger.warning("Embedding API failed; falling back to BM25-only", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


def rrf_fuse(
    *ranked_lists: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked lists of IDs.

    Args:
        *ranked_lists: Each list is an ordered sequence of chunk_ids
            (best first).
        k: RRF constant (default 60, standard value).

    Returns:
        List of (chunk_id, fused_score) sorted by descending score.
    """
    scores: dict[str, float] = {}
    for ranked_list in ranked_lists:
        for rank, chunk_id in enumerate(ranked_list):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# DocsIndex — read-only hybrid search over pre-built SQLite index
# ---------------------------------------------------------------------------


class DocsIndex:
    """Read-only hybrid retrieval over a pre-built documentation index.

    The index is a SQLite database with FTS5 full-text search and
    sqlite-vec vector indexes built by ``scripts/build_docs_index.py``.

    Usage::

        index = DocsIndex(Path("src/docs_index.db"))
        chunks = await index.search("What is ECI?", top_k=6)
    """

    def __init__(self, db_path: Path) -> None:
        """Open the SQLite index in read-only mode.

        Args:
            db_path: Path to the pre-built SQLite index file.

        Raises:
            FileNotFoundError: If the index file does not exist.
        """
        if not db_path.exists():
            raise FileNotFoundError(f"Docs index not found: {db_path}")

        self._db_path = db_path
        self._conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row

        # Try to load sqlite-vec extension
        self._has_vec = False
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._has_vec = True
        except Exception:
            logger.warning(
                "sqlite-vec not available; vector search disabled, using BM25 only"
            )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    async def search(
        self,
        query: str,
        top_k: int = 6,
        exclude_chunk_ids: frozenset[str] = frozenset(),
    ) -> list[DocChunk]:
        """Hybrid search: BM25 + chunk vectors + HyPE vectors, fused via RRF.

        Falls back to BM25-only if embedding API or sqlite-vec is unavailable.

        Args:
            query: The search query string.
            top_k: Number of top results to return.
            exclude_chunk_ids: Chunk IDs to exclude from results (e.g.,
                already auto-injected chunks).

        Returns:
            List of DocChunk results, best first.
        """
        import asyncio

        # BM25 search (always available)
        bm25_ids = await asyncio.to_thread(self._bm25_search, query, 20)

        # Vector searches (if available)
        chunk_vec_ids: list[str] = []
        hype_vec_ids: list[str] = []

        if self._has_vec:
            embedding = await _embed_query(query)
            if embedding is not None:
                emb_bytes = _serialize_embedding(embedding)
                chunk_vec_ids, hype_vec_ids = await asyncio.to_thread(
                    self._vector_searches, emb_bytes, 20
                )

        # Fuse results
        if chunk_vec_ids or hype_vec_ids:
            fused = rrf_fuse(bm25_ids, chunk_vec_ids, hype_vec_ids)
        else:
            # BM25 only
            fused = [(cid, 1.0 / (60 + i + 1)) for i, cid in enumerate(bm25_ids)]

        # Filter excluded chunks and take top_k
        filtered = [
            (cid, score) for cid, score in fused if cid not in exclude_chunk_ids
        ][:top_k]

        # Fetch full chunk data
        if not filtered:
            return []

        return await asyncio.to_thread(
            self._fetch_chunks, [(cid, score) for cid, score in filtered]
        )

    @staticmethod
    def _build_fts_query(query: str) -> str:
        """Build an FTS5 MATCH expression with phrase + AND fallback.

        Strategy: try phrase match first (highest precision), then AND of
        all terms (moderate recall), then AND with prefix on last term
        (highest recall).  BM25 naturally ranks phrase matches highest.

        FTS5 column order is (body, section_title, doc_title), so column
        weights in bm25() should follow this order.

        Args:
            query: Raw user query string.

        Returns:
            FTS5 MATCH expression string, or empty string if no terms.
        """

        def _safe(term: str) -> str:
            """Quote a single term, stripping double-quote chars."""
            return f'"{term.replace(chr(34), "")}"'

        terms = [t for t in query.split() if t.strip()]
        if not terms:
            return ""

        parts: list[str] = []

        # 1. Exact phrase match (if multi-word)
        if len(terms) > 1:
            phrase = " ".join(t.replace(chr(34), "") for t in terms)
            parts.append(f'"{phrase}"')

        # 2. AND of all terms (for when phrase doesn't match exactly)
        safe_terms = [_safe(t) for t in terms]
        parts.append(f"({' AND '.join(safe_terms)})")

        # 3. AND with prefix on last term (for partial-word recall)
        if len(terms) > 1:
            prefix_terms = safe_terms[:-1] + [f"{terms[-1].replace(chr(34), '')}*"]
            parts.append(f"({' AND '.join(prefix_terms)})")

        return " OR ".join(parts)

    def _bm25_search(self, query: str, limit: int) -> list[str]:
        """BM25 keyword search via FTS5 with phrase matching and column weights.

        Uses phrase + AND + prefix query construction for balanced
        precision/recall.  Weights doc_title 10x and section_title 5x
        over body to prioritize title matches.

        Args:
            query: The search query.
            limit: Maximum results to return.

        Returns:
            List of chunk_ids ordered by BM25 relevance.
        """
        try:
            fts_query = self._build_fts_query(query)
            if not fts_query:
                return []

            # Column order: body, section_title, doc_title
            # Weights:      1.0,  5.0,           10.0
            rows = self._conn.execute(
                """
                SELECT c.chunk_id
                FROM chunks_fts fts
                JOIN chunks c ON c.rowid = fts.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts, 1.0, 5.0, 10.0)
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
            return [row["chunk_id"] for row in rows]
        except Exception:
            logger.warning("BM25 search failed", exc_info=True)
            return []

    def _vector_searches(
        self, emb_bytes: bytes, limit: int
    ) -> tuple[list[str], list[str]]:
        """Run both chunk and HyPE vector searches.

        Args:
            emb_bytes: Serialized query embedding.
            limit: Maximum results per search.

        Returns:
            Tuple of (chunk_vector_ids, hype_vector_ids).
        """
        chunk_ids: list[str] = []
        hype_ids: list[str] = []

        # vec0 KNN queries require `AND k = ?` and don't support JOINs
        # inside the query. Use CTEs to join back to source tables.

        try:
            rows = self._conn.execute(
                """
                SELECT chunk_id, distance
                FROM chunk_vec
                WHERE embedding MATCH ?
                  AND k = ?
                """,
                (emb_bytes, limit),
            ).fetchall()
            chunk_ids = [row["chunk_id"] for row in rows]
        except Exception:
            logger.warning("Chunk vector search failed", exc_info=True)

        try:
            # Fetch nearest HyPE question vectors, then resolve to chunk IDs.
            # We fetch limit*5 questions to ensure enough distinct chunks after
            # deduplication (each chunk has ~5 questions, so many may match).
            hype_limit = limit * 5
            rows = self._conn.execute(
                """
                WITH knn AS (
                    SELECT question_id, distance
                    FROM hype_vec
                    WHERE embedding MATCH ?
                      AND k = ?
                )
                SELECT hq.chunk_id, MIN(knn.distance) AS best_distance
                FROM knn
                JOIN hype_questions hq ON hq.question_id = knn.question_id
                GROUP BY hq.chunk_id
                ORDER BY best_distance
                """,
                (emb_bytes, hype_limit),
            ).fetchall()
            hype_ids = [row["chunk_id"] for row in rows][:limit]
        except Exception:
            logger.warning("HyPE vector search failed", exc_info=True)

        return chunk_ids, hype_ids

    def _fetch_chunks(self, id_scores: list[tuple[str, float]]) -> list[DocChunk]:
        """Fetch full chunk data for a list of (chunk_id, score) pairs.

        Args:
            id_scores: List of (chunk_id, score) tuples.

        Returns:
            List of DocChunk objects in the same order as input.
        """
        if not id_scores:
            return []

        ids = [cid for cid, _ in id_scores]
        score_map = dict(id_scores)
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"""
            SELECT chunk_id, doc_filename, doc_title, section_title, body
            FROM chunks
            WHERE chunk_id IN ({placeholders})
            """,
            ids,
        ).fetchall()

        row_map = {row["chunk_id"]: row for row in rows}
        chunks: list[DocChunk] = []
        for cid in ids:
            row = row_map.get(cid)
            if row:
                chunks.append(
                    DocChunk(
                        chunk_id=row["chunk_id"],
                        doc_filename=row["doc_filename"],
                        doc_title=row["doc_title"],
                        section_title=row["section_title"],
                        body=row["body"],
                        score=score_map.get(cid, 0.0),
                    )
                )
        return chunks


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def format_chunks_for_prompt(chunks: list[DocChunk | dict]) -> str:
    """Format retrieved chunks as XML for injection into the agent system prompt.

    Accepts either DocChunk objects or plain dicts with the same keys.

    Args:
        chunks: List of DocChunk objects or dicts with chunk data.

    Returns:
        XML-formatted string suitable for appending to the system prompt.
    """
    if not chunks:
        return ""

    parts: list[str] = ["<documentation_context>"]
    for chunk in chunks:
        if isinstance(chunk, dict):
            section = chunk.get("section_title", "")
            body = chunk.get("body", "")
            filename = chunk.get("doc_filename", "")
        else:
            section = chunk.section_title
            body = chunk.body
            filename = chunk.doc_filename

        parts.append(
            f'<doc_chunk source="{filename}" section="{section}">\n{body}\n</doc_chunk>'
        )
    parts.append("</documentation_context>")
    return "\n".join(parts)
