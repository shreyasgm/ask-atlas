# Ask-Atlas

Ask-Atlas is an AI agent that answers questions about international trade data from the [Atlas of Economic Complexity](https://atlas.cid.harvard.edu/) database. It uses a LangGraph agent pipeline, a FastAPI backend with SSE streaming, and supports multiple LLM providers (OpenAI, Anthropic, Google).

## Features

- **Natural Language to SQL** — Converts user questions into optimized SQL queries against the Atlas trade database.
- **Trade-Specific Query Processing** — Supports multiple product classifications (SITC, HS 1992, HS 2012, etc.) and resolves product codes before generating SQL.
- **Agentic Query Planning** — Breaks complex queries into sub-questions and executes them sequentially.
- **Interactive Follow-ups** — Maintains conversation history via PostgreSQL checkpointing, allowing multi-turn conversations.
- **Multi-Provider LLM Support** — Swap between OpenAI, Anthropic, and Google models via configuration.
- **FastAPI Backend with SSE Streaming** — Decoupled API layer with real-time streamed responses, ready for integration with Slack, custom frontends, or the Atlas website.
- **Async-First Architecture** — All pipeline nodes are `async def`; designed for ~20+ concurrent users.

## Workflow Overview

```mermaid
flowchart TD
    A[User Question] --> B{Check Input Type}
    B -->|Non-trade/Harmful| C[Refuse to Answer]
    B -->|Valid Trade Input| D[Agentic Query Planning]

    D --> E[Break Question into Sub-Questions]

    E -->|Products Found| F[Product Lookup]
    E -->|No Products| H[Generate SQL Query]

    F -->|Product Codes| H
    E --> E2[Get Schema]
    E2 -->|Schema| H

    H --> H1[Validate SQL Query]
    H1 --> H2[Execute Query]
    H2 --> H3{Answer Complete?}
    H3 -->|No| D
    H3 -->|Yes| J[Generate Final Response]


    style C fill:#ffcccc
    style F fill:#e6f3ff
    style D fill:#e6e6ff
    style J fill:#e6ffe6
```

## Architecture

```
Client Layer          Streamlit (dev UI)  ·  FastAPI (production integrations)
                                │
Agent Service Layer   LangGraph StateGraph with async nodes
                      (planning → product lookup → SQL gen → execution → response)
                                │
Data Layer            PostgreSQL (Atlas trade DB)  ·  PostgreSQL (checkpoints)
```

## Usage

Ask-Atlas supports natural language queries about international trade data. Example questions:

- *"What were the top 5 products exported by the US to China in 2020?"*
- *"How did Brazil's wheat exports change between 2010 and 2020?"*
- *"What services did India export to the US in 2021?"*

Users can refine their queries or ask follow-ups, and the system will maintain context.

## Getting Started

```bash
# Install dependencies
pip install -e ".[dev]"

# Start the FastAPI backend
uv run uvicorn src.api:app --host 0.0.0.0 --port 8000

# In a separate terminal, start the Streamlit dev UI
uv run streamlit run app.py
```

### Running Tests

This project uses **test-driven development**. Write tests first, then implement.

```bash
# Run unit tests (no external dependencies)
PYTHONPATH=$(pwd) pytest -m "not db and not integration"

# Start Docker test DB (real production data subset, port 5433)
docker compose -f docker-compose.test.yml up -d --wait

# Run DB integration tests
ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
  PYTHONPATH=$(pwd) pytest -m "db" -v
```

See `CLAUDE.md` for full developer guidelines.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/threads` | Create a new conversation thread |
| `POST` | `/chat` | Send a question, receive a complete response |
| `POST` | `/chat/stream` | Send a question, receive SSE-streamed response chunks |

## Roadmap

- **Redis caching** for product code lookups and repeated queries
- **Human-in-the-loop** approval for expensive or large-result queries
- **LLM-as-a-judge evaluation suite** for systematic answer quality measurement
- **Structured output** for reliable, typed response formatting
- **Query validation layer** to catch SQL issues before execution
- **Interactive analysis canvas** — chat + live visualization workspace (long-term vision)

## Acknowledgments

Ask-Atlas relies on the Atlas trade database curated by the Harvard Growth Lab. Thanks to the Growth Lab development team for maintaining the database and making a copy available specifically for this project.
