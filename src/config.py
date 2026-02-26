"""
Application configuration using Pydantic Settings.

Centralizes all configuration loaded from environment variables with type validation.
Non-secret defaults (model names, providers) live in model_config.py at the project root.
"""

import logging
from enum import Enum
from functools import lru_cache
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Project root directory (parent of src/)
BASE_DIR = Path(__file__).resolve().parents[1]

# Import non-secret defaults from model_config.py
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "model_config", BASE_DIR / "model_config.py"
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
}


class AgentMode(str, Enum):
    """System operating mode controlling which tool pipelines are available."""

    AUTO = "auto"
    GRAPHQL_SQL = "graphql_sql"
    SQL_ONLY = "sql_only"


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

    # Agent mode
    agent_mode: AgentMode = Field(
        _MODEL_DEFAULTS["agent_mode"],
        validation_alias=AliasChoices("AGENT_MODE", "agent_mode"),
        description="Agent operating mode: 'auto', 'graphql_sql', or 'sql_only'",
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


@lru_cache()
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
