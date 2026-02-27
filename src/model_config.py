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
