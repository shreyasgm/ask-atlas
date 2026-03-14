#!/usr/bin/env python3
"""Build the documentation search index for hybrid retrieval.

Reads markdown docs from ``src/docs/``, chunks by section headers, generates
contextual summaries and HyPE questions via LLM, embeds via Vertex AI
``text-embedding-005``, and persists to a SQLite database with FTS5 + sqlite-vec.

Usage::

    uv run python scripts/build_docs_index.py
    uv run python scripts/build_docs_index.py --docs-dir src/docs --output src/docs_index.db
    uv run python scripts/build_docs_index.py --force  # rebuild all, ignore checksums

The output SQLite file is designed to be committed to the repo or built in CI
and copied into the Docker image.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sqlite3
import sys
from pathlib import Path

# Add project root to sys.path so we can import src modules
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.docs_pipeline import DocEntry, load_docs_manifest  # noqa: E402
from src.docs_retrieval import (  # noqa: E402
    EMBEDDING_DIM,
    _serialize_embedding,
    chunk_markdown_by_headers,
)

logger = logging.getLogger(__name__)

# Max concurrent LLM calls (avoid rate limits while still being fast)
LLM_CONCURRENCY = 20

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_filename TEXT NOT NULL,
    doc_title TEXT NOT NULL,
    section_title TEXT NOT NULL,
    body TEXT NOT NULL,
    contextual_summary TEXT DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body,
    section_title,
    doc_title,
    content='chunks',
    content_rowid='rowid'
);

-- Triggers to keep FTS5 in sync with chunks table
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, body, section_title, doc_title)
    VALUES (new.rowid, new.body, new.section_title, new.doc_title);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, section_title, doc_title)
    VALUES ('delete', old.rowid, old.body, old.section_title, old.doc_title);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, section_title, doc_title)
    VALUES ('delete', old.rowid, old.body, old.section_title, old.doc_title);
    INSERT INTO chunks_fts(rowid, body, section_title, doc_title)
    VALUES (new.rowid, new.body, new.section_title, new.doc_title);
END;

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS hype_questions (
    question_id TEXT PRIMARY KEY,
    chunk_id TEXT NOT NULL,
    question TEXT NOT NULL,
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS hype_embeddings (
    question_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    FOREIGN KEY (question_id) REFERENCES hype_questions(question_id)
);

CREATE TABLE IF NOT EXISTS file_checksums (
    filename TEXT PRIMARY KEY,
    checksum TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Embedding via Vertex AI
# ---------------------------------------------------------------------------


def embed_texts(
    texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT"
) -> list[list[float]]:
    """Embed a batch of texts via Vertex AI text-embedding-005.

    Args:
        texts: List of text strings to embed.
        task_type: Vertex AI task type (RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY).

    Returns:
        List of embedding vectors.
    """
    from vertexai.language_models import TextEmbeddingInput, TextEmbeddingModel

    model = TextEmbeddingModel.from_pretrained("text-embedding-005")
    # Vertex AI allows up to 250 texts per batch
    all_embeddings: list[list[float]] = []
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = [TextEmbeddingInput(t, task_type=task_type) for t in batch]
        results = model.get_embeddings(inputs)
        all_embeddings.extend([r.values for r in results])
        logger.info("Embedded batch %d-%d of %d", i, i + len(batch), len(texts))
    return all_embeddings


# ---------------------------------------------------------------------------
# Async LLM calls for contextual retrieval + HyPE
# ---------------------------------------------------------------------------


async def generate_contextual_summary(
    chunk_body: str, doc_title: str, sem: asyncio.Semaphore
) -> str:
    """Generate a contextual summary for a chunk using a lightweight LLM.

    Args:
        chunk_body: The chunk text.
        doc_title: The parent document title.
        sem: Concurrency-limiting semaphore.

    Returns:
        A brief contextual summary string.
    """
    try:
        import litellm

        async with sem:
            response = await litellm.acompletion(
                model="openai/gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a technical writer. Given a documentation chunk from "
                            f"the document '{doc_title}' about the Atlas of Economic Complexity, "
                            "write a 1-2 sentence contextual summary that situates this chunk "
                            "within the broader document. Focus on what this chunk covers and "
                            "why someone might search for it."
                        ),
                    },
                    {"role": "user", "content": chunk_body[:3000]},
                ],
                max_tokens=150,
            )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.warning("Contextual summary generation failed", exc_info=True)
        return ""


async def generate_hype_questions(
    chunk_body: str, doc_title: str, section_title: str, sem: asyncio.Semaphore
) -> list[str]:
    """Generate hypothetical questions that this chunk would answer.

    Args:
        chunk_body: The chunk text.
        doc_title: The parent document title.
        section_title: The section title.
        sem: Concurrency-limiting semaphore.

    Returns:
        List of 5 hypothetical questions.
    """
    try:
        import litellm

        async with sem:
            response = await litellm.acompletion(
                model="openai/gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You generate search queries. Given a documentation chunk from "
                            f"'{doc_title}' (section: '{section_title}'), generate exactly 5 "
                            "diverse questions that a user might ask that this chunk would answer. "
                            "Output one question per line, no numbering or bullets."
                        ),
                    },
                    {"role": "user", "content": chunk_body[:3000]},
                ],
                max_tokens=300,
            )
        questions = [
            q.strip()
            for q in response.choices[0].message.content.strip().split("\n")
            if q.strip()
        ]
        return questions[:5]
    except Exception:
        logger.warning("HyPE question generation failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


def compute_file_checksum(filepath: Path) -> str:
    """Compute SHA256 checksum of a file."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


async def build_index(
    docs_dir: Path,
    output_path: Path,
    force: bool = False,
    concurrency: int = LLM_CONCURRENCY,
) -> None:
    """Build or incrementally update the documentation search index.

    Args:
        docs_dir: Path to the documentation directory.
        output_path: Path for the output SQLite database.
        force: If True, rebuild all files regardless of checksums.
        concurrency: Max concurrent LLM calls.
    """
    conn = sqlite3.connect(str(output_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)

    # Load manifest
    manifest = load_docs_manifest(docs_dir)
    if not manifest:
        logger.error("No documentation files found in %s", docs_dir)
        conn.close()
        return

    # Determine which files need processing
    existing_checksums: dict[str, str] = {}
    if not force:
        rows = conn.execute("SELECT filename, checksum FROM file_checksums").fetchall()
        existing_checksums = {row[0]: row[1] for row in rows}

    files_to_process: list[DocEntry] = []
    for entry in manifest:
        current_checksum = compute_file_checksum(entry.full_path)
        if force or existing_checksums.get(entry.filename) != current_checksum:
            files_to_process.append(entry)
            logger.info("Will process: %s (changed or new)", entry.filename)
        else:
            logger.info("Skipping unchanged: %s", entry.filename)

    # Remove checksums for files that no longer exist (before early exit)
    current_filenames = {e.filename for e in manifest}
    stored_filenames = {
        row[0] for row in conn.execute("SELECT filename FROM file_checksums").fetchall()
    }
    removed = stored_filenames - current_filenames
    for fn in removed:
        conn.execute("DELETE FROM file_checksums WHERE filename = ?", (fn,))
        logger.info("Removed stale checksum for: %s", fn)
    if removed:
        conn.commit()

    if not files_to_process:
        logger.info("All files up to date, nothing to do.")
        conn.close()
        return

    # Process changed files
    all_chunks: list[dict] = []
    for entry in files_to_process:
        # Remove old data for this file
        old_chunk_ids = [
            row[0]
            for row in conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_filename = ?",
                (entry.filename,),
            ).fetchall()
        ]
        if old_chunk_ids:
            placeholders = ",".join("?" * len(old_chunk_ids))
            conn.execute(
                f"DELETE FROM hype_embeddings WHERE question_id IN "
                f"(SELECT question_id FROM hype_questions WHERE chunk_id IN ({placeholders}))",
                old_chunk_ids,
            )
            conn.execute(
                f"DELETE FROM hype_questions WHERE chunk_id IN ({placeholders})",
                old_chunk_ids,
            )
            conn.execute(
                f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})",
                old_chunk_ids,
            )
            conn.execute(
                f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})",
                old_chunk_ids,
            )

        # Chunk the document
        chunks = chunk_markdown_by_headers(
            entry.content, entry.filename, entry.title, entry.keywords
        )
        logger.info("  %s: %d chunks", entry.filename, len(chunks))
        all_chunks.extend(chunks)

    # --- Async LLM calls (summaries + HyPE questions in parallel) ---
    sem = asyncio.Semaphore(concurrency)

    # Generate contextual summaries concurrently
    logger.info(
        "Generating contextual summaries for %d chunks (concurrency=%d)...",
        len(all_chunks),
        concurrency,
    )
    summary_tasks = [
        generate_contextual_summary(chunk["body"], chunk["doc_title"], sem)
        for chunk in all_chunks
    ]
    summaries = await asyncio.gather(*summary_tasks)
    for chunk, summary in zip(all_chunks, summaries):
        chunk["contextual_summary"] = summary
    logger.info("Contextual summaries complete.")

    # Generate HyPE questions concurrently
    logger.info(
        "Generating HyPE questions for %d chunks (concurrency=%d)...",
        len(all_chunks),
        concurrency,
    )
    hype_tasks = [
        generate_hype_questions(
            chunk["body"], chunk["doc_title"], chunk["section_title"], sem
        )
        for chunk in all_chunks
    ]
    hype_results = await asyncio.gather(*hype_tasks)
    logger.info("HyPE question generation complete.")

    all_hype: list[dict] = []
    for chunk, questions in zip(all_chunks, hype_results):
        for q in questions:
            q_id = hashlib.sha256(f"{chunk['chunk_id']}::{q}".encode()).hexdigest()[:16]
            all_hype.append(
                {
                    "question_id": q_id,
                    "chunk_id": chunk["chunk_id"],
                    "question": q,
                }
            )

    # Insert chunks
    logger.info("Inserting %d chunks...", len(all_chunks))
    for chunk in all_chunks:
        conn.execute(
            "INSERT OR REPLACE INTO chunks (chunk_id, doc_filename, doc_title, section_title, body, contextual_summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                chunk["chunk_id"],
                chunk["doc_filename"],
                chunk["doc_title"],
                chunk["section_title"],
                chunk["body"],
                chunk.get("contextual_summary", ""),
            ),
        )

    # Insert HyPE questions
    logger.info("Inserting %d HyPE questions...", len(all_hype))
    for hq in all_hype:
        conn.execute(
            "INSERT OR REPLACE INTO hype_questions (question_id, chunk_id, question) VALUES (?, ?, ?)",
            (hq["question_id"], hq["chunk_id"], hq["question"]),
        )

    # Embed chunks (body + contextual summary)
    logger.info("Embedding %d chunks...", len(all_chunks))
    chunk_texts = [f"{c.get('contextual_summary', '')} {c['body']}" for c in all_chunks]
    chunk_embeddings = embed_texts(chunk_texts, task_type="RETRIEVAL_DOCUMENT")
    for chunk, emb in zip(all_chunks, chunk_embeddings):
        conn.execute(
            "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
            (chunk["chunk_id"], _serialize_embedding(emb)),
        )

    # Embed HyPE questions
    if all_hype:
        logger.info("Embedding %d HyPE questions...", len(all_hype))
        hype_texts = [hq["question"] for hq in all_hype]
        hype_embeddings = embed_texts(hype_texts, task_type="RETRIEVAL_QUERY")
        for hq, emb in zip(all_hype, hype_embeddings):
            conn.execute(
                "INSERT OR REPLACE INTO hype_embeddings (question_id, embedding) VALUES (?, ?)",
                (hq["question_id"], _serialize_embedding(emb)),
            )

    # Update file checksums
    for entry in files_to_process:
        checksum = compute_file_checksum(entry.full_path)
        conn.execute(
            "INSERT OR REPLACE INTO file_checksums (filename, checksum) VALUES (?, ?)",
            (entry.filename, checksum),
        )

    conn.commit()

    # Create sqlite-vec virtual tables if extension available
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)

        # Check if vec tables already exist
        existing_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "chunk_vec" not in existing_tables:
            conn.execute(
                f"CREATE VIRTUAL TABLE chunk_vec USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[{EMBEDDING_DIM}])"
            )
        if "hype_vec" not in existing_tables:
            conn.execute(
                f"CREATE VIRTUAL TABLE hype_vec USING vec0(question_id TEXT PRIMARY KEY, embedding float[{EMBEDDING_DIM}])"
            )

        # Populate vec tables from blob data
        conn.execute("DELETE FROM chunk_vec")
        conn.execute(
            "INSERT INTO chunk_vec (chunk_id, embedding) "
            "SELECT chunk_id, embedding FROM chunk_embeddings"
        )
        conn.execute("DELETE FROM hype_vec")
        conn.execute(
            "INSERT INTO hype_vec (question_id, embedding) "
            "SELECT question_id, embedding FROM hype_embeddings"
        )
        conn.commit()
        logger.info("sqlite-vec indexes populated successfully")
    except Exception:
        logger.warning(
            "sqlite-vec not available; index will work with BM25 only",
            exc_info=True,
        )

    conn.close()
    logger.info("Index built successfully at %s", output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build documentation search index for hybrid retrieval"
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=PROJECT_ROOT / "src" / "docs",
        help="Path to documentation directory (default: src/docs)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "src" / "docs_index.db",
        help="Output SQLite database path (default: src/docs_index.db)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild all files regardless of checksums",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=LLM_CONCURRENCY,
        help=f"Max concurrent LLM calls (default: {LLM_CONCURRENCY})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    asyncio.run(build_index(args.docs_dir, args.output, args.force, args.concurrency))


if __name__ == "__main__":
    main()
