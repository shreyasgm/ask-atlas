"""Integration tests for the docs index build pipeline.

Exercises each build phase with real external services on a single chunk:
1. Contextual summary generation (real LLM via litellm)
2. HyPE question generation (real LLM via litellm)
3. Embedding via Vertex AI text-embedding-005
4. Full single-chunk build: chunk → LLM enrichment → embed → SQLite → search

Requires: OpenAI API key + GCloud ADC configured.

Run::

    PYTHONPATH=$(pwd) uv run pytest src/tests/test_build_docs_index_integration.py -m integration -v
"""

from __future__ import annotations

import asyncio
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_docs_index import (  # noqa: E402
    build_index,
    embed_texts,
    generate_contextual_summary,
    generate_hype_questions,
)
from src.docs_retrieval import EMBEDDING_DIM  # noqa: E402

pytestmark = pytest.mark.integration

# A realistic chunk from the Atlas documentation
SAMPLE_CHUNK_BODY = (
    "The Economic Complexity Index (ECI) measures the diversity and sophistication "
    "of a country's export basket. It is derived from the Method of Reflections, "
    "which iteratively computes the average complexity of the products a country "
    "exports and the average complexity of the countries that export those products. "
    "Countries with high ECI values tend to have diverse economies producing "
    "complex, knowledge-intensive goods."
)
SAMPLE_DOC_TITLE = "Metrics Glossary"
SAMPLE_SECTION_TITLE = "Economic Complexity Index (ECI)"


# ---------------------------------------------------------------------------
# Tests: contextual summary (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestContextualSummaryIntegration:
    async def test_generates_relevant_summary(self):
        """Real LLM should produce a coherent contextual summary."""
        sem = asyncio.Semaphore(5)
        result = await generate_contextual_summary(
            SAMPLE_CHUNK_BODY, SAMPLE_DOC_TITLE, sem
        )

        assert isinstance(result, str)
        assert len(result) > 20, f"Summary too short: {result!r}"
        # Should reference something related to the content
        result_lower = result.lower()
        assert any(
            term in result_lower
            for term in ["eci", "complexity", "export", "metric", "index"]
        ), f"Summary seems unrelated to ECI content: {result!r}"


# ---------------------------------------------------------------------------
# Tests: HyPE question generation (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHypeQuestionsIntegration:
    async def test_generates_valid_questions(self):
        """Real LLM should produce parseable, relevant HyPE questions."""
        sem = asyncio.Semaphore(5)
        questions = await generate_hype_questions(
            SAMPLE_CHUNK_BODY, SAMPLE_DOC_TITLE, SAMPLE_SECTION_TITLE, sem
        )

        assert isinstance(questions, list)
        assert len(questions) >= 3, (
            f"Expected at least 3 questions, got {len(questions)}: {questions}"
        )
        assert len(questions) <= 5, (
            f"Expected at most 5 questions, got {len(questions)}"
        )

        for q in questions:
            assert isinstance(q, str)
            assert len(q) > 10, f"Question too short: {q!r}"
            # Each question should end with a question mark (basic sanity)
            assert q.rstrip().endswith("?"), f"Not a question: {q!r}"


# ---------------------------------------------------------------------------
# Tests: Vertex AI embedding
# ---------------------------------------------------------------------------


class TestEmbeddingIntegration:
    def test_embeds_single_text(self):
        """Vertex AI text-embedding-005 should return a 768-dim vector."""
        texts = ["What is the Economic Complexity Index?"]
        embeddings = embed_texts(texts, task_type="RETRIEVAL_QUERY")

        assert len(embeddings) == 1
        assert len(embeddings[0]) == EMBEDDING_DIM
        # Values should be floats in a reasonable range
        assert all(isinstance(v, float) for v in embeddings[0])
        assert any(v != 0.0 for v in embeddings[0]), "Embedding is all zeros"

    def test_embeds_batch(self):
        """Embedding multiple texts should return one vector per text."""
        texts = [
            "What is ECI?",
            "How is Product Complexity Index calculated?",
            "Trade data coverage years",
        ]
        embeddings = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        assert len(embeddings) == 3
        for emb in embeddings:
            assert len(emb) == EMBEDDING_DIM

    def test_different_texts_produce_different_embeddings(self):
        """Semantically different texts should produce different vectors."""
        texts = [
            "The Economic Complexity Index measures country sophistication",
            "Bilateral trade data covers the period from 1962 to 2024",
        ]
        embeddings = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")

        # Compute cosine similarity — should be noticeably below 1.0
        dot = sum(a * b for a, b in zip(embeddings[0], embeddings[1]))
        norm0 = sum(a * a for a in embeddings[0]) ** 0.5
        norm1 = sum(b * b for b in embeddings[1]) ** 0.5
        cosine_sim = dot / (norm0 * norm1) if norm0 and norm1 else 0.0

        assert cosine_sim < 0.95, (
            f"Unrelated texts have suspiciously high similarity: {cosine_sim:.4f}"
        )


# ---------------------------------------------------------------------------
# Tests: full single-chunk build pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFullBuildIntegration:
    async def test_single_chunk_build_and_search(self, tmp_path: Path):
        """Build an index from one doc file, then verify search works end-to-end.

        Exercises: chunking → real LLM summaries → real LLM HyPE questions
        → real Vertex AI embeddings → SQLite FTS5 + vec → search retrieval.
        """
        # Create a minimal docs directory with one file
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "test_eci.md").write_text(
            '---\ntitle: "ECI Overview"\npurpose: "test"\n'
            'when_to_load: "always"\nkeywords:\n  - eci\n  - complexity\n---\n\n'
            f"## ECI Definition\n\n{SAMPLE_CHUNK_BODY}\n"
        )

        output_db = tmp_path / "test_index.db"

        # Build the index with real LLM + real embeddings
        await build_index(docs_dir, output_db, force=True, concurrency=5)

        # Verify the database has expected content
        conn = sqlite3.connect(str(output_db))

        # 1. Chunks inserted
        chunks = conn.execute(
            "SELECT chunk_id, section_title, body, contextual_summary FROM chunks"
        ).fetchall()
        assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
        chunk_id, section_title, body, summary = chunks[0]
        assert section_title == "ECI Definition"
        assert "Economic Complexity Index" in body
        assert len(summary) > 10, f"Summary too short: {summary!r}"

        # 2. HyPE questions generated
        questions = conn.execute(
            "SELECT question FROM hype_questions WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchall()
        assert len(questions) >= 3, (
            f"Expected at least 3 HyPE questions, got {len(questions)}"
        )

        # 3. Chunk embedding stored with correct dimensionality
        emb_row = conn.execute(
            "SELECT embedding FROM chunk_embeddings WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        assert emb_row is not None, "No chunk embedding found"
        emb_floats = struct.unpack(f"<{len(emb_row[0]) // 4}f", emb_row[0])
        assert len(emb_floats) == EMBEDDING_DIM

        # 4. HyPE embeddings stored
        hype_emb_count = conn.execute(
            "SELECT COUNT(*) FROM hype_embeddings"
        ).fetchone()[0]
        assert hype_emb_count == len(questions), (
            f"Expected {len(questions)} HyPE embeddings, got {hype_emb_count}"
        )

        # 5. FTS5 search works
        fts_results = conn.execute(
            """
            SELECT c.section_title FROM chunks_fts fts
            JOIN chunks c ON c.rowid = fts.rowid
            WHERE chunks_fts MATCH '"complexity"'
            """
        ).fetchall()
        assert len(fts_results) >= 1

        # 6. File checksum stored
        checksums = conn.execute("SELECT filename FROM file_checksums").fetchall()
        assert len(checksums) == 1
        assert checksums[0][0] == "test_eci.md"

        conn.close()

        # 7. DocsIndex search retrieves our chunk
        from src.docs_retrieval import DocsIndex

        index = DocsIndex(output_db)
        results = await index.search("What is ECI?", top_k=3)
        index.close()

        assert len(results) >= 1, "DocsIndex.search returned no results"
        assert any("ECI" in r.body or "complexity" in r.body.lower() for r in results)
