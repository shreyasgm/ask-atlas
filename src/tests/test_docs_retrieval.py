"""Unit tests for the docs retrieval module.

Tests cover chunking, RRF fusion, prompt formatting, DocsIndex BM25 search,
exclude logic, and embedding fallback behavior.
All tests are unit tests — no external services required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.docs_retrieval import (
    DocChunk,
    DocsIndex,
    _make_chunk_id,
    _normalize_embedding,
    _split_by_header,
    chunk_markdown_by_headers,
    format_chunks_for_prompt,
    rrf_fuse,
)

# ---------------------------------------------------------------------------
# Tests: chunk_markdown_by_headers
# ---------------------------------------------------------------------------


class TestChunkMarkdownByHeaders:
    def test_splits_by_h2_headers(self):
        body = (
            "## Introduction\n\nIntro content here.\n\n"
            "## Methods\n\nMethods content here."
        )
        chunks = chunk_markdown_by_headers(body, "test.md", "Test Doc")
        assert len(chunks) == 2
        assert chunks[0]["section_title"] == "Introduction"
        assert "Intro content" in chunks[0]["body"]
        assert chunks[1]["section_title"] == "Methods"
        assert "Methods content" in chunks[1]["body"]

    def test_assigns_deterministic_chunk_ids(self):
        body = "## Section A\n\nContent A.\n\n## Section B\n\nContent B."
        chunks = chunk_markdown_by_headers(body, "doc.md", "Doc")
        ids = [c["chunk_id"] for c in chunks]
        assert len(set(ids)) == 2  # unique IDs
        # Running again produces same IDs
        chunks2 = chunk_markdown_by_headers(body, "doc.md", "Doc")
        assert [c["chunk_id"] for c in chunks2] == ids

    def test_handles_preamble_before_first_header(self):
        body = "Preamble text.\n\n## First Section\n\nSection content."
        chunks = chunk_markdown_by_headers(body, "doc.md", "Doc")
        assert len(chunks) == 2
        # First chunk is preamble (uses doc_title as section_title)
        assert chunks[0]["section_title"] == "Doc"
        assert "Preamble" in chunks[0]["body"]

    def test_sub_splits_large_sections_by_h3(self):
        # Create a section with >1500 tokens (~6000+ chars)
        large_body = "## Big Section\n\n"
        large_body += "### Sub A\n\n" + "Word " * 1000 + "\n\n"
        large_body += "### Sub B\n\n" + "Word " * 1000 + "\n"
        chunks = chunk_markdown_by_headers(large_body, "big.md", "Big Doc")
        assert len(chunks) >= 2
        # Should have sub-section titles
        titles = [c["section_title"] for c in chunks]
        assert any("Sub A" in t for t in titles)
        assert any("Sub B" in t for t in titles)

    def test_empty_body_returns_empty(self):
        chunks = chunk_markdown_by_headers("", "empty.md", "Empty")
        assert chunks == []

    def test_no_headers_single_chunk(self):
        body = "Just some plain text without any headers."
        chunks = chunk_markdown_by_headers(body, "plain.md", "Plain Doc")
        assert len(chunks) == 1
        assert chunks[0]["section_title"] == "Plain Doc"
        assert "plain text" in chunks[0]["body"]

    def test_chunk_fields_populated(self):
        body = "## Metrics\n\nECI measures complexity."
        chunks = chunk_markdown_by_headers(body, "metrics.md", "Metrics Guide")
        chunk = chunks[0]
        assert chunk["doc_filename"] == "metrics.md"
        assert chunk["doc_title"] == "Metrics Guide"
        assert chunk["section_title"] == "Metrics"
        assert "ECI" in chunk["body"]
        assert len(chunk["chunk_id"]) == 16


# ---------------------------------------------------------------------------
# Tests: _split_by_header
# ---------------------------------------------------------------------------


class TestSplitByHeader:
    def test_splits_correctly(self):
        text = "Preamble\n## A\nContent A\n## B\nContent B"
        sections = _split_by_header(text, "## ")
        assert len(sections) == 3
        assert sections[0] == ("", "Preamble")
        assert sections[1] == ("A", "Content A")
        assert sections[2] == ("B", "Content B")

    def test_no_headers(self):
        text = "Just text"
        sections = _split_by_header(text, "## ")
        assert len(sections) == 1
        assert sections[0] == ("", "Just text")


# ---------------------------------------------------------------------------
# Tests: rrf_fuse
# ---------------------------------------------------------------------------


class TestRRFFuse:
    def test_single_list(self):
        result = rrf_fuse(["a", "b", "c"])
        ids = [cid for cid, _ in result]
        assert ids == ["a", "b", "c"]

    def test_multiple_lists_boost_overlap(self):
        list1 = ["a", "b", "c"]
        list2 = ["b", "a", "d"]
        result = rrf_fuse(list1, list2)
        ids = [cid for cid, _ in result]
        # "a" and "b" appear in both lists, should rank higher
        assert set(ids[:2]) == {"a", "b"}
        # "c" and "d" each appear once
        assert set(ids[2:]) == {"c", "d"}

    def test_empty_lists(self):
        result = rrf_fuse([], [])
        assert result == []

    def test_scores_are_positive(self):
        result = rrf_fuse(["x", "y"])
        for _, score in result:
            assert score > 0

    def test_k_parameter(self):
        # With smaller k, scores change but relative order stays the same
        result_k60 = rrf_fuse(["a", "b"], k=60)
        result_k10 = rrf_fuse(["a", "b"], k=10)
        # Both should have same ordering
        assert [cid for cid, _ in result_k60] == [cid for cid, _ in result_k10]
        # But k=10 gives higher individual scores
        assert result_k10[0][1] > result_k60[0][1]


# ---------------------------------------------------------------------------
# Tests: format_chunks_for_prompt
# ---------------------------------------------------------------------------


class TestFormatChunksForPrompt:
    def test_formats_doc_chunks(self):
        chunks = [
            DocChunk(
                chunk_id="abc123",
                doc_filename="metrics.md",
                doc_title="Metrics",
                section_title="ECI",
                body="ECI measures complexity.",
                score=0.5,
            )
        ]
        result = format_chunks_for_prompt(chunks)
        assert "<documentation_context>" in result
        assert "</documentation_context>" in result
        assert 'source="metrics.md"' in result
        assert 'section="ECI"' in result
        assert "ECI measures complexity." in result

    def test_formats_dicts(self):
        chunks = [
            {
                "chunk_id": "abc123",
                "doc_filename": "data.md",
                "doc_title": "Data",
                "section_title": "Coverage",
                "body": "Data covers 2000-2024.",
            }
        ]
        result = format_chunks_for_prompt(chunks)
        assert 'source="data.md"' in result
        assert "Data covers 2000-2024." in result

    def test_empty_returns_empty_string(self):
        assert format_chunks_for_prompt([]) == ""

    def test_multiple_chunks(self):
        chunks = [
            DocChunk("id1", "a.md", "A", "S1", "Body 1"),
            DocChunk("id2", "b.md", "B", "S2", "Body 2"),
        ]
        result = format_chunks_for_prompt(chunks)
        assert result.count("<doc_chunk") == 2
        assert result.count("</doc_chunk>") == 2


# ---------------------------------------------------------------------------
# Tests: DocsIndex (with in-memory SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture
def docs_index_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite docs index for testing."""
    db_path = tmp_path / "test_docs.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_filename TEXT NOT NULL,
            doc_title TEXT NOT NULL,
            section_title TEXT NOT NULL,
            body TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            body, section_title, doc_title,
            content='chunks', content_rowid='rowid',
            tokenize='porter unicode61'
        );

        CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, body, section_title, doc_title)
            VALUES (new.rowid, new.body, new.section_title, new.doc_title);
        END;

        INSERT INTO chunks VALUES ('chunk1', 'metrics.md', 'Metrics Guide', 'ECI Definition',
            'The Economic Complexity Index (ECI) measures the knowledge and capabilities embedded in a country export basket.');
        INSERT INTO chunks VALUES ('chunk2', 'metrics.md', 'Metrics Guide', 'PCI Definition',
            'The Product Complexity Index (PCI) measures the amount of knowledge required to produce a product.');
        INSERT INTO chunks VALUES ('chunk3', 'trade.md', 'Trade Data', 'Coverage',
            'Trade data covers bilateral merchandise trade flows from 1962 to 2024.');
        INSERT INTO chunks VALUES ('chunk4', 'trade.md', 'Trade Data', 'Mirror Statistics',
            'Mirror statistics use partner reported data to fill gaps in direct reporting.');
        """
    )
    conn.commit()
    conn.close()
    return db_path


class TestDocsIndex:
    def test_bm25_search_finds_relevant_chunks(self, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        # BM25 search should find ECI-related chunk
        results = index._bm25_search("economic complexity index ECI", 5)
        assert len(results) > 0
        assert "chunk1" in results
        index.close()

    def test_bm25_phrase_match_ranks_eci_first(self, docs_index_db: Path):
        """Phrase matching + title weight should rank the ECI chunk first."""
        index = DocsIndex(docs_index_db)
        results = index._bm25_search("Economic Complexity Index", 5)
        assert len(results) > 0
        # The ECI definition chunk should be ranked first due to phrase match
        # and section_title containing "ECI Definition"
        assert results[0] == "chunk1"
        index.close()

    def test_bm25_search_ranks_relevant_higher(self, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        results = index._bm25_search("mirror statistics", 5)
        assert len(results) > 0
        # Mirror statistics chunk should appear in results
        assert "chunk4" in results
        index.close()

    def test_bm25_search_handles_empty_query(self, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        results = index._bm25_search("", 5)
        # Empty query may return nothing or all — just shouldn't crash
        assert isinstance(results, list)
        index.close()

    @patch("src.docs_retrieval._embed_query", new_callable=AsyncMock, return_value=None)
    async def test_search_excludes_chunk_ids(self, mock_embed, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        # Search with exclusion (BM25 only since embedding returns None)
        results = await index.search(
            "ECI economic complexity",
            top_k=10,
            exclude_chunk_ids=frozenset({"chunk1"}),
        )
        chunk_ids = [c.chunk_id for c in results]
        assert "chunk1" not in chunk_ids
        index.close()

    @patch("src.docs_retrieval._embed_query", new_callable=AsyncMock, return_value=None)
    async def test_search_returns_doc_chunks(self, mock_embed, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        results = await index.search("trade data coverage", top_k=3)
        assert len(results) > 0
        for chunk in results:
            assert isinstance(chunk, DocChunk)
            assert chunk.chunk_id
            assert chunk.body
            assert chunk.doc_filename
        index.close()

    @patch("src.docs_retrieval._embed_query", new_callable=AsyncMock, return_value=None)
    async def test_search_respects_top_k(self, mock_embed, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        results = await index.search("metrics", top_k=2)
        assert len(results) <= 2
        index.close()

    def test_nonexistent_db_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            DocsIndex(tmp_path / "nonexistent.db")

    def test_fetch_chunks_returns_correct_data(self, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        chunks = index._fetch_chunks([("chunk1", 0.5), ("chunk3", 0.3)])
        assert len(chunks) == 2
        assert chunks[0].chunk_id == "chunk1"
        assert chunks[0].score == 0.5
        assert "ECI" in chunks[0].body
        assert chunks[1].chunk_id == "chunk3"
        index.close()

    def test_fetch_chunks_handles_missing_ids(self, docs_index_db: Path):
        index = DocsIndex(docs_index_db)
        chunks = index._fetch_chunks([("nonexistent", 0.5)])
        assert len(chunks) == 0
        index.close()


# ---------------------------------------------------------------------------
# Tests: _make_chunk_id
# ---------------------------------------------------------------------------


class TestBuildFtsQuery:
    """Tests for DocsIndex._build_fts_query() — FTS5 query construction."""

    def test_single_term_produces_and_only(self):
        q = DocsIndex._build_fts_query("ECI")
        # Single term: just AND clause (no phrase, no prefix)
        assert '"ECI"' in q
        assert "OR" not in q

    def test_multi_term_includes_phrase_and_and(self):
        q = DocsIndex._build_fts_query("Economic Complexity Index")
        # Should have phrase match
        assert '"Economic Complexity Index"' in q
        # Should have AND fallback
        assert "AND" in q
        # Connected by OR
        assert "OR" in q

    def test_handles_quotes_in_input(self):
        q = DocsIndex._build_fts_query('the "ECI" metric')
        # Should not crash; quotes are stripped from terms
        assert q  # non-empty

    def test_empty_query_returns_empty(self):
        assert DocsIndex._build_fts_query("") == ""
        assert DocsIndex._build_fts_query("   ") == ""

    def test_prefix_matching_on_last_term(self):
        q = DocsIndex._build_fts_query("trade data cov")
        # Last part should have prefix wildcard
        assert "cov*" in q


class TestNormalizeEmbedding:
    """Tests for _normalize_embedding() — L2 normalization."""

    def test_unit_length_after_normalization(self):
        import math

        vec = [3.0, 4.0]  # 3-4-5 triangle
        normed = _normalize_embedding(vec)
        length = math.sqrt(sum(x * x for x in normed))
        assert abs(length - 1.0) < 1e-6

    def test_zero_vector_unchanged(self):
        vec = [0.0, 0.0, 0.0]
        normed = _normalize_embedding(vec)
        assert normed == [0.0, 0.0, 0.0]

    def test_already_normalized_unchanged(self):
        import math

        vec = [1.0 / math.sqrt(3), 1.0 / math.sqrt(3), 1.0 / math.sqrt(3)]
        normed = _normalize_embedding(vec)
        for a, b in zip(vec, normed):
            assert abs(a - b) < 1e-6


class TestMakeChunkId:
    def test_deterministic(self):
        id1 = _make_chunk_id("file.md", "Section A")
        id2 = _make_chunk_id("file.md", "Section A")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        id1 = _make_chunk_id("file.md", "Section A")
        id2 = _make_chunk_id("file.md", "Section B")
        assert id1 != id2

    def test_length(self):
        cid = _make_chunk_id("test.md", "Title")
        assert len(cid) == 16
