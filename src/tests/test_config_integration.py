"""Integration tests for application configuration.

Validates that Settings loads correctly from the environment and that
the configured database URL is actually reachable.

NOTE: This file was generated with LLM assistance and needs human review.
"""

import pytest
from sqlalchemy import create_engine, text

from src.config import get_settings


class TestConfigIntegration:
    """Validate real configuration values (no mocks)."""

    def test_settings_load_from_env(self):
        """Settings loads successfully with required env vars present."""
        settings = get_settings()
        assert settings.atlas_db_url.startswith("postgresql")
        # At least one LLM API key should be configured
        assert settings.openai_api_key or settings.anthropic_api_key or settings.google_api_key

    def test_settings_defaults(self):
        """Default values for agent configuration are sensible."""
        settings = get_settings()
        assert settings.max_results_per_query > 0
        assert settings.query_model  # non-empty string

    @pytest.mark.db
    def test_atlas_db_url_is_connectable(self):
        """The configured atlas_db_url can actually execute a query."""
        settings = get_settings()
        engine = create_engine(
            settings.atlas_db_url,
            connect_args={"connect_timeout": 10},
        )
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.scalar() == 1
        finally:
            engine.dispose()
