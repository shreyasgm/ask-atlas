# Ask Atlas

Ask Atlas is an AI-powered assistant that answers natural language questions about international trade using data from the [Atlas of Economic Complexity](https://atlas.hks.harvard.edu/). Ask a question in plain English — the agent figures out which products, countries, and classification schemes are involved, generates SQL, runs it against a 60-table trade database, and streams back an interpreted answer.

```
"What were Brazil's top 5 exports to China in 2020?"
"How has Kenya's economic complexity changed over the last decade?"
"Which countries export the most pharmaceuticals by RCA?"
```

## Features

- **Natural language → answer** — Ask in plain English; the agent interprets your question, resolves products and countries, queries the data, and streams back an interpreted answer.
- **Live pipeline visibility** — Watch each step in real time — identifying products, looking up codes, generating SQL, executing queries — so you can see exactly how the agent arrived at its answer.
- **Atlas visualization links** — Results include clickable links to explore data on the [Atlas website](https://atlas.hks.harvard.edu/).
- **Trade controls** — Toggle goods/services, classification schema (HS92, HS12, SITC), and export/import direction. Settings propagate through the entire pipeline.
- **Conversation history** — Pick up where you left off; conversations persist across sessions.
- **Built-in domain knowledge** — Ask about methodology (ECI, RCA, product space) and get sourced explanations drawn from Atlas documentation.

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
            LG["LangGraph StateGraph<br/>Agent loop + 3 pipelines:<br/>SQL · GraphQL · Docs"]
        end
        API --> Agent
    end

    subgraph AtlasAPI["Atlas Public APIs"]
        ExploreAPI["Explore GraphQL API"]
        CPAPI["Country Pages GraphQL API"]
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
    Agent -->|"HTTPS"| ExploreAPI
    Agent -->|"HTTPS"| CPAPI

    style Browser fill:#e8f4f8,stroke:#2196F3
    style Firebase fill:#fff3e0,stroke:#FF9800
    style GCP fill:#e8f5e9,stroke:#4CAF50
    style AtlasAPI fill:#fff3e0,stroke:#FF9800
    style VPC fill:#f3e5f5,stroke:#9C27B0
    style AWS fill:#fce4ec,stroke:#f44336
```

**Two-database design**: Trade data lives in a read-only AWS RDS instance managed by the Harvard Growth Lab. Application state (conversations, LangGraph checkpoints) lives in a separate Cloud SQL instance. The agent also calls the Atlas public GraphQL APIs for visualization links and pre-computed metrics.

## Tech Stack

| Layer | Technologies |
|-------|-------------|
| **Frontend** | React 19, TypeScript 5.9, Vite 7, Tailwind CSS 4, react-markdown, KaTeX |
| **Backend** | Python 3.12, FastAPI, LangGraph, LangChain, SQLAlchemy, httpx, sqlglot |
| **LLM** | OpenAI (default), with Anthropic and Google as swappable providers |
| **Database** | PostgreSQL (Atlas trade data on AWS RDS, app state on Cloud SQL) |
| **Infra** | Google Cloud Run, Firebase Hosting, Cloud Build, GitHub Actions CI/CD |
| **Testing** | pytest (4 tiers: unit/DB/integration/eval), Vitest (frontend) |

## Agent Pipeline

A LangGraph StateGraph with an outer agent loop (the LLM decides which tool to call) wrapping three deterministic pipelines — SQL, GraphQL, and Docs:

```mermaid
graph TD
    START([START]) --> agent
    agent -->|no tool_calls| END_NODE([END])
    agent -->|queries exceeded?| mqe[max_queries_exceeded]
    agent -->|query_tool| etq[extract_tool_question]
    agent -->|atlas_graphql| egq[extract_graphql_question]
    agent -->|docs_tool| edq[extract_docs_question]

    etq --> ep[extract_products]
    ep --> lc[lookup_codes]
    lc --> gti[get_table_info]
    gti --> gs[generate_sql]
    gs --> vs[validate_sql]
    vs -->|valid| es[execute_sql]
    vs -->|error| fr[format_results]
    es --> fr
    fr --> agent

    egq --> cq[classify_query]
    cq -->|reject| fgr[format_graphql_results]
    cq -->|ok| ee[extract_entities]
    ee --> ri[resolve_ids]
    ri --> beg[build_and_execute_graphql]
    beg --> fgr
    fgr --> agent

    edq --> ss[select_and_synthesize]
    ss --> fdr[format_docs_results]
    fdr --> agent

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
    style egq fill:#fff3e0,stroke:#F57C00
    style cq fill:#fff3e0,stroke:#F57C00
    style ee fill:#fff3e0,stroke:#F57C00
    style ri fill:#fff3e0,stroke:#F57C00
    style beg fill:#fff3e0,stroke:#F57C00
    style fgr fill:#e8f5e9,stroke:#388E3C
    style edq fill:#f3e5f5,stroke:#7B1FA2
    style ss fill:#f3e5f5,stroke:#7B1FA2
    style fdr fill:#e8f5e9,stroke:#388E3C
    style mqe fill:#ffebee,stroke:#c62828
```

The agent can loop multiple times per question — after seeing results from one query, it may decide to call a different tool or run additional queries to fully answer the user's question.

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

- **[Technical Overview](docs/public/architecture.md)** — Comprehensive reference covering architecture, database schemas, pipeline nodes, frontend components, deployment, and evaluation system.

## Acknowledgments

Ask Atlas relies on the [Atlas of Economic Complexity](https://atlas.hks.harvard.edu/) trade database curated by the [Harvard Growth Lab](https://growthlab.hks.harvard.edu/). Thanks to the Growth Lab development team for maintaining the database and making a copy available for this project.
