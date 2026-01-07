"""
Application configuration using Pydantic Settings.

Centralizes all configuration loaded from environment variables with type validation.
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices
from functools import lru_cache

# Project root directory (parent of src/)
BASE_DIR = Path(__file__).resolve().parents[1]


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

    # LLM Configuration
    openai_api_key: str = Field(
        ...,
        validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key"),
        description="OpenAI API key",
    )
    query_model: str = Field(
        "gpt-5.2",
        validation_alias=AliasChoices("QUERY_MODEL", "query_model"),
        description="Model for SQL query generation",
    )
    metadata_model: str = Field(
        "gpt-5-mini",
        validation_alias=AliasChoices("METADATA_MODEL", "metadata_model"),
        description="Model for metadata extraction",
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
