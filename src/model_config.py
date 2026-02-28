"""
Non-secret LLM configuration.

Edit this file to switch models and providers. Secrets (API keys, DB URLs)
stay in .env which is gitignored.

Environment variables always override these values if set.

Supported providers: "openai", "anthropic", "google-genai"

Model aliases (short names that work in each provider's API):

  OpenAI (provider: "openai")
    Frontier:   gpt-5.2              — flagship reasoning model
    Fast:       gpt-5-mini           — cheaper/faster GPT-5
    Tiny:       gpt-5-nano           — fastest/cheapest GPT-5
    Coding:     gpt-5.3-codex        — best for code (Feb 2026)
    Legacy:     gpt-4.1, gpt-4.1-mini, gpt-4.1-nano

  Anthropic (provider: "anthropic")
    Frontier:   claude-opus-4-6      — most capable (Feb 2026)
    Balanced:   claude-sonnet-4-6    — speed + intelligence (Feb 2026)
    Fast:       claude-haiku-4-5     — fastest, near-frontier
    Legacy:     claude-sonnet-4-5, claude-opus-4-5

  Google Gemini (provider: "google-genai")
    Frontier:   gemini-3-pro-preview   — most intelligent (preview)
    Fast:       gemini-3-flash-preview — pro-level at flash speed (preview)
    Stable:     gemini-2.5-pro         — best stable/GA model
    Balanced:   gemini-2.5-flash       — stable, fast, cheap
    Tiny:       gemini-2.5-flash-lite  — cheapest
"""

from dataclasses import dataclass

# --- Frontier model (complex reasoning, agent orchestration, SQL generation) ---
FRONTIER_MODEL = "gpt-5.2"
FRONTIER_MODEL_PROVIDER = "openai"

# --- Lightweight model (extraction, classification, selection) ---
LIGHTWEIGHT_MODEL = "gpt-5-mini"
LIGHTWEIGHT_MODEL_PROVIDER = "openai"

# --- Docs pipeline ---
# Maximum documents the docs tool can select per invocation.
MAX_DOCS_PER_SELECTION = 3

# --- Agent mode ---
# "auto" (default), "graphql_sql", "sql_only", or "graphql_only"
AGENT_MODE = "auto"

# --- Per-prompt model assignment ---
# Maps each prompt to "frontier" or "lightweight".
# Override individual entries to experiment with model routing.
PROMPT_MODEL_ASSIGNMENTS = {
    "agent_system_prompt": "frontier",
    "graphql_classification": "lightweight",
    "graphql_entity_extraction": "lightweight",
    "id_resolution_selection": "lightweight",
    "sql_generation": "frontier",
    "product_extraction": "lightweight",
    "product_code_selection": "lightweight",
    "document_selection": "lightweight",
    "documentation_synthesis": "lightweight",
}


# ---------------------------------------------------------------------------
# Model pricing (per 1M tokens)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelPricing:
    """Per 1M token pricing for a model."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_creation: float = 0.0


MODEL_PRICING: dict[str, ModelPricing] = {
    # ── Anthropic ── cache_read ≈ 10% of input, cache_creation ≈ 125% of input
    "claude-opus-4-6-20260204": ModelPricing(
        5.00, 25.00, cache_read=0.50, cache_creation=6.25
    ),
    "claude-sonnet-4-6-20260217": ModelPricing(
        3.00, 15.00, cache_read=0.30, cache_creation=3.75
    ),
    "claude-sonnet-4-20250514": ModelPricing(
        3.00, 15.00, cache_read=0.30, cache_creation=3.75
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        1.00, 5.00, cache_read=0.10, cache_creation=1.25
    ),
    # ── OpenAI ── cache_read ≈ 10% of input, cache_creation = same as input
    "gpt-5.3-codex": ModelPricing(1.75, 14.00, cache_read=0.175, cache_creation=1.75),
    "gpt-5.2": ModelPricing(1.75, 14.00, cache_read=0.175, cache_creation=1.75),
    "gpt-5": ModelPricing(1.25, 10.00, cache_read=0.125, cache_creation=1.25),
    "gpt-5-mini": ModelPricing(0.25, 2.00, cache_read=0.025, cache_creation=0.25),
    "gpt-4.1": ModelPricing(2.00, 8.00, cache_read=0.50, cache_creation=2.00),
    "gpt-4.1-mini": ModelPricing(0.40, 1.60, cache_read=0.10, cache_creation=0.40),
    # ── Google ── cache_read ≈ 10% of input, cache_creation = same as input
    "gemini-3.1-pro": ModelPricing(2.00, 12.00, cache_read=0.20, cache_creation=2.00),
    "gemini-2.5-pro": ModelPricing(1.25, 10.00, cache_read=0.125, cache_creation=1.25),
    "gemini-3-flash": ModelPricing(0.50, 3.00, cache_read=0.05, cache_creation=0.50),
    "gemini-2.5-flash": ModelPricing(0.30, 2.50, cache_read=0.03, cache_creation=0.30),
}

DEFAULT_PRICING = ModelPricing(1.00, 5.00, cache_read=0.10, cache_creation=1.25)
