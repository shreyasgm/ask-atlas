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

    Uses the google-genai SDK (replaces deprecated vertexai SDK).

    Args:
        texts: List of text strings to embed.
        task_type: Vertex AI task type (RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY).

    Returns:
        List of embedding vectors.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True)
    config = types.EmbedContentConfig(task_type=task_type)
    # Vertex AI text-embedding-005 has a 20,000 token per-request limit.
    # With doc chunks averaging ~400 tokens, batch size of 20 stays safely under.
    all_embeddings: list[list[float]] = []
    batch_size = 20
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.models.embed_content(
            model="text-embedding-005",
            contents=batch,
            config=config,
        )
        all_embeddings.extend([e.values for e in response.embeddings])
        logger.info("Embedded batch %d-%d of %d", i, i + len(batch), len(texts))
    return all_embeddings


# ---------------------------------------------------------------------------
# Async LLM calls for contextual retrieval + HyPE
# ---------------------------------------------------------------------------


async def generate_contextual_summary(
    chunk_body: str, doc_title: str, sem: asyncio.Semaphore
) -> str:
    """Generate a contextual summary for a chunk using a lightweight LLM.

    Retries once on failure before falling back to an empty string.

    Args:
        chunk_body: The chunk text.
        doc_title: The parent document title.
        sem: Concurrency-limiting semaphore.

    Returns:
        A brief contextual summary string.
    """
    import litellm

    messages = [
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
    ]

    for attempt in range(2):
        try:
            async with sem:
                response = await litellm.acompletion(
                    model="openai/gpt-5-mini",
                    messages=messages,
                    max_tokens=1000,
                )
            return response.choices[0].message.content.strip()
        except Exception:
            if attempt == 0:
                logger.warning("Contextual summary generation failed, retrying...")
                await asyncio.sleep(1)
            else:
                logger.warning(
                    "Contextual summary generation failed after retry", exc_info=True
                )
    return ""


async def generate_hype_questions(
    chunk_body: str, doc_title: str, section_title: str, sem: asyncio.Semaphore
) -> list[str]:
    """Generate hypothetical questions that this chunk would answer.

    Retries once on failure before falling back to an empty list.

    Args:
        chunk_body: The chunk text.
        doc_title: The parent document title.
        section_title: The section title.
        sem: Concurrency-limiting semaphore.

    Returns:
        List of 5 hypothetical questions.
    """
    import litellm

    messages = [
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
    ]

    for attempt in range(2):
        try:
            async with sem:
                response = await litellm.acompletion(
                    model="openai/gpt-5-mini",
                    messages=messages,
                    max_tokens=2000,
                )
            questions = [
                q.strip()
                for q in response.choices[0].message.content.strip().split("\n")
                if q.strip()
            ]
            return questions[:5]
        except Exception:
            if attempt == 0:
                logger.warning("HyPE question generation failed, retrying...")
                await asyncio.sleep(1)
            else:
                logger.warning(
                    "HyPE question generation failed after retry", exc_info=True
                )
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
    # With --force, remove the existing DB for a clean rebuild
    if force and output_path.exists():
        logger.info("Force mode: removing existing index at %s", output_path)
        output_path.unlink()
        # Also remove WAL/SHM files if present
        for suffix in ("-wal", "-shm"):
            wal_path = output_path.parent / (output_path.name + suffix)
            if wal_path.exists():
                wal_path.unlink()

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

    # -----------------------------------------------------------------------
    # Phase 1: Chunking — insert chunks into DB immediately (empty summary)
    # -----------------------------------------------------------------------
    all_chunk_ids: list[str] = []
    for entry in files_to_process:
        # Chunk the document in memory first
        chunks = chunk_markdown_by_headers(
            entry.content, entry.filename, entry.title, entry.keywords
        )
        new_chunk_ids = {c["chunk_id"] for c in chunks}

        # Check what already exists in the DB for this file
        old_chunk_ids = {
            row[0]
            for row in conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_filename = ?",
                (entry.filename,),
            ).fetchall()
        }

        if new_chunk_ids == old_chunk_ids:
            # Content unchanged (resume case) — keep existing data, skip cleanup
            logger.info(
                "  %s: %d chunks already in DB (resuming)", entry.filename, len(chunks)
            )
            all_chunk_ids.extend(new_chunk_ids)
            continue

        # Content changed — remove old data and insert new chunks
        if old_chunk_ids:
            old_list = list(old_chunk_ids)
            placeholders = ",".join("?" * len(old_list))
            conn.execute(
                f"DELETE FROM hype_embeddings WHERE question_id IN "
                f"(SELECT question_id FROM hype_questions WHERE chunk_id IN ({placeholders}))",
                old_list,
            )
            conn.execute(
                f"DELETE FROM hype_questions WHERE chunk_id IN ({placeholders})",
                old_list,
            )
            conn.execute(
                f"DELETE FROM chunk_embeddings WHERE chunk_id IN ({placeholders})",
                old_list,
            )
            conn.execute(
                f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})",
                old_list,
            )

        logger.info("  %s: %d chunks", entry.filename, len(chunks))
        for chunk in chunks:
            conn.execute(
                "INSERT OR REPLACE INTO chunks "
                "(chunk_id, doc_filename, doc_title, section_title, body, contextual_summary) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    chunk["chunk_id"],
                    chunk["doc_filename"],
                    chunk["doc_title"],
                    chunk["section_title"],
                    chunk["body"],
                    "",
                ),
            )
            all_chunk_ids.append(chunk["chunk_id"])

    conn.commit()
    logger.info("Phase 1 complete: %d chunks to process.", len(all_chunk_ids))

    # -----------------------------------------------------------------------
    # Phase 2: Contextual summaries — only for chunks with empty summary
    # -----------------------------------------------------------------------
    sem = asyncio.Semaphore(concurrency)

    chunks_needing_summary = conn.execute(
        "SELECT chunk_id, body, doc_title FROM chunks "
        "WHERE contextual_summary = '' AND chunk_id IN ({})".format(
            ",".join("?" * len(all_chunk_ids))
        ),
        all_chunk_ids,
    ).fetchall()

    if chunks_needing_summary:
        logger.info(
            "Phase 2: Generating contextual summaries for %d chunks (concurrency=%d)...",
            len(chunks_needing_summary),
            concurrency,
        )
        summary_tasks = [
            generate_contextual_summary(row[1], row[2], sem)
            for row in chunks_needing_summary
        ]
        summaries = await asyncio.gather(*summary_tasks)
        for row, summary in zip(chunks_needing_summary, summaries):
            if summary:
                conn.execute(
                    "UPDATE chunks SET contextual_summary = ? WHERE chunk_id = ?",
                    (summary, row[0]),
                )
        conn.commit()
        logger.info("Phase 2 complete: contextual summaries generated.")
    else:
        logger.info("Phase 2: All chunks already have summaries, skipping.")

    # -----------------------------------------------------------------------
    # Phase 3: HyPE questions — only for chunks without questions
    # -----------------------------------------------------------------------
    existing_hype_chunk_ids = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT chunk_id FROM hype_questions"
        ).fetchall()
    }
    chunks_needing_hype = conn.execute(
        "SELECT chunk_id, body, doc_title, section_title FROM chunks "
        "WHERE chunk_id IN ({})".format(",".join("?" * len(all_chunk_ids))),
        all_chunk_ids,
    ).fetchall()
    chunks_needing_hype = [
        row for row in chunks_needing_hype if row[0] not in existing_hype_chunk_ids
    ]

    if chunks_needing_hype:
        logger.info(
            "Phase 3: Generating HyPE questions for %d chunks (concurrency=%d)...",
            len(chunks_needing_hype),
            concurrency,
        )
        hype_tasks = [
            generate_hype_questions(row[1], row[2], row[3], sem)
            for row in chunks_needing_hype
        ]
        hype_results = await asyncio.gather(*hype_tasks)
        non_empty = sum(1 for r in hype_results if r)
        total_qs = sum(len(r) for r in hype_results)
        logger.info(
            "HyPE generation: %d/%d chunks produced questions (%d total)",
            non_empty,
            len(hype_results),
            total_qs,
        )

        for row, questions in zip(chunks_needing_hype, hype_results):
            for q in questions:
                q_id = hashlib.sha256(f"{row[0]}::{q}".encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO hype_questions "
                    "(question_id, chunk_id, question) VALUES (?, ?, ?)",
                    (q_id, row[0], q),
                )
        conn.commit()
        logger.info("Phase 3 complete: HyPE questions inserted.")
    else:
        logger.info("Phase 3: All chunks already have HyPE questions, skipping.")

    # -----------------------------------------------------------------------
    # Phase 4: Chunk embeddings — only for chunks without embeddings
    # -----------------------------------------------------------------------
    existing_chunk_emb_ids = {
        row[0]
        for row in conn.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()
    }
    chunks_needing_emb = conn.execute(
        "SELECT chunk_id, body, contextual_summary FROM chunks "
        "WHERE chunk_id IN ({})".format(",".join("?" * len(all_chunk_ids))),
        all_chunk_ids,
    ).fetchall()
    chunks_needing_emb = [
        row for row in chunks_needing_emb if row[0] not in existing_chunk_emb_ids
    ]

    if chunks_needing_emb:
        logger.info("Phase 4: Embedding %d chunks...", len(chunks_needing_emb))
        chunk_texts = [f"{row[2]} {row[1]}" for row in chunks_needing_emb]
        chunk_embeddings = embed_texts(chunk_texts, task_type="RETRIEVAL_DOCUMENT")
        for row, emb in zip(chunks_needing_emb, chunk_embeddings):
            conn.execute(
                "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                (row[0], _serialize_embedding(emb)),
            )
        conn.commit()
        logger.info("Phase 4 complete: chunk embeddings stored.")
    else:
        logger.info("Phase 4: All chunks already have embeddings, skipping.")

    # -----------------------------------------------------------------------
    # Phase 5: HyPE embeddings — only for questions without embeddings
    # -----------------------------------------------------------------------
    existing_hype_emb_ids = {
        row[0]
        for row in conn.execute("SELECT question_id FROM hype_embeddings").fetchall()
    }
    questions_needing_emb = conn.execute(
        "SELECT question_id, question FROM hype_questions "
        "WHERE chunk_id IN ({})".format(",".join("?" * len(all_chunk_ids))),
        all_chunk_ids,
    ).fetchall()
    questions_needing_emb = [
        row for row in questions_needing_emb if row[0] not in existing_hype_emb_ids
    ]

    if questions_needing_emb:
        logger.info(
            "Phase 5: Embedding %d HyPE questions...", len(questions_needing_emb)
        )
        hype_texts = [row[1] for row in questions_needing_emb]
        hype_embeddings = embed_texts(hype_texts, task_type="RETRIEVAL_QUERY")
        for row, emb in zip(questions_needing_emb, hype_embeddings):
            conn.execute(
                "INSERT OR REPLACE INTO hype_embeddings (question_id, embedding) VALUES (?, ?)",
                (row[0], _serialize_embedding(emb)),
            )
        conn.commit()
        logger.info("Phase 5 complete: HyPE embeddings stored.")
    else:
        logger.info("Phase 5: All HyPE questions already have embeddings, skipping.")

    # Update file checksums (only after all phases complete)
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


async def fill_gaps(
    output_path: Path,
    concurrency: int = LLM_CONCURRENCY,
) -> None:
    """Fill gaps in an existing index: missing summaries, questions, or embeddings.

    Scans all chunks in the DB and runs only the phases needed to complete them.
    Does not re-chunk or modify file checksums.

    Args:
        output_path: Path to the existing SQLite database.
        concurrency: Max concurrent LLM calls.
    """
    if not output_path.exists():
        logger.error("No index found at %s — run a full build first.", output_path)
        return

    conn = sqlite3.connect(str(output_path))
    all_chunk_ids = [
        row[0] for row in conn.execute("SELECT chunk_id FROM chunks").fetchall()
    ]
    if not all_chunk_ids:
        logger.info("No chunks in index, nothing to fill.")
        conn.close()
        return

    logger.info(
        "fill-gaps: scanning %d chunks for incomplete data...", len(all_chunk_ids)
    )
    sem = asyncio.Semaphore(concurrency)

    # Phase 2: summaries
    chunks_needing_summary = conn.execute(
        "SELECT chunk_id, body, doc_title FROM chunks WHERE contextual_summary = ''"
    ).fetchall()
    if chunks_needing_summary:
        logger.info(
            "Generating summaries for %d chunks...", len(chunks_needing_summary)
        )
        tasks = [
            generate_contextual_summary(row[1], row[2], sem)
            for row in chunks_needing_summary
        ]
        summaries = await asyncio.gather(*tasks)
        for row, summary in zip(chunks_needing_summary, summaries):
            if summary:
                conn.execute(
                    "UPDATE chunks SET contextual_summary = ? WHERE chunk_id = ?",
                    (summary, row[0]),
                )
        conn.commit()
        filled = sum(1 for s in summaries if s)
        logger.info(
            "Summaries: filled %d/%d gaps.", filled, len(chunks_needing_summary)
        )
    else:
        logger.info("All chunks have summaries.")

    # Phase 3: HyPE questions
    existing_hype_chunk_ids = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT chunk_id FROM hype_questions"
        ).fetchall()
    }
    chunks_needing_hype = [
        cid for cid in all_chunk_ids if cid not in existing_hype_chunk_ids
    ]
    if chunks_needing_hype:
        placeholders = ",".join("?" * len(chunks_needing_hype))
        rows = conn.execute(
            f"SELECT chunk_id, body, doc_title, section_title FROM chunks "
            f"WHERE chunk_id IN ({placeholders})",
            chunks_needing_hype,
        ).fetchall()
        logger.info("Generating HyPE questions for %d chunks...", len(rows))
        tasks = [generate_hype_questions(row[1], row[2], row[3], sem) for row in rows]
        results = await asyncio.gather(*tasks)
        for row, questions in zip(rows, results):
            for q in questions:
                q_id = hashlib.sha256(f"{row[0]}::{q}".encode()).hexdigest()[:16]
                conn.execute(
                    "INSERT OR REPLACE INTO hype_questions "
                    "(question_id, chunk_id, question) VALUES (?, ?, ?)",
                    (q_id, row[0], q),
                )
        conn.commit()
        logger.info("HyPE questions: filled gaps for %d chunks.", len(rows))
    else:
        logger.info("All chunks have HyPE questions.")

    # Phase 4: chunk embeddings
    existing_emb_ids = {
        row[0]
        for row in conn.execute("SELECT chunk_id FROM chunk_embeddings").fetchall()
    }
    chunks_needing_emb = [cid for cid in all_chunk_ids if cid not in existing_emb_ids]
    if chunks_needing_emb:
        placeholders = ",".join("?" * len(chunks_needing_emb))
        rows = conn.execute(
            f"SELECT chunk_id, body, contextual_summary FROM chunks "
            f"WHERE chunk_id IN ({placeholders})",
            chunks_needing_emb,
        ).fetchall()
        logger.info("Embedding %d chunks...", len(rows))
        texts = [f"{row[2]} {row[1]}" for row in rows]
        embeddings = embed_texts(texts, task_type="RETRIEVAL_DOCUMENT")
        for row, emb in zip(rows, embeddings):
            conn.execute(
                "INSERT OR REPLACE INTO chunk_embeddings (chunk_id, embedding) VALUES (?, ?)",
                (row[0], _serialize_embedding(emb)),
            )
        conn.commit()
        logger.info("Chunk embeddings: filled %d gaps.", len(rows))
    else:
        logger.info("All chunks have embeddings.")

    # Phase 5: HyPE embeddings
    existing_hype_emb_ids = {
        row[0]
        for row in conn.execute("SELECT question_id FROM hype_embeddings").fetchall()
    }
    all_questions = conn.execute(
        "SELECT question_id, question FROM hype_questions"
    ).fetchall()
    questions_needing_emb = [
        row for row in all_questions if row[0] not in existing_hype_emb_ids
    ]
    if questions_needing_emb:
        logger.info("Embedding %d HyPE questions...", len(questions_needing_emb))
        texts = [row[1] for row in questions_needing_emb]
        embeddings = embed_texts(texts, task_type="RETRIEVAL_QUERY")
        for row, emb in zip(questions_needing_emb, embeddings):
            conn.execute(
                "INSERT OR REPLACE INTO hype_embeddings (question_id, embedding) VALUES (?, ?)",
                (row[0], _serialize_embedding(emb)),
            )
        conn.commit()
        logger.info("HyPE embeddings: filled %d gaps.", len(questions_needing_emb))
    else:
        logger.info("All HyPE questions have embeddings.")

    # Rebuild sqlite-vec indexes
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
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
        logger.info("sqlite-vec indexes repopulated.")
    except Exception:
        logger.warning("sqlite-vec not available", exc_info=True)

    conn.close()
    logger.info("fill-gaps complete.")


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
        "--fill-gaps",
        action="store_true",
        help="Fill missing summaries, questions, and embeddings in an existing index",
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

    if args.fill_gaps:
        asyncio.run(fill_gaps(args.output, args.concurrency))
    else:
        asyncio.run(
            build_index(args.docs_dir, args.output, args.force, args.concurrency)
        )


if __name__ == "__main__":
    main()
