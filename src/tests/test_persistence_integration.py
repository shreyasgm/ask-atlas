"""Integration tests for checkpoint persistence.

Tests real PostgresSaver connectivity and MemorySaver fallback behavior.

NOTE: This file was generated with LLM assistance and needs human review.
Fragile areas: fallback test uses a bogus URL which may behave differently
depending on network configuration.
"""

import os

import pytest
from pathlib import Path

from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import MemorySaver

from src.config import get_settings
from src.persistence import CheckpointerManager
from src.text_to_sql import AtlasTextToSQL

BASE_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def checkpoint_db_url():
    """Provide the Docker test DB URL, skip if not set."""
    url = os.environ.get("ATLAS_DB_URL")
    if not url:
        pytest.skip("ATLAS_DB_URL not set")
    return url


@pytest.mark.db
class TestPersistenceIntegration:
    """Validate real checkpointer behavior against Postgres and fallback."""

    def test_checkpointer_with_real_checkpoint_db(self):
        """PostgresSaver is used when CHECKPOINT_DB_URL is configured."""
        settings = get_settings()
        if not settings.checkpoint_db_url:
            pytest.skip("CHECKPOINT_DB_URL not configured")

        manager = CheckpointerManager(db_url=settings.checkpoint_db_url)
        try:
            cp = manager.checkpointer
            assert not isinstance(cp, MemorySaver)
        finally:
            manager.close()

    def test_checkpointer_fallback_with_bad_url(self):
        """Bad URL gracefully falls back to MemorySaver (no crash)."""
        manager = CheckpointerManager(db_url="postgresql://invalid:5432/nope")
        try:
            cp = manager.checkpointer
            assert isinstance(cp, MemorySaver)
        finally:
            manager.close()

    def test_state_survives_manager_restart(self, checkpoint_db_url):
        """Checkpoint metadata persists across CheckpointerManager instances."""
        config = {
            "configurable": {
                "thread_id": "restart-test-thread",
                "checkpoint_ns": "",
            }
        }
        metadata = {"source": "restart-test", "step": 1, "parents": {}}

        # --- Manager A: store a checkpoint ---
        manager_a = CheckpointerManager(db_url=checkpoint_db_url)
        try:
            checkpoint = empty_checkpoint()
            manager_a.checkpointer.put(config, checkpoint, metadata, {})
        finally:
            manager_a.close()

        # --- Manager B: read it back after a full restart ---
        manager_b = CheckpointerManager(db_url=checkpoint_db_url)
        try:
            tup = manager_b.checkpointer.get_tuple(config)
            assert tup is not None, "Checkpoint not found after manager restart"
            assert tup.metadata["source"] == "restart-test"
            assert tup.metadata["step"] == 1
        finally:
            manager_b.close()

