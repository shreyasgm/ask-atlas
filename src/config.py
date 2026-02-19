"""
Application configuration using Pydantic Settings.

Centralizes all configuration loaded from environment variables with type validation.
Non-secret defaults (model names, providers) live in model_config.py at the project root.
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices
from functools import lru_cache
from langchain_core.language_models import BaseChatModel

# Project root directory (parent of src/)
BASE_DIR = Path(__file__).resolve().parents[1]

# Import non-secret defaults from model_config.py
import importlib.util

_spec = importlib.util.spec_from_file_location("model_config", BASE_DIR / "model_config.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_MODEL_DEFAULTS = {
    "query_model": getattr(_mod, "QUERY_MODEL", "gpt-5.2"),
    "query_model_provider": getattr(_mod, "QUERY_MODEL_PROVIDER", "openai"),
    "metadata_model": getattr(_mod, "METADATA_MODEL", "gpt-5-mini"),
    "metadata_model_provider": getattr(_mod, "METADATA_MODEL_PROVIDER", "openai"),
}


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
    query_model: str = Field(
        _MODEL_DEFAULTS["query_model"],
        validation_alias=AliasChoices("QUERY_MODEL", "QUERY_LLM", "query_model"),
        description="Model for SQL query generation",
    )
    query_model_provider: str = Field(
        _MODEL_DEFAULTS["query_model_provider"],
        validation_alias=AliasChoices("QUERY_MODEL_PROVIDER", "query_model_provider"),
        description="Provider for the query model ('openai', 'anthropic', or 'google-genai')",
    )
    metadata_model: str = Field(
        _MODEL_DEFAULTS["metadata_model"],
        validation_alias=AliasChoices("METADATA_MODEL", "METADATA_LLM", "metadata_model"),
        description="Model for metadata extraction",
    )
    metadata_model_provider: str = Field(
        _MODEL_DEFAULTS["metadata_model_provider"],
        validation_alias=AliasChoices("METADATA_MODEL_PROVIDER", "metadata_model_provider"),
        description="Provider for the metadata model ('openai', 'anthropic', or 'google-genai')",
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
