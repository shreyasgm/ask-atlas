"""
Application configuration using Pydantic Settings.

Centralizes all configuration loaded from environment variables with type validation.
Non-secret defaults (model names, providers) live in src/model_config.py.
"""

import logging
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared rate limiter — smooths burst patterns to avoid provider throttling.
# A single instance is shared across all LLM calls in the process so that
# concurrent requests from different users are collectively throttled.
#
# NOTE: This is a per-process limiter. With multiple uvicorn workers (separate
# OS processes) and multiple Cloud Run instances, each gets its own independent
# rate limiter. This does NOT protect against hitting the provider's API-key-level
# rate limits (RPM/TPM). Its purpose is to smooth local burst patterns only.
# For actual provider rate limit protection, rely on the provider's 429 responses
# and LangChain's built-in retry logic.
# ---------------------------------------------------------------------------
_rate_limiter = InMemoryRateLimiter(
    requests_per_second=10,
    check_every_n_seconds=0.05,
    max_bucket_size=20,
)

# Project root directory (parent of src/)
BASE_DIR = Path(__file__).resolve().parents[1]

# Import non-secret defaults from model_config.py
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "model_config", BASE_DIR / "src" / "model_config.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_MODEL_DEFAULTS = {
    "frontier_model": getattr(_mod, "FRONTIER_MODEL", "gpt-5.2"),
    "frontier_model_provider": getattr(_mod, "FRONTIER_MODEL_PROVIDER", "openai"),
    "lightweight_model": getattr(_mod, "LIGHTWEIGHT_MODEL", "gpt-5-mini"),
    "lightweight_model_provider": getattr(_mod, "LIGHTWEIGHT_MODEL_PROVIDER", "openai"),
    "agent_mode": getattr(_mod, "AGENT_MODE", "auto"),
    "prompt_model_assignments": getattr(_mod, "PROMPT_MODEL_ASSIGNMENTS", {}),
    "max_docs_per_selection": getattr(_mod, "MAX_DOCS_PER_SELECTION", 2),
}


class AgentMode(StrEnum):
    """System operating mode controlling which tool pipelines are available."""

    AUTO = "auto"
    GRAPHQL_SQL = "graphql_sql"
    SQL_ONLY = "sql_only"
    GRAPHQL_ONLY = "graphql_only"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    atlas_db_url: str = Field(
        ...,
        validation_alias=AliasChoices("ATLAS_DB_URL", "atlas_db_url"),
        description="PostgreSQL connection URI for Atlas database",
    )
    checkpoint_db_url: str | None = Field(
        None,
        validation_alias=AliasChoices("CHECKPOINT_DB_URL", "checkpoint_db_url"),
        description="PostgreSQL connection URI for checkpoint storage",
    )

    # LLM Configuration — API keys
    openai_api_key: str | None = Field(
        None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"),
        description="OpenAI API key",
    )
    anthropic_api_key: str | None = Field(
        None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"),
        description="Anthropic API key",
    )
    google_api_key: str | None = Field(
        None,
        validation_alias=AliasChoices("GOOGLE_API_KEY", "google_api_key"),
        description="Google AI API key",
    )

    # LLM Configuration — models and providers (defaults from model_config.py)
    # Accept old env var names (QUERY_MODEL, QUERY_LLM, METADATA_MODEL, METADATA_LLM)
    # for backward compatibility, with the new names taking priority.
    frontier_model: str = Field(
        _MODEL_DEFAULTS["frontier_model"],
        validation_alias=AliasChoices(
            "FRONTIER_MODEL", "QUERY_MODEL", "QUERY_LLM", "frontier_model"
        ),
        description="Frontier model for complex reasoning (SQL generation, agent orchestration)",
    )
    frontier_model_provider: str = Field(
        _MODEL_DEFAULTS["frontier_model_provider"],
        validation_alias=AliasChoices(
            "FRONTIER_MODEL_PROVIDER",
            "QUERY_MODEL_PROVIDER",
            "frontier_model_provider",
        ),
        description="Provider for the frontier model ('openai', 'anthropic', or 'google-genai')",
    )
    lightweight_model: str = Field(
        _MODEL_DEFAULTS["lightweight_model"],
        validation_alias=AliasChoices(
            "LIGHTWEIGHT_MODEL",
            "METADATA_MODEL",
            "METADATA_LLM",
            "lightweight_model",
        ),
        description="Lightweight model for extraction and classification tasks",
    )
    lightweight_model_provider: str = Field(
        _MODEL_DEFAULTS["lightweight_model_provider"],
        validation_alias=AliasChoices(
            "LIGHTWEIGHT_MODEL_PROVIDER",
            "METADATA_MODEL_PROVIDER",
            "lightweight_model_provider",
        ),
        description="Provider for the lightweight model ('openai', 'anthropic', or 'google-genai')",
    )

    # GraphQL API endpoints (public Atlas APIs — override only in tests or staging)
    graphql_explore_url: str = Field(
        "https://atlas.hks.harvard.edu/api/graphql",
        validation_alias=AliasChoices("GRAPHQL_EXPLORE_URL", "graphql_explore_url"),
        description="Atlas Explore GraphQL API endpoint",
    )
    graphql_country_pages_url: str = Field(
        "https://atlas.hks.harvard.edu/api/countries/graphql",
        validation_alias=AliasChoices(
            "GRAPHQL_COUNTRY_PAGES_URL", "graphql_country_pages_url"
        ),
        description="Atlas Country Pages GraphQL API endpoint",
    )

    # Docs pipeline
    max_docs_per_selection: int = Field(
        _MODEL_DEFAULTS["max_docs_per_selection"],
        validation_alias=AliasChoices(
            "MAX_DOCS_PER_SELECTION", "max_docs_per_selection"
        ),
        description="Maximum documents the docs tool can select per invocation",
    )

    # Agent mode
    agent_mode: AgentMode = Field(
        _MODEL_DEFAULTS["agent_mode"],
        validation_alias=AliasChoices("AGENT_MODE", "agent_mode"),
        description="Agent operating mode: 'auto', 'graphql_sql', 'sql_only', or 'graphql_only'",
    )

    # Per-prompt model assignments
    prompt_model_assignments: dict[str, str] = Field(
        default_factory=lambda: dict(_MODEL_DEFAULTS["prompt_model_assignments"]),
        description="Maps each prompt key to 'frontier' or 'lightweight'",
    )

    # Agent Configuration
    max_queries_per_question: int = Field(
        30,
        validation_alias=AliasChoices("MAX_QUERIES", "max_queries_per_question"),
        description="Maximum number of queries per user question",
    )
    max_results_per_query: int = Field(
        15,
        validation_alias=AliasChoices("MAX_RESULTS", "max_results_per_query"),
        description="Maximum rows returned per SQL query",
    )

    # Logging
    log_level: str = Field(
        "INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "log_level"),
        description="Python log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    log_format: str = Field(
        "text",
        validation_alias=AliasChoices("LOG_FORMAT", "log_format"),
        description="Log output format: 'json' for structured Cloud Run logs, 'text' for local dev",
    )

    # CORS
    cors_origins: str = Field(
        "",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
        description="Comma-separated additional CORS origins (e.g. https://example.com,https://other.com)",
    )

    # Feature Flags
    enable_langsmith: bool = Field(
        True,
        validation_alias=AliasChoices("LANGCHAIN_TRACING_V2", "enable_langsmith"),
        description="Enable LangSmith tracing",
    )
    langsmith_project: str = Field(
        "ask-atlas",
        validation_alias=AliasChoices("LANGCHAIN_PROJECT", "langsmith_project"),
        description="LangSmith project name",
    )
    use_custom_graph: bool = Field(
        True,
        validation_alias=AliasChoices("USE_CUSTOM_GRAPH", "use_custom_graph"),
        description="Use custom LangGraph workflow (rollback flag)",
    )

    model_config = {
        "env_file": BASE_DIR / ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Returns a singleton Settings instance. The settings are loaded once
    and cached for subsequent calls.
    """
    return Settings()


def create_llm(model: str, provider: str, **kwargs) -> BaseChatModel:
    """Create a chat model for the given provider.

    Args:
        model: Model name (e.g. "gpt-5.2", "claude-sonnet-4-5-20250929", "gemini-2.5-flash").
        provider: One of "openai", "anthropic", or "google-genai" / "google".
        **kwargs: Extra keyword arguments forwarded to the model constructor.

    Returns:
        A LangChain chat model instance.

    Raises:
        ValueError: If the provider is not supported.
    """
    kwargs.setdefault("rate_limiter", _rate_limiter)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, **kwargs)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, **kwargs)
    elif provider in ("google-genai", "google"):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, **kwargs)
    else:
        raise ValueError(
            f"Unsupported LLM provider: {provider!r}. "
            "Use 'openai', 'anthropic', or 'google-genai'."
        )


def get_prompt_model(prompt_key: str) -> BaseChatModel:
    """Get the LLM instance for a specific prompt.

    Looks up the model type assignment for the given prompt key
    and returns the corresponding frontier or lightweight model.

    Args:
        prompt_key: The prompt identifier (must exist in prompt_model_assignments).

    Returns:
        A LangChain chat model instance configured for the prompt's tier.

    Raises:
        KeyError: If prompt_key is not found in prompt_model_assignments.
    """
    settings = get_settings()
    assignments = settings.prompt_model_assignments
    if prompt_key not in assignments:
        raise KeyError(
            f"Unknown prompt key: {prompt_key!r}. "
            f"Available keys: {sorted(assignments.keys())}"
        )
    tier = assignments[prompt_key]
    if tier == "frontier":
        return create_llm(settings.frontier_model, settings.frontier_model_provider)
    else:
        return create_llm(
            settings.lightweight_model, settings.lightweight_model_provider
        )
