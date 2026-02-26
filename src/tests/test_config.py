"""Unit tests for application configuration.

Validates that Settings loads from env and has sensible defaults.
No database or external service required.
"""

import os
from enum import Enum
from unittest.mock import patch

import pytest

from src.config import get_settings

# Minimal env for tests that construct Settings without a real .env file.
_MIN_ENV = {"ATLAS_DB_URL": "postgresql://test:5432/testdb"}


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Ensure get_settings LRU cache is cleared around every test.

    Also ensures ATLAS_DB_URL is set so Settings can be constructed
    even when no .env file is present (e.g. in worktrees).
    """
    get_settings.cache_clear()
    old = os.environ.get("ATLAS_DB_URL")
    if old is None:
        os.environ["ATLAS_DB_URL"] = _MIN_ENV["ATLAS_DB_URL"]
    yield
    get_settings.cache_clear()
    if old is None:
        os.environ.pop("ATLAS_DB_URL", None)


class TestConfigUnit:
    """Validate configuration values (no DB required)."""

    @pytest.mark.integration
    def test_settings_load_from_env(self):
        """Settings loads successfully with required env vars present."""
        settings = get_settings()
        assert settings.atlas_db_url.startswith("postgresql")
        # At least one LLM API key should be configured
        assert (
            settings.openai_api_key
            or settings.anthropic_api_key
            or settings.google_api_key
        )

    def test_settings_defaults(self):
        """Default values for agent configuration are sensible."""
        settings = get_settings()
        assert settings.max_results_per_query > 0
        assert settings.frontier_model  # non-empty string


class TestAgentMode:
    """AgentMode enum has the three required modes with correct values."""

    def test_agent_mode_exists(self):
        from src.config import AgentMode

        assert issubclass(AgentMode, Enum)

    def test_agent_mode_values(self):
        from src.config import AgentMode

        assert AgentMode.SQL_ONLY.value == "sql_only"
        assert AgentMode.GRAPHQL_SQL.value == "graphql_sql"
        assert AgentMode.AUTO.value == "auto"

    def test_agent_mode_is_str_enum(self):
        """AgentMode members should be usable as strings (str, Enum)."""
        from src.config import AgentMode

        # str inheritance means equality comparison with plain strings works
        assert AgentMode.AUTO == "auto"
        assert AgentMode.SQL_ONLY == "sql_only"
        assert AgentMode.GRAPHQL_SQL == "graphql_sql"
        # .value always returns the plain string
        assert AgentMode.SQL_ONLY.value == "sql_only"

    def test_agent_mode_from_string(self):
        """Can construct AgentMode from its string value."""
        from src.config import AgentMode

        assert AgentMode("auto") is AgentMode.AUTO
        assert AgentMode("sql_only") is AgentMode.SQL_ONLY
        assert AgentMode("graphql_sql") is AgentMode.GRAPHQL_SQL

    def test_agent_mode_invalid_raises(self):
        from src.config import AgentMode

        with pytest.raises(ValueError):
            AgentMode("invalid_mode")

    def test_agent_mode_has_exactly_three_members(self):
        from src.config import AgentMode

        assert len(AgentMode) == 3

    def test_settings_agent_mode_default(self):
        """Settings should have an agent_mode field defaulting to 'auto'."""
        from src.config import AgentMode

        settings = get_settings()
        assert settings.agent_mode == AgentMode.AUTO


class TestModelRenaming:
    """frontier_model / lightweight_model replace old query_model / metadata_model."""

    def test_frontier_model_field_exists(self):
        settings = get_settings()
        assert hasattr(settings, "frontier_model")
        assert isinstance(settings.frontier_model, str)
        assert len(settings.frontier_model) > 0

    def test_lightweight_model_field_exists(self):
        settings = get_settings()
        assert hasattr(settings, "lightweight_model")
        assert isinstance(settings.lightweight_model, str)
        assert len(settings.lightweight_model) > 0

    def test_frontier_model_provider_field_exists(self):
        settings = get_settings()
        assert hasattr(settings, "frontier_model_provider")
        assert isinstance(settings.frontier_model_provider, str)

    def test_lightweight_model_provider_field_exists(self):
        settings = get_settings()
        assert hasattr(settings, "lightweight_model_provider")
        assert isinstance(settings.lightweight_model_provider, str)

    def test_backward_compat_query_model_env_var(self):
        """Setting QUERY_MODEL env var should populate frontier_model."""
        get_settings.cache_clear()
        with patch.dict(
            "os.environ",
            {"QUERY_MODEL": "my-custom-frontier", "ATLAS_DB_URL": "postgresql://x"},
        ):
            get_settings.cache_clear()
            settings = get_settings()
            assert settings.frontier_model == "my-custom-frontier"
        get_settings.cache_clear()

    def test_backward_compat_metadata_model_env_var(self):
        """Setting METADATA_MODEL env var should populate lightweight_model."""
        get_settings.cache_clear()
        with patch.dict(
            "os.environ",
            {
                "METADATA_MODEL": "my-custom-lightweight",
                "ATLAS_DB_URL": "postgresql://x",
            },
        ):
            get_settings.cache_clear()
            settings = get_settings()
            assert settings.lightweight_model == "my-custom-lightweight"
        get_settings.cache_clear()

    def test_backward_compat_query_llm_env_var(self):
        """Legacy QUERY_LLM env var should also populate frontier_model."""
        get_settings.cache_clear()
        with patch.dict(
            "os.environ",
            {"QUERY_LLM": "legacy-frontier", "ATLAS_DB_URL": "postgresql://x"},
        ):
            get_settings.cache_clear()
            settings = get_settings()
            assert settings.frontier_model == "legacy-frontier"
        get_settings.cache_clear()

    def test_backward_compat_metadata_llm_env_var(self):
        """Legacy METADATA_LLM env var should also populate lightweight_model."""
        get_settings.cache_clear()
        with patch.dict(
            "os.environ",
            {"METADATA_LLM": "legacy-lightweight", "ATLAS_DB_URL": "postgresql://x"},
        ):
            get_settings.cache_clear()
            settings = get_settings()
            assert settings.lightweight_model == "legacy-lightweight"
        get_settings.cache_clear()

    def test_new_env_vars_take_priority(self):
        """FRONTIER_MODEL should take priority when both old and new are set."""
        get_settings.cache_clear()
        with patch.dict(
            "os.environ",
            {
                "FRONTIER_MODEL": "new-frontier",
                "QUERY_MODEL": "old-query",
                "ATLAS_DB_URL": "postgresql://x",
            },
        ):
            get_settings.cache_clear()
            settings = get_settings()
            assert settings.frontier_model == "new-frontier"
        get_settings.cache_clear()


class TestPromptModelAssignments:
    """PROMPT_MODEL_ASSIGNMENTS maps prompt keys to model tiers."""

    def test_settings_has_prompt_model_assignments(self):
        settings = get_settings()
        assert hasattr(settings, "prompt_model_assignments")
        assert isinstance(settings.prompt_model_assignments, dict)

    def test_existing_prompts_are_assigned(self):
        """All currently existing prompt keys have assignments."""
        settings = get_settings()
        pma = settings.prompt_model_assignments
        # Existing prompts in the codebase
        assert "sql_generation" in pma
        assert "product_extraction" in pma
        assert "product_code_selection" in pma

    def test_new_prompts_are_assigned(self):
        """New prompt keys from the redesign are present."""
        settings = get_settings()
        pma = settings.prompt_model_assignments
        assert "agent_system_prompt" in pma
        assert "graphql_classification" in pma
        assert "graphql_entity_extraction" in pma
        assert "id_resolution_selection" in pma
        assert "document_selection" in pma
        assert "documentation_synthesis" in pma

    def test_assignments_are_valid_tiers(self):
        """Every assignment value must be 'frontier' or 'lightweight'."""
        settings = get_settings()
        for key, tier in settings.prompt_model_assignments.items():
            assert tier in (
                "frontier",
                "lightweight",
            ), f"Prompt {key!r} has invalid tier {tier!r}"

    def test_sql_generation_uses_frontier(self):
        """SQL generation is a complex task — must use frontier model."""
        settings = get_settings()
        assert settings.prompt_model_assignments["sql_generation"] == "frontier"

    def test_product_extraction_uses_lightweight(self):
        """Product extraction is a structured extraction task — lightweight."""
        settings = get_settings()
        assert settings.prompt_model_assignments["product_extraction"] == "lightweight"

    def test_agent_system_prompt_uses_frontier(self):
        """Agent orchestration requires frontier model."""
        settings = get_settings()
        assert settings.prompt_model_assignments["agent_system_prompt"] == "frontier"


class TestGetPromptModel:
    """get_prompt_model() returns the correct LLM for a given prompt key."""

    def test_get_prompt_model_exists(self):
        from src.config import get_prompt_model

        assert callable(get_prompt_model)

    def test_frontier_prompt_returns_frontier_model(self):
        """A prompt assigned to 'frontier' should use the frontier model config."""
        from src.config import get_prompt_model

        with patch("src.config.create_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            result = get_prompt_model("sql_generation")

            assert result == "mock-llm"
            call_args = mock_create.call_args
            settings = get_settings()
            assert call_args[0][0] == settings.frontier_model
            assert call_args[0][1] == settings.frontier_model_provider

    def test_lightweight_prompt_returns_lightweight_model(self):
        """A prompt assigned to 'lightweight' should use the lightweight model config."""
        from src.config import get_prompt_model

        with patch("src.config.create_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            result = get_prompt_model("product_extraction")

            assert result == "mock-llm"
            call_args = mock_create.call_args
            settings = get_settings()
            assert call_args[0][0] == settings.lightweight_model
            assert call_args[0][1] == settings.lightweight_model_provider

    def test_unknown_prompt_raises_key_error(self):
        """Requesting an unregistered prompt key should raise KeyError."""
        from src.config import get_prompt_model

        with pytest.raises(KeyError):
            get_prompt_model("nonexistent_prompt_key")

    def test_all_assigned_prompts_resolve(self):
        """Every key in PROMPT_MODEL_ASSIGNMENTS should resolve without error."""
        from src.config import get_prompt_model

        settings = get_settings()
        with patch("src.config.create_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            for key in settings.prompt_model_assignments:
                result = get_prompt_model(key)
                assert result == "mock-llm"
