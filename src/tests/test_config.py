"""Tests for application configuration.

Validates that Settings loads from env and has sensible defaults.
Unit tests need no database or external service.
Integration tests verify that API keys are set for the configured providers.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.config import _MODEL_DEFAULTS, create_router_llm, get_settings

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

    def test_settings_load_from_env(self):
        """Settings loads successfully with required env vars present."""
        settings = get_settings()
        assert settings.atlas_db_url.startswith("postgresql")

    def test_settings_defaults(self):
        """Default values for agent configuration are sensible."""
        settings = get_settings()
        assert settings.max_results_per_query > 0
        assert settings.frontier_model  # non-empty string


class TestAgentMode:
    """AgentMode enum has the three required modes with correct values."""

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

    def test_agent_mode_has_exactly_four_members(self):
        from src.config import AgentMode

        assert len(AgentMode) == 4

    def test_settings_agent_mode_default(self):
        """Settings should have an agent_mode field defaulting to 'auto'."""
        from src.config import AgentMode

        settings = get_settings()
        assert settings.agent_mode == AgentMode.AUTO


class TestModelRenaming:
    """frontier_model / lightweight_model replace old query_model / metadata_model."""

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

    def test_frontier_prompt_returns_frontier_model(self):
        """A prompt assigned to 'frontier' should call create_router_llm('frontier')."""
        from src.config import get_prompt_model

        with patch("src.config.create_router_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            result = get_prompt_model("sql_generation")

            assert result == "mock-llm"
            mock_create.assert_called_once_with("frontier")

    def test_lightweight_prompt_returns_lightweight_model(self):
        """A prompt assigned to 'lightweight' should call create_router_llm('lightweight')."""
        from src.config import get_prompt_model

        with patch("src.config.create_router_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            result = get_prompt_model("product_extraction")

            assert result == "mock-llm"
            mock_create.assert_called_once_with("lightweight")

    def test_unknown_prompt_raises_key_error(self):
        """Requesting an unregistered prompt key should raise KeyError."""
        from src.config import get_prompt_model

        with pytest.raises(KeyError):
            get_prompt_model("nonexistent_prompt_key")

    def test_all_assigned_prompts_resolve(self):
        """Every key in PROMPT_MODEL_ASSIGNMENTS should resolve without error."""
        from src.config import get_prompt_model

        settings = get_settings()
        with patch("src.config.create_router_llm") as mock_create:
            mock_create.return_value = "mock-llm"
            for key in settings.prompt_model_assignments:
                result = get_prompt_model(key)
                assert result == "mock-llm"


# ---------------------------------------------------------------------------
# Router LLM tests
# ---------------------------------------------------------------------------


class TestCreateRouterLlm:
    """Tests for create_router_llm() multi-provider load balancing."""

    @staticmethod
    def _mock_settings(**overrides):
        """Build a mock Settings with controlled API key values."""
        defaults = {
            "atlas_db_url": "postgresql://x",
            "openai_api_key": None,
            "anthropic_api_key": None,
            "google_api_key": None,
            "frontier_fallback_models": list(
                _MODEL_DEFAULTS["frontier_fallback_models"]
            ),
            "lightweight_fallback_models": list(
                _MODEL_DEFAULTS["lightweight_fallback_models"]
            ),
            "litellm_routing_strategy": "latency-based-routing",
            "litellm_cooldown_time": 60,
            "litellm_allowed_fails": 2,
            "litellm_num_retries": 2,
        }
        defaults.update(overrides)
        mock = MagicMock()
        for k, v in defaults.items():
            setattr(mock, k, v)
        return mock

    def test_filters_by_available_keys_openai_only(self):
        """Only OpenAI key set → Router model_list has only OpenAI entry."""
        mock_settings = self._mock_settings(openai_api_key="sk-test")
        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("litellm.Router") as mock_router_cls,
            patch("langchain_litellm.ChatLiteLLMRouter") as mock_chat,
        ):
            mock_chat.return_value = "mock-router-llm"
            result = create_router_llm("frontier")

            call_kwargs = mock_router_cls.call_args[1]
            model_list = call_kwargs["model_list"]
            assert len(model_list) == 1
            assert "openai/" in model_list[0]["litellm_params"]["model"]
            assert result == "mock-router-llm"

    def test_no_keys_raises(self):
        """No API keys → clear ValueError."""
        mock_settings = self._mock_settings()
        with patch("src.config.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="No API keys configured"):
                create_router_llm("frontier")

    def test_all_keys_includes_all_models(self):
        """All 3 API keys set → all 3 models in Router list."""
        mock_settings = self._mock_settings(
            openai_api_key="sk-test",
            anthropic_api_key="sk-ant-test",
            google_api_key="goog-test",
        )
        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("litellm.Router") as mock_router_cls,
            patch("langchain_litellm.ChatLiteLLMRouter") as mock_chat,
        ):
            mock_chat.return_value = "mock-router-llm"
            create_router_llm("frontier")

            call_kwargs = mock_router_cls.call_args[1]
            model_list = call_kwargs["model_list"]
            assert len(model_list) == 3
            providers = {e["litellm_params"]["model"].split("/")[0] for e in model_list}
            assert providers == {"openai", "anthropic", "gemini"}

    def test_returns_base_chat_model(self):
        """Return type should be a BaseChatModel (or mock standing in for one)."""
        mock_settings = self._mock_settings(openai_api_key="sk-test")
        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("litellm.Router"),
            patch("langchain_litellm.ChatLiteLLMRouter") as mock_chat,
        ):
            sentinel = MagicMock()
            mock_chat.return_value = sentinel
            result = create_router_llm("lightweight", temperature=0)
            assert result is sentinel
            # Verify kwargs forwarded
            assert mock_chat.call_args[1]["temperature"] == 0

    def test_invalid_tier_raises(self):
        """Unknown tier string → ValueError."""
        with pytest.raises(ValueError, match="Unknown tier"):
            create_router_llm("nonexistent")

    def test_create_llm_still_works(self):
        """Old create_llm factory should still work (regression guard)."""
        from src.config import create_llm

        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = "direct-llm"
            result = create_llm("gpt-5-mini", "openai", temperature=0)
            assert result == "direct-llm"


# ---------------------------------------------------------------------------
# Integration tests — require real API keys
# ---------------------------------------------------------------------------

_PROVIDER_TO_KEY_FIELD = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google-genai": "google_api_key",
}


@pytest.mark.integration
class TestApiKeysForConfiguredProviders:
    """Verify that API keys are set for every provider referenced in model_config."""

    def test_frontier_provider_key_is_set(self):
        """API key for the frontier model's provider must be present."""
        settings = get_settings()
        field = _PROVIDER_TO_KEY_FIELD[settings.frontier_model_provider]
        value = getattr(settings, field)
        assert value, (
            f"Frontier provider {settings.frontier_model_provider!r} requires "
            f"{field} to be set"
        )

    def test_lightweight_provider_key_is_set(self):
        """API key for the lightweight model's provider must be present."""
        settings = get_settings()
        field = _PROVIDER_TO_KEY_FIELD[settings.lightweight_model_provider]
        value = getattr(settings, field)
        assert value, (
            f"Lightweight provider {settings.lightweight_model_provider!r} requires "
            f"{field} to be set"
        )
