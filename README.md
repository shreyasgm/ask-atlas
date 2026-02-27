# Ask Atlas

Ask Atlas is an AI-powered assistant that answers natural language questions about international trade using data from the [Atlas of Economic Complexity](https://atlas.hks.harvard.edu/). Ask a question in plain English — the agent figures out which products, countries, and classification schemes are involved, generates SQL, runs it against a 60-table trade database, and streams back an interpreted answer.

```
"What were Brazil's top 5 exports to China in 2020?"
"How has Kenya's economic complexity changed over the last decade?"
"Which countries export the most pharmaceuticals by RCA?"
```

## Features

- **Agentic query pipeline** — A LangGraph agent breaks complex trade questions into sub-queries, resolving products, generating SQL, validating and executing it, and interpreting results across multiple iterations.
- **Product code resolution** — Resolves natural language ("cars", "crude oil") to exact HS/SITC codes via a 5-stage pipeline: LLM extraction, code suggestion, database verification, full-text search with trigram fallback, and LLM-based final selection.
- **Live pipeline visibility** — The UI shows each pipeline step in real time — identifying products, looking up codes, generating SQL, executing queries — so users can see exactly how the agent arrived at its answer.
- **Trade-specific controls** — Users constrain queries by trade mode (goods/services), classification schema (HS92, HS12, SITC), and direction (exports/imports) via toggle controls that propagate through the entire pipeline.
- **5 classification systems, ~60 tables** — Covers goods trade (HS 1992, HS 2012, SITC) and services trade (unilateral/bilateral) with economic complexity metrics (ECI, RCA, COG, distance) at multiple granularities.
- **LLM-as-judge evaluation** — A comprehensive eval framework scores answers across factual correctness, data accuracy, completeness, and reasoning quality, with three judge modes (ground-truth comparison, refusal testing, plausibility scoring) and automated reporting by category and difficulty.

## Architecture

```mermaid
graph TB
    subgraph Browser["Browser"]
        SPA["React 19 SPA<br/>Tailwind CSS<br/>SSE streaming"]
    end

    subgraph Firebase["Firebase Hosting"]
        Static["Static SPA assets"]
    end

    subgraph GCP["Google Cloud Platform (Cloud Run)"]
        direction TB
        API["FastAPI + Uvicorn<br/>2 workers · SSE streaming<br/>120s request timeout"]
        subgraph Agent["AtlasTextToSQL"]
            direction LR
            LG["LangGraph StateGraph<br/>Outer agent loop<br/>+ inner pipeline"]
        end
        API --> Agent
    end

    subgraph VPC["VPC (private network)"]
        AppDB[("App DB<br/>Cloud SQL<br/>---<br/>conversations<br/>checkpoints")]
    end

    subgraph AWS["AWS (external)"]
        AtlasDB[("Atlas Data DB<br/>RDS PostgreSQL<br/>read-only<br/>---<br/>7 schemas<br/>~60 tables")]
    end

    SPA -->|HTTPS| Static
    SPA -->|"HTTPS (API calls)"| API
    Agent -->|"Private IP"| AppDB
    Agent -->|"Static IP<br/>(whitelisted)"| AtlasDB

    style Browser fill:#e8f4f8,stroke:#2196F3
    style Firebase fill:#fff3e0,stroke:#FF9800
    style GCP fill:#e8f5e9,stroke:#4CAF50
    style VPC fill:#f3e5f5,stroke:#9C27B0
    style AWS fill:#fce4ec,stroke:#f44336
```

**Two-database design**: Trade data lives in a read-only AWS RDS instance managed by the Harvard Growth Lab. Application state (conversations, LangGraph checkpoints) lives in a separate Cloud SQL instance. Cloud Run connects to the external database through a VPC with a static egress IP.

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | React 19, TypeScript 5.9, Vite 7, Tailwind CSS 4, react-markdown |
| **Backend** | Python 3.12, FastAPI, LangGraph, LangChain, SQLAlchemy (sync + async) |
| **LLM** | OpenAI (default), with Anthropic and Google as swappable providers |
| **Database** | PostgreSQL (Atlas trade data on AWS RDS, app state on Cloud SQL) |
| **Infra** | Google Cloud Run, Firebase Hosting, Cloud Build, GitHub Actions CI/CD |
| **Testing** | pytest (4 tiers: unit/DB/integration/eval), Vitest (frontend) |

## Agent Pipeline

The LangGraph StateGraph uses an outer agent loop (LLM decides when to query) wrapping an inner deterministic pipeline:

```mermaid
graph TD
    START([START]) --> agent
    agent -->|"harmful or<br/>off-topic?"| REFUSE([Politely refuse])
    agent -->|tool_calls?| etq[extract_tool_question]
    agent -->|queries exceeded?| mqe[max_queries_exceeded]
    agent -->|no tool_calls| END_NODE([END])

    etq --> ep[extract_products]
    ep --> lc[lookup_codes]
    lc --> gti[get_table_info]
    gti --> gs[generate_sql]
    gs --> vs[validate_sql]

    vs -->|valid| es[execute_sql]
    vs -->|error| fr[format_results]
    es --> fr

    fr --> agent
    mqe --> agent

    style agent fill:#4CAF50,color:#fff,stroke:#388E3C
    style etq fill:#e3f2fd,stroke:#1976D2
    style ep fill:#e3f2fd,stroke:#1976D2
    style lc fill:#e3f2fd,stroke:#1976D2
    style gti fill:#e3f2fd,stroke:#1976D2
    style gs fill:#e3f2fd,stroke:#1976D2
    style vs fill:#fff3e0,stroke:#F57C00
    style es fill:#e3f2fd,stroke:#1976D2
    style fr fill:#e8f5e9,stroke:#388E3C
    style mqe fill:#ffebee,stroke:#c62828
```

The agent can loop multiple times per question — after seeing results from one query, it may decide to run additional queries to fully answer the user's question.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js >= 23 and pnpm >= 10
- Docker (for the local app database)
- Access to the Atlas trade database (connection string via `ATLAS_DB_URL`)
- At least one LLM API key (`OPENAI_API_KEY` by default)

### Environment Setup

Copy `.env.example` to `.env` and fill in the required values:

| Variable | Required | Description |
|----------|----------|-------------|
| `ATLAS_DB_URL` | Yes | PostgreSQL URI for the Atlas trade data DB (read-only) |
| `OPENAI_API_KEY` | Yes* | API key for the default LLM provider |
| `CHECKPOINT_DB_URL` | No | PostgreSQL URI for the app state DB; falls back to in-memory storage if unset |
| `ANTHROPIC_API_KEY` | No | Required only if switching LLM provider to Anthropic |
| `GOOGLE_API_KEY` | No | Required only if switching LLM provider to Google |

*Or the equivalent key for whichever provider you configure in `src/model_config.py`.

### Running Locally

```bash
# 1. Install backend dependencies
uv sync

# 2. Start the local app database (conversations + checkpoints)
docker compose up -d

# 3. Start the FastAPI backend
uv run uvicorn src.api:app --host 0.0.0.0 --port 8000

# 4. In a separate terminal, start the frontend
cd frontend && pnpm install && pnpm dev
```

The `docker-compose.yml` runs a PostgreSQL instance for app state on port **5435** (configurable via `APP_DB_PORT`). Set `CHECKPOINT_DB_URL=postgresql://ask_atlas_app:devpass@localhost:5435/ask_atlas_app` in your `.env` to use it. If you skip this step, the backend falls back to in-memory storage (conversations won't persist across restarts).

The frontend dev server (port 5173) proxies `/api` requests to the backend (port 8000).

### Running Tests

```bash
# Backend unit tests (mocked LLM + DB, no external deps)
PYTHONPATH=$(pwd) uv run pytest -m "not db and not integration and not eval"

# Frontend checks (type-check + lint + format)
cd frontend && pnpm check

# Frontend tests
cd frontend && pnpm test
```

**DB tests** require Docker test databases (separate from the local dev DB above):

```bash
# Start test DBs — Atlas mock data on port 5433, app DB on port 5434
docker compose -f docker-compose.test.yml up -d --wait

# Run DB tests
ATLAS_DB_URL=postgresql://postgres:testpass@localhost:5433/atlas_test \
CHECKPOINT_DB_URL=postgresql://ask_atlas_app:testpass@localhost:5434/ask_atlas_app \
PYTHONPATH=$(pwd) uv run pytest -m "db" -v
```

**Integration tests** hit real LLM APIs and require the corresponding API keys in `.env`:

```bash
PYTHONPATH=$(pwd) uv run pytest -m "integration" -v
```

See `CLAUDE.md` for full developer guidelines including all test tiers, code style, and deployment procedures.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/api/threads` | Create a new conversation thread |
| `GET` | `/api/threads` | List conversations for a session |
| `GET` | `/api/threads/{thread_id}/messages` | Retrieve message history and trade overrides |
| `DELETE` | `/api/threads/{thread_id}` | Delete a conversation |
| `POST` | `/api/chat` | Send a question, receive a complete response |
| `POST` | `/api/chat/stream` | Send a question, receive SSE-streamed response |
| `GET` | `/api/debug/caches` | Cache hit rate diagnostics |

## Documentation

- **[Technical Overview](docs/technical_overview.md)** — Comprehensive reference covering architecture, database schemas, pipeline nodes, frontend components, deployment, and evaluation system.
- **[CLAUDE.md](CLAUDE.md)** — Developer guidelines: build commands, test tiers, code style, and monorepo conventions.

## Acknowledgments

Ask Atlas relies on the [Atlas of Economic Complexity](https://atlas.hks.harvard.edu/) trade database curated by the [Harvard Growth Lab](https://growthlab.hks.harvard.edu/). Thanks to the Growth Lab development team for maintaining the database and making a copy available for this project.
