"""Tests for the docs index build script (scripts/build_docs_index.py).

Tests cover:
- Incremental updates via file checksums (skip unchanged, rebuild changed)
- Old data cleanup when a file changes
- HyPE question ID determinism
- LLM failure resilience (build completes despite LLM errors)
- Full pipeline: chunking → LLM enrichment → embedding → SQLite persistence
- Stale file removal from checksums table

All LLM and embedding API calls are mocked — no external services required.
"""

from __future__ import annotations

import hashlib
import sqlite3

# We need to import from the scripts directory, which adds project root to sys.path
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_docs_index import (  # noqa: E402
    build_index,
    compute_file_checksum,
    generate_contextual_summary,
    generate_hype_questions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SAMPLE_MD_CONTENT = """\
---
title: "Test Metrics"
purpose: "Testing"
when_to_load: "always"
keywords:
  - test
  - metrics
---

## Section Alpha

This section covers the Alpha metric, which measures the first-order complexity.

## Section Beta

The Beta metric is derived from Alpha and captures second-order effects.
It is commonly used in combination with other indices.
"""

SAMPLE_MD_CONTENT_V2 = """\
---
title: "Test Metrics"
purpose: "Testing"
when_to_load: "always"
keywords:
  - test
  - metrics
---

## Section Alpha

This section covers the Alpha metric (updated definition), which measures first-order complexity.

## Section Beta

The Beta metric is derived from Alpha and captures second-order effects.
It is commonly used in combination with other indices.

## Section Gamma

A brand new section about the Gamma metric.
"""


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """Create a temp docs directory with sample markdown files."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "test_metrics.md").write_text(SAMPLE_MD_CONTENT)
    return d


@pytest.fixture
def output_db(tmp_path: Path) -> Path:
    """Path for the output SQLite database."""
    return tmp_path / "docs_index.db"


def _mock_litellm_response(content: str) -> MagicMock:
    """Create a mock litellm response object."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    return response


# ---------------------------------------------------------------------------
# Tests: compute_file_checksum
# ---------------------------------------------------------------------------


class TestComputeFileChecksum:
    def test_returns_hex_digest(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = compute_file_checksum(f)
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 hex digest length

    def test_different_content_different_checksum(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert compute_file_checksum(f1) != compute_file_checksum(f2)

    def test_same_content_same_checksum(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        assert compute_file_checksum(f1) == compute_file_checksum(f2)


# ---------------------------------------------------------------------------
# Tests: generate_contextual_summary (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGenerateContextualSummary:
    async def test_returns_llm_response(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        mock_response = _mock_litellm_response("This chunk covers the Alpha metric.")
        with patch(
            "litellm.acompletion", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await generate_contextual_summary(
                "Alpha metric content", "Metrics Doc", sem
            )
        assert result == "This chunk covers the Alpha metric."

    async def test_retries_once_on_failure_then_succeeds(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        call_count = 0

        async def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient error")
            return _mock_litellm_response("Recovered summary.")

        with patch("litellm.acompletion", side_effect=fail_then_succeed):
            result = await generate_contextual_summary("content", "Doc", sem)

        assert result == "Recovered summary."
        assert call_count == 2

    async def test_returns_empty_after_two_failures(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ) as mock_llm:
            result = await generate_contextual_summary("content", "Doc", sem)
        assert result == ""
        assert mock_llm.call_count == 2  # tried twice

    async def test_respects_semaphore(self):
        """Verify the semaphore actually limits concurrency."""
        import asyncio

        sem = asyncio.Semaphore(2)
        max_concurrent = 0
        current_concurrent = 0

        async def tracking_acompletion(*args, **kwargs):
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            max_concurrent = max(max_concurrent, current_concurrent)
            await asyncio.sleep(0.05)  # Simulate API latency
            current_concurrent -= 1
            return _mock_litellm_response("summary")

        with patch("litellm.acompletion", side_effect=tracking_acompletion):
            tasks = [
                generate_contextual_summary(f"chunk {i}", "Doc", sem) for i in range(6)
            ]
            await asyncio.gather(*tasks)

        assert max_concurrent <= 2, (
            f"Semaphore(2) allowed {max_concurrent} concurrent calls"
        )


# ---------------------------------------------------------------------------
# Tests: generate_hype_questions (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGenerateHypeQuestions:
    async def test_parses_newline_separated_questions(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        mock_response = _mock_litellm_response(
            "What is the Alpha metric?\n"
            "How does Alpha relate to complexity?\n"
            "Where is Alpha used?\n"
            "What data does Alpha need?\n"
            "How is Alpha calculated?"
        )
        with patch(
            "litellm.acompletion", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await generate_hype_questions("content", "Doc", "Alpha", sem)
        assert len(result) == 5
        assert "What is the Alpha metric?" in result

    async def test_truncates_to_5_questions(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        # LLM returns 7 questions
        mock_response = _mock_litellm_response(
            "\n".join(f"Question {i}?" for i in range(7))
        )
        with patch(
            "litellm.acompletion", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await generate_hype_questions("content", "Doc", "Section", sem)
        assert len(result) == 5

    async def test_filters_blank_lines(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        mock_response = _mock_litellm_response("Q1?\n\n\nQ2?\n\nQ3?")
        with patch(
            "litellm.acompletion", new_callable=AsyncMock, return_value=mock_response
        ):
            result = await generate_hype_questions("content", "Doc", "Section", sem)
        assert len(result) == 3
        assert "" not in result

    async def test_retries_once_on_failure_then_succeeds(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        call_count = 0

        async def fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient error")
            return _mock_litellm_response("Q1?\nQ2?\nQ3?")

        with patch("litellm.acompletion", side_effect=fail_then_succeed):
            result = await generate_hype_questions("content", "Doc", "Section", sem)

        assert len(result) == 3
        assert call_count == 2

    async def test_returns_empty_after_two_failures(self):
        import asyncio

        sem = asyncio.Semaphore(5)
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fail"),
        ) as mock_llm:
            result = await generate_hype_questions("content", "Doc", "Section", sem)
        assert result == []
        assert mock_llm.call_count == 2  # tried twice


# ---------------------------------------------------------------------------
# Tests: HyPE question ID determinism
# ---------------------------------------------------------------------------


class TestHypeQuestionIdDeterminism:
    def test_same_inputs_same_id(self):
        chunk_id = "abc123"
        question = "What is ECI?"
        id1 = hashlib.sha256(f"{chunk_id}::{question}".encode()).hexdigest()[:16]
        id2 = hashlib.sha256(f"{chunk_id}::{question}".encode()).hexdigest()[:16]
        assert id1 == id2

    def test_different_questions_different_ids(self):
        chunk_id = "abc123"
        id1 = hashlib.sha256(f"{chunk_id}::What is ECI?".encode()).hexdigest()[:16]
        id2 = hashlib.sha256(f"{chunk_id}::What is PCI?".encode()).hexdigest()[:16]
        assert id1 != id2

    def test_different_chunks_different_ids(self):
        question = "What is ECI?"
        id1 = hashlib.sha256(f"chunk1::{question}".encode()).hexdigest()[:16]
        id2 = hashlib.sha256(f"chunk2::{question}".encode()).hexdigest()[:16]
        assert id1 != id2


# ---------------------------------------------------------------------------
# Tests: build_index (full pipeline, mocked LLM + embeddings)
# ---------------------------------------------------------------------------


def _mock_embed_texts(texts, task_type="RETRIEVAL_DOCUMENT"):
    """Return fake 768-dim embeddings for each text."""
    return [[0.1] * 768 for _ in texts]


def _patch_llm_and_embeddings():
    """Context manager that patches both LLM and embedding calls."""
    summary_response = _mock_litellm_response("A contextual summary.")
    questions_response = _mock_litellm_response("Q1?\nQ2?\nQ3?\nQ4?\nQ5?")

    async def mock_acompletion(*args, **kwargs):
        # Distinguish summary vs questions by max_tokens
        max_tokens = kwargs.get("max_tokens", 2000)
        if max_tokens <= 1000:
            return summary_response
        return questions_response

    return (
        patch("litellm.acompletion", side_effect=mock_acompletion),
        patch("scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts),
    )


@pytest.mark.asyncio
class TestBuildIndex:
    async def test_creates_all_tables(self, docs_dir: Path, output_db: Path):
        """Build index and verify all expected tables exist."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        for expected in [
            "chunks",
            "chunk_embeddings",
            "hype_questions",
            "hype_embeddings",
            "file_checksums",
        ]:
            assert expected in tables, f"Missing table: {expected}"

    async def test_chunks_inserted_correctly(self, docs_dir: Path, output_db: Path):
        """Verify chunks are inserted with correct data from the markdown file."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        chunks = conn.execute(
            "SELECT chunk_id, doc_filename, doc_title, section_title, body, contextual_summary FROM chunks"
        ).fetchall()
        conn.close()

        assert len(chunks) == 2  # "Section Alpha" and "Section Beta"
        filenames = {row[1] for row in chunks}
        assert filenames == {"test_metrics.md"}
        titles = {row[3] for row in chunks}
        assert "Section Alpha" in titles
        assert "Section Beta" in titles
        # Contextual summary should be populated
        for row in chunks:
            assert row[5] == "A contextual summary."

    async def test_hype_questions_inserted(self, docs_dir: Path, output_db: Path):
        """Verify HyPE questions are created for each chunk."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        questions = conn.execute(
            "SELECT question_id, chunk_id, question FROM hype_questions"
        ).fetchall()
        chunk_ids = conn.execute("SELECT chunk_id FROM chunks").fetchall()
        conn.close()

        # 2 chunks × 5 questions each = 10
        assert len(questions) == 10
        # Every question should reference a valid chunk
        valid_chunk_ids = {row[0] for row in chunk_ids}
        for q in questions:
            assert q[1] in valid_chunk_ids

    async def test_embeddings_stored(self, docs_dir: Path, output_db: Path):
        """Verify embeddings are stored for chunks and HyPE questions."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        chunk_embs = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        hype_embs = conn.execute("SELECT COUNT(*) FROM hype_embeddings").fetchone()[0]
        conn.close()

        assert chunk_embs == 2  # one per chunk
        assert hype_embs == 10  # one per HyPE question

    async def test_fts5_index_populated(self, docs_dir: Path, output_db: Path):
        """Verify FTS5 index can find chunks by keyword search."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        results = conn.execute(
            """
            SELECT c.section_title FROM chunks_fts fts
            JOIN chunks c ON c.rowid = fts.rowid
            WHERE chunks_fts MATCH '"Alpha"'
            """,
        ).fetchall()
        conn.close()

        assert len(results) >= 1
        assert any("Alpha" in row[0] for row in results)

    async def test_file_checksums_stored(self, docs_dir: Path, output_db: Path):
        """Verify file checksums are persisted after build."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        checksums = conn.execute(
            "SELECT filename, checksum FROM file_checksums"
        ).fetchall()
        conn.close()

        assert len(checksums) == 1
        assert checksums[0][0] == "test_metrics.md"
        # Checksum should match the actual file
        expected = compute_file_checksum(docs_dir / "test_metrics.md")
        assert checksums[0][1] == expected

    async def test_incremental_skips_unchanged_files(
        self, docs_dir: Path, output_db: Path
    ):
        """Build twice without changes — second build should skip all files."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        # Count chunks after first build
        conn = sqlite3.connect(str(output_db))
        count1 = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()

        # Build again without changes — should be a no-op
        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            with patch("scripts.build_docs_index.embed_texts") as mock_embed:
                await build_index(docs_dir, output_db, force=False)
                # LLM should NOT be called (no files to process)
                mock_llm.assert_not_called()
                mock_embed.assert_not_called()

        conn = sqlite3.connect(str(output_db))
        count2 = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()

        assert count1 == count2

    async def test_incremental_reprocesses_changed_files(
        self, docs_dir: Path, output_db: Path
    ):
        """Modify a file and rebuild — only the changed file gets reprocessed."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        conn = sqlite3.connect(str(output_db))
        chunks_before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        conn.close()
        assert chunks_before == 2

        # Modify the file (v2 adds a third section)
        (docs_dir / "test_metrics.md").write_text(SAMPLE_MD_CONTENT_V2)

        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        conn = sqlite3.connect(str(output_db))
        chunks_after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        sections = {
            row[0]
            for row in conn.execute("SELECT section_title FROM chunks").fetchall()
        }
        conn.close()

        assert chunks_after == 3  # Alpha + Beta + Gamma
        assert "Section Gamma" in sections

    async def test_old_data_cleaned_on_reindex(self, docs_dir: Path, output_db: Path):
        """When a file is reindexed, old chunks/questions/embeddings are removed."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        # Modify file and rebuild
        (docs_dir / "test_metrics.md").write_text(SAMPLE_MD_CONTENT_V2)

        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        conn = sqlite3.connect(str(output_db))
        new_chunk_ids = {
            row[0] for row in conn.execute("SELECT chunk_id FROM chunks").fetchall()
        }
        new_question_ids = {
            row[0]
            for row in conn.execute("SELECT question_id FROM hype_questions").fetchall()
        }
        # Check no orphan embeddings exist
        emb_chunk_ids = {
            row[0]
            for row in conn.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()
        }
        emb_q_ids = {
            row[0]
            for row in conn.execute(
                "SELECT question_id FROM hype_embeddings"
            ).fetchall()
        }
        conn.close()

        # Old chunks that were replaced should not exist
        # (Alpha changed content → different chunk body → but same section_title → same chunk_id)
        # Gamma is new, so new_chunk_ids should contain it
        assert "Section Gamma" in {
            row[0]
            for row in sqlite3.connect(str(output_db))
            .execute("SELECT section_title FROM chunks")
            .fetchall()
        }
        # Embedding tables should exactly match current chunks/questions
        assert emb_chunk_ids == new_chunk_ids
        assert emb_q_ids == new_question_ids

    async def test_stale_file_checksum_removed(self, docs_dir: Path, output_db: Path):
        """If a file is deleted between builds, its checksum is cleaned up."""
        # Add a second file
        (docs_dir / "extra.md").write_text(
            "---\ntitle: Extra\npurpose: test\nwhen_to_load: always\n---\n\n## Extra Section\n\nExtra content."
        )

        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        checksums = {
            row[0]
            for row in conn.execute("SELECT filename FROM file_checksums").fetchall()
        }
        conn.close()
        assert "extra.md" in checksums

        # Delete the file
        (docs_dir / "extra.md").unlink()

        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        conn = sqlite3.connect(str(output_db))
        checksums_after = {
            row[0]
            for row in conn.execute("SELECT filename FROM file_checksums").fetchall()
        }
        conn.close()

        assert "extra.md" not in checksums_after
        assert "test_metrics.md" in checksums_after

    async def test_build_completes_with_all_llm_failures(
        self, docs_dir: Path, output_db: Path
    ):
        """Build should complete even if every LLM call fails — chunks still get indexed."""
        with patch(
            "litellm.acompletion",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ):
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts
            ):
                await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        summaries = conn.execute("SELECT contextual_summary FROM chunks").fetchall()
        questions = conn.execute("SELECT COUNT(*) FROM hype_questions").fetchone()[0]
        conn.close()

        # Chunks should still be inserted
        assert chunks == 2
        # Summaries should be empty (LLM failed)
        for row in summaries:
            assert row[0] == ""
        # No HyPE questions generated (LLM failed)
        assert questions == 0

    async def test_empty_docs_dir_exits_early(self, tmp_path: Path, output_db: Path):
        """Build with empty/nonexistent docs dir should not crash."""
        empty_dir = tmp_path / "empty_docs"
        empty_dir.mkdir()

        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(empty_dir, output_db, force=True)

        # DB should exist but have no chunks
        conn = sqlite3.connect(str(output_db))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        # Schema tables created, but no data
        assert "chunks" in tables

    async def test_force_reprocesses_unchanged_files(
        self, docs_dir: Path, output_db: Path
    ):
        """With --force, even unchanged files are reprocessed."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        # Rebuild with force — LLM should be called again
        p1, p2 = _patch_llm_and_embeddings()
        with p1 as mock_llm, p2:
            await build_index(docs_dir, output_db, force=True)
            # Should have been called for summaries + questions (2 chunks × 2 = 4 calls)
            assert mock_llm.call_count == 4

    async def test_force_removes_existing_db(self, docs_dir: Path, output_db: Path):
        """With --force, the existing DB file is deleted before rebuilding."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=False)

        assert output_db.exists()
        original_inode = output_db.stat().st_ino

        # Force rebuild — should create a new file (different inode)
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        assert output_db.exists()
        assert output_db.stat().st_ino != original_inode, (
            "DB file was not replaced — force should delete and recreate"
        )


# ---------------------------------------------------------------------------
# Tests: Resume/checkpoint behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBuildIndexResume:
    """Verify that the build pipeline resumes from the last completed phase."""

    async def test_resume_skips_completed_summaries(
        self, docs_dir: Path, output_db: Path
    ):
        """If summaries are already in the DB, re-running should not regenerate them."""
        # First, do a full build
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        # Verify summaries are populated
        conn = sqlite3.connect(str(output_db))
        summaries = conn.execute("SELECT contextual_summary FROM chunks").fetchall()
        assert all(s[0] != "" for s in summaries)

        # Delete checksums so the file looks "changed" and will be reprocessed,
        # but leave chunk data intact to simulate resume after chunking+summaries
        conn.execute("DELETE FROM file_checksums")
        # Also clear embeddings so we need to redo those phases
        conn.execute("DELETE FROM chunk_embeddings")
        conn.execute("DELETE FROM hype_embeddings")
        conn.commit()
        conn.close()

        # Re-run — summaries should NOT be regenerated (they're already in DB)
        # but embeddings should be (they were deleted)
        summary_call_count = 0

        async def tracking_acompletion(*args, **kwargs):
            nonlocal summary_call_count
            max_tokens = kwargs.get("max_tokens", 2000)
            if max_tokens <= 1000:
                summary_call_count += 1
            return _mock_litellm_response(
                "A contextual summary."
                if max_tokens <= 1000
                else "Q1?\nQ2?\nQ3?\nQ4?\nQ5?"
            )

        with patch("litellm.acompletion", side_effect=tracking_acompletion):
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts
            ):
                await build_index(docs_dir, output_db, force=False)

        # Summaries already existed, so no summary LLM calls should have been made
        assert summary_call_count == 0, (
            f"Expected 0 summary calls on resume, got {summary_call_count}"
        )

    async def test_resume_skips_completed_hype_questions(
        self, docs_dir: Path, output_db: Path
    ):
        """If HyPE questions already exist, re-running should not regenerate them."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        q_count = conn.execute("SELECT COUNT(*) FROM hype_questions").fetchone()[0]
        assert q_count == 10  # 2 chunks × 5 questions

        # Clear checksums + embeddings, but keep chunks/summaries/questions
        conn.execute("DELETE FROM file_checksums")
        conn.execute("DELETE FROM chunk_embeddings")
        conn.execute("DELETE FROM hype_embeddings")
        conn.commit()
        conn.close()

        hype_call_count = 0

        async def tracking_acompletion(*args, **kwargs):
            nonlocal hype_call_count
            max_tokens = kwargs.get("max_tokens", 2000)
            if max_tokens > 1000:
                hype_call_count += 1
            return _mock_litellm_response(
                "A contextual summary."
                if max_tokens <= 1000
                else "Q1?\nQ2?\nQ3?\nQ4?\nQ5?"
            )

        with patch("litellm.acompletion", side_effect=tracking_acompletion):
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts
            ):
                await build_index(docs_dir, output_db, force=False)

        assert hype_call_count == 0, (
            f"Expected 0 HyPE calls on resume, got {hype_call_count}"
        )

    async def test_resume_skips_completed_chunk_embeddings(
        self, docs_dir: Path, output_db: Path
    ):
        """If chunk embeddings exist, re-running should not re-embed them."""
        p1, p2 = _patch_llm_and_embeddings()
        with p1, p2:
            await build_index(docs_dir, output_db, force=True)

        conn = sqlite3.connect(str(output_db))
        emb_count = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        assert emb_count == 2

        # Clear checksums + HyPE embeddings only — chunk embeddings stay
        conn.execute("DELETE FROM file_checksums")
        conn.execute("DELETE FROM hype_embeddings")
        conn.commit()
        conn.close()

        embed_calls: list[tuple[list[str], str]] = []
        original_mock = _mock_embed_texts

        def tracking_embed(texts, task_type="RETRIEVAL_DOCUMENT"):
            embed_calls.append((texts, task_type))
            return original_mock(texts, task_type)

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_litellm_response("dummy")
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=tracking_embed
            ):
                await build_index(docs_dir, output_db, force=False)

        # Only RETRIEVAL_QUERY calls should happen (HyPE embeddings were cleared)
        doc_embed_calls = [c for c in embed_calls if c[1] == "RETRIEVAL_DOCUMENT"]
        assert len(doc_embed_calls) == 0, (
            f"Expected 0 RETRIEVAL_DOCUMENT embed calls, got {len(doc_embed_calls)}"
        )

    async def test_resume_after_embedding_failure(
        self, docs_dir: Path, output_db: Path
    ):
        """Simulate a crash during embedding — resume should only embed what's missing."""
        # Build but make chunk embedding fail partway
        call_count = 0

        def failing_embed(texts, task_type="RETRIEVAL_DOCUMENT"):
            nonlocal call_count
            call_count += 1
            if task_type == "RETRIEVAL_DOCUMENT":
                raise RuntimeError("Vertex AI quota exceeded")
            return _mock_embed_texts(texts, task_type)

        p1, _ = _patch_llm_and_embeddings()
        with p1:
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=failing_embed
            ):
                with pytest.raises(RuntimeError, match="Vertex AI quota exceeded"):
                    await build_index(docs_dir, output_db, force=True)

        # Verify chunks and summaries were persisted despite embedding failure
        conn = sqlite3.connect(str(output_db))
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        summaries = conn.execute("SELECT contextual_summary FROM chunks").fetchall()
        q_count = conn.execute("SELECT COUNT(*) FROM hype_questions").fetchone()[0]
        emb_count = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        conn.close()

        assert chunk_count == 2, "Chunks should have been persisted"
        assert all(s[0] != "" for s in summaries), (
            "Summaries should have been persisted"
        )
        assert q_count == 10, "HyPE questions should have been persisted"
        assert emb_count == 0, "No chunk embeddings should exist (embedding failed)"

        # Now resume — should skip summaries+questions, only do embeddings
        summary_calls = 0
        hype_calls = 0

        async def tracking_acompletion(*args, **kwargs):
            nonlocal summary_calls, hype_calls
            max_tokens = kwargs.get("max_tokens", 2000)
            if max_tokens <= 1000:
                summary_calls += 1
            else:
                hype_calls += 1
            return _mock_litellm_response(
                "A contextual summary."
                if max_tokens <= 1000
                else "Q1?\nQ2?\nQ3?\nQ4?\nQ5?"
            )

        # Clear the file checksum so it re-runs (simulating incomplete build)
        conn = sqlite3.connect(str(output_db))
        conn.execute("DELETE FROM file_checksums")
        conn.commit()
        conn.close()

        with patch("litellm.acompletion", side_effect=tracking_acompletion):
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts
            ):
                await build_index(docs_dir, output_db, force=False)

        assert summary_calls == 0, "Summaries should not be regenerated on resume"
        assert hype_calls == 0, "HyPE questions should not be regenerated on resume"

        # Verify everything is now complete
        conn = sqlite3.connect(str(output_db))
        final_emb = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        final_hype_emb = conn.execute(
            "SELECT COUNT(*) FROM hype_embeddings"
        ).fetchone()[0]
        conn.close()

        assert final_emb == 2, "Chunk embeddings should now exist"
        assert final_hype_emb == 10, "HyPE embeddings should now exist"

    async def test_chunks_persisted_before_llm_phase(
        self, docs_dir: Path, output_db: Path
    ):
        """Chunks should be in the DB even if the summary LLM phase crashes."""

        async def crashing_acompletion(*args, **kwargs):
            raise RuntimeError("LLM service down")

        with patch("litellm.acompletion", side_effect=crashing_acompletion):
            with patch(
                "scripts.build_docs_index.embed_texts", side_effect=_mock_embed_texts
            ):
                await build_index(docs_dir, output_db, force=True)

        # Chunks should still be in the DB (inserted before LLM phase)
        conn = sqlite3.connect(str(output_db))
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        summaries = conn.execute("SELECT contextual_summary FROM chunks").fetchall()
        conn.close()

        assert chunk_count == 2, "Chunks should be persisted before LLM phase"
        # Summaries should be empty since LLM failed
        assert all(s[0] == "" for s in summaries)
