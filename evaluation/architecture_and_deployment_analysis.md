# Ask-Atlas: Architecture, Deployment & Feature Analysis

> **Date:** 2026-02-21
> **Status:** Research & analysis, pending decisions
> **Related:** GitHub issues #14, #19, #20, #23, #30, #31, #32, #34, #35, #36, #39

---

## Table of Contents

1. [Conversation History: Database, Sessions & Persistence](#1-conversation-history)
2. [Caching Architecture](#2-caching-architecture)
3. [Deployment Architecture](#3-deployment-architecture)
4. [Static IP for AWS Database Access](#4-static-ip-for-aws-database-access)
5. [LLM Routing: OpenRouter vs Direct API](#5-llm-routing)
6. [LLM Cost Analysis](#6-llm-cost-analysis)
7. [GCP Infrastructure Cost Analysis](#7-gcp-infrastructure-cost-analysis)
8. [Playwright E2E Testing](#8-playwright-e2e-testing)
9. [Issue Priority Discussion](#9-issue-priority-discussion)

---

## 1. Conversation History

### 1.1 Current State of the Codebase

The infrastructure for persistent conversations is **partially built** but not wired up:

- **`src/persistence.py`** already has `CheckpointerManager` and `AsyncCheckpointerManager` classes that attempt PostgresSaver first, then fall back to MemorySaver. The code is there — it just needs a `CHECKPOINT_DB_URL` environment variable pointing at a PostgreSQL database.

- **`src/config.py`** defines `checkpoint_db_url` as an optional setting separate from `atlas_db_url`. The design already anticipates two databases.

- **`src/api.py`** generates UUID thread IDs (`POST /threads`, or auto-generated in `POST /chat` and `POST /chat/stream`). Thread IDs are passed to LangGraph via `configurable: { thread_id }`.

- **`src/state.py`** uses LangGraph's `add_messages` reducer — message history accumulates across turns within the same thread automatically.

- **Frontend** (`frontend/src/hooks/use-chat-stream.ts`) receives a `thread_id` SSE event and navigates to `/chat/{threadId}`. But thread ID is stored only in React state — it vanishes on page refresh.

- **Frontend sidebar** (`frontend/src/components/workspace/left-sidebar.tsx`) has placeholder text: "No conversations yet" and "No saved queries." The UI skeleton is ready but the data layer doesn't exist.

- **No persistent storage anywhere in the frontend:** Zero usage of localStorage, sessionStorage, or cookies. Verified via search across the entire frontend source.

**What's missing:** (1) a running PostgreSQL database for checkpoints, (2) API endpoints to list/get/delete conversations, and (3) frontend code to fetch and display conversation history.

### 1.2 Database Requirements

**The Atlas data database is managed externally** (by the software development team, hosted on AWS). You don't control it and won't incur costs for it. This means the app database needs to be a **completely separate PostgreSQL server** — you can't add a second database to the Atlas server since you don't have admin access.

**Do you need an app database for cookie-based conversation history?**

**Yes, you do.** Here's why: cookies/localStorage only store the *identifier* (a UUID). The actual conversation data (messages, tool calls, intermediate agent state) has to live somewhere persistent. That somewhere is the database. The flow is:

1. User visits Ask Atlas → frontend checks localStorage for a `session_id`
2. If none exists, generate a UUID and store it in localStorage
3. Send `session_id` as a header with every API request
4. Backend stores conversation data in PostgreSQL, keyed by `session_id`
5. When user returns, frontend reads `session_id` from localStorage → backend loads their conversations

Without a database, the conversations exist only in memory (MemorySaver), and memory is wiped every time the server restarts or Cloud Run recycles an instance. The cookie is just a pointer — the data it points to needs to be in a database.

**What the app database stores:**

| Table/Schema | Purpose | Who writes |
|---|---|---|
| LangGraph checkpoint tables | Full conversation state (messages, tool calls, intermediate state) | LangGraph's `AsyncPostgresSaver` — creates tables automatically |
| `conversations` | Metadata: title, created_at, updated_at, session_id | Your API |
| `users` (future) | User accounts when auth is added | Your API |
| `saved_queries` (future) | User-bookmarked SQL queries | Your API |

**Size estimate:** The app database will be tiny. Each conversation's checkpoint data is maybe 10-50 KB (mostly serialized message text). With 20 users, 30 conversations/day, 20 days/month = ~12,000 conversations/month = ~120-600 MB/month. Even after a year, this is under 10 GB. The smallest Cloud SQL instance (db-f1-micro with 10 GB SSD) would be more than adequate.

**Whether to implement now or later:** You could defer the app database if you accept that:
- Conversations are lost on server restart (MemorySaver is in-memory only)
- Multi-turn conversations within a session still work (MemorySaver keeps state while the process runs)
- No conversation history in the sidebar
- The skipped integration test (`test_persistence_integration.py`) remains skipped

The database becomes necessary when you want any of: conversation history persistence, surviving server restarts, or deploying to Cloud Run (where instances are recycled unpredictably).

### 1.3 Cookie/localStorage Approach (Approach B) — Implementation Details

Since you've chosen this approach, here's the concrete implementation plan:

**Frontend changes:**
```typescript
// src/utils/session.ts
const SESSION_KEY = 'ask_atlas_session_id';

export function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}
```

**How it integrates with the existing API:**
- Add `X-Session-Id` header to every fetch request in `use-chat-stream.ts`
- Backend reads this header and uses it to filter conversations in `GET /threads`
- Thread IDs remain UUIDs (as they are now); the session ID is just a grouping key

**Data loss scenarios and mitigations:**

| Scenario | Impact | Mitigation |
|---|---|---|
| User clears browser data | Session UUID lost, conversations orphaned in DB | Add a small info tooltip: "History is stored in this browser" |
| User uses incognito mode | New session each time, no history | Detect and show a notice |
| User switches browsers/devices | Different session, no cross-device history | Expected behavior for cookie approach |
| User switches computers | New session, fresh start | Expected |
| IT policy clears storage | Session lost | No good mitigation short of user auth |

**Setting honest expectations in the UI:** The sidebar should say something like "Recent conversations (this browser)" rather than just "Conversations" — this signals to users that history is browser-local.

**Backend endpoints needed (issue #35):**

```
GET  /threads?session_id={id}           → list conversations for this session
GET  /threads/{thread_id}/messages      → get message history for a thread
DELETE /threads/{thread_id}             → delete a conversation
```

LangGraph's `AsyncPostgresSaver` stores full state per thread_id. The `/messages` endpoint can reconstruct message history from the checkpoint state. You also need a `conversations` metadata table that maps `thread_id` → `session_id` + title + timestamps.

### 1.4 Future Path to User Auth

When you eventually add authentication, the migration is straightforward:
1. Add a `users` table and Google OAuth endpoints
2. When a user logs in, migrate their localStorage session's conversations to their user account (match `session_id` → `user_id` one time)
3. After migration, conversations are keyed by `user_id` instead of `session_id`
4. Remove localStorage dependency — session comes from the auth cookie

This is a clean upgrade path where the cookie approach serves as a stepping stone.

---

## 2. Caching Architecture

### 2.1 Current State: Almost No Caching

The codebase has **almost zero caching**. The only cached value is the `Settings` singleton via `@lru_cache()` in `src/config.py`. Everything else runs fresh on every request:

- **Product classification lookups** (`src/product_and_schema_lookup.py`): Every query triggers LLM calls and database full-text searches to map product names to HS codes. No results are cached.
- **Database table metadata** (`src/sql_multiple_schemas.py`): Table schemas are reflected fresh on every `AtlasTextToSQL` instantiation via `metadata.reflect()`.
- **LLM responses**: No caching. Each query runs the full LangGraph pipeline from scratch.

This is explicitly documented in `technical_overview.md`:
> "The application doesn't cache: LLM responses, database query results, product code lookups. Each query runs fresh every time."

### 2.2 What Should Be Cached

**Tier 1 — High value, stable data (cache aggressively):**

| Data | Current Cost | Cache TTL | Why |
|---|---|---|---|
| Product name → HS code mappings | LLM call ($) + DB full-text search (~200ms) | 24 hours – 7 days | HS codes change ~every 5 years (WCO revisions). Same products are queried repeatedly. |
| Official product details (`_get_official_product_details()`) | DB query (~100ms) | 24 hours | Deterministic: same code → same official name |
| Database table/schema metadata | `metadata.reflect()` (~500ms) | 1-6 hours or until restart | Schemas change only on deployment |

**Tier 2 — Medium value (cache selectively):**

| Data | Cache TTL | Why |
|---|---|---|
| Full-text search results (`_direct_text_search()`) | 1-6 hours | Same product names searched repeatedly; results stable |
| SQL generation for identical questions | 5-15 minutes | Trade data doesn't change within minutes, but context-dependent queries shouldn't be cached long |

**Not worth caching:** Conversational LLM responses (too context-dependent), streaming SSE events, per-user state.

### 2.3 In-Process Caching on Cloud Run

**How it works:** Python's `functools.lru_cache`, `cachetools.TTLCache`, or module-level dictionaries live in the process's memory. Each Cloud Run container instance maintains its own cache that persists across requests handled by that instance.

```python
from cachetools import TTLCache
from asyncache import cached

# Module-level cache: survives across requests on the same instance
_hs_code_cache = TTLCache(maxsize=2048, ttl=86400)  # 24-hour TTL

@cached(_hs_code_cache)
async def lookup_hs_code(product_name: str, schema: str) -> list[dict]:
    # Expensive LLM + DB call
    ...
```

**Cloud Run instance lifecycle and its impact on caching:**

- **Idle timeout:** Cloud Run keeps idle instances alive for a **maximum of ~15 minutes** after the last request. It can shut them down sooner.
- **No guaranteed lifetime:** Even actively-serving instances can be recycled at Google's discretion (maintenance, rebalancing).
- **Scale to zero:** If no requests arrive, all instances eventually shut down and all in-process cache is lost.
- **Min-instances:** Setting `min-instances=1` keeps one container warm but it can still be recycled.

**Practical implications:** In-process cache is "best effort." On a warm instance handling regular traffic, the cache can live for hours. But you can't depend on it — any request might hit a fresh instance with an empty cache.

**Multi-instance cache divergence:** If Cloud Run scales to 3 instances:
- Each has its own independent cache (no shared state)
- Users routed to different instances get different cache hit rates
- Popular lookups are cached redundantly across instances (3x memory use)
- New instances during traffic spikes start with empty caches (worst time for cache misses)

**Despite these limitations, in-process caching is still worth implementing** because:
1. At 20 concurrent users, you likely have 1-3 instances, so redundancy is minimal
2. Even a 50% cache hit rate halves your HS code lookup costs
3. Zero infrastructure cost, zero additional latency (nanoseconds vs. milliseconds for Redis)
4. It's the recommended first step before adding external caching

### 2.4 Redis: When You Need It and Which Service

**When in-process caching becomes insufficient:**
1. Cache hit rate drops below ~40% due to frequent instance recycling
2. New instances take too long to warm up their caches
3. You need cache persistence across deployments
4. You need shared state across instances (rate limiting, sessions)

**Redis service options:**

| Service | Monthly Cost | Latency | Setup | Best For |
|---|---|---|---|---|
| **Cloud Memorystore** | ~$25-30/mo minimum (1 GB Basic + VPC) | ~1-2ms | Medium (VPC config required) | Production at scale |
| **Upstash** | $0 (free tier: 500K commands/mo) | ~5-15ms (REST API) | Low (env vars only) | Small apps, serverless |
| **Self-hosted on VM** | ~$7/mo (e2-micro) | ~2-5ms | High (you manage everything) | Cost-sensitive, ops-savvy |

**Upstash is the clear winner for your scale.** Here's why:

- **Free tier** covers your likely usage: at 20 users doing ~30 queries/day, that's ~12,000 cache reads + writes/month — well under 500K commands.
- **No VPC required:** Connects over HTTPS with token auth. No VPC connectors, no networking configuration.
- **Serverless pricing:** Pay per command, not per provisioned GB. Scales to zero.
- **Data persists to disk:** Unlike pure in-memory Redis, Upstash persists data, so it survives restarts.
- **Pay-as-you-go cap:** Maximum $120/month regardless of usage.

The extra 5-10ms of latency from Upstash vs. VPC-local Memorystore is irrelevant when your LLM calls take 2-10 seconds.

**Memorystore makes sense only when:**
- You have 100+ concurrent users and need sub-millisecond cache access
- You're already using a VPC (which you will be, for the static IP setup — see Section 4)
- The $25/mo floor is trivial relative to your total spend

**Integration with FastAPI:**

```python
# src/cache.py — Two-tier caching pattern
from cachetools import TTLCache
from upstash_redis import Redis

# Tier 1: in-process (nanoseconds, lost on instance recycle)
_local_cache = TTLCache(maxsize=1024, ttl=3600)  # 1-hour local TTL

# Tier 2: Upstash Redis (milliseconds, survives restarts)
redis = Redis.from_env()  # reads UPSTASH_REDIS_REST_URL and token

async def get_hs_codes(product_name: str, schema: str) -> list[dict]:
    key = f"hs:{schema}:{normalize(product_name)}"

    # Check local cache first
    if key in _local_cache:
        return _local_cache[key]

    # Check Redis
    cached = redis.get(key)
    if cached:
        _local_cache[key] = cached  # warm local cache
        return cached

    # Compute (expensive)
    result = await classify_product(product_name, schema)
    redis.setex(key, 86400, json.dumps(result))  # 24h in Redis
    _local_cache[key] = result
    return result
```

### 2.5 Recommended Caching Roadmap

**Phase 1 (Now):** Add in-process `TTLCache` caching to:
- `_get_official_product_details()` and `_aget_official_product_details()` — 24h TTL
- `_direct_text_search()` and `_adirect_text_search()` — 6h TTL
- Database metadata loading (`_initialize_metadata`) — 1h TTL or until restart
- Add cache hit/miss logging so you have data to inform the Redis decision

**Phase 2 (When deploying to Cloud Run):** Add Upstash Redis as a second tier:
- Start with HS code lookups only (the most expensive operation)
- Free tier is likely sufficient
- Add `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` to your environment

**Phase 3 (If needed):** Evaluate whether Upstash pay-as-you-go or Memorystore makes more sense based on observed usage patterns.

---

## 3. Deployment Architecture

### 3.1 Architecture Overview

Your app has two external dependencies (Atlas DB on AWS, LLM APIs) and three components you deploy:

```
                    ┌──────────────────────────┐
                    │    Firebase Hosting       │
                    │    (React static build)   │
                    │    CDN-cached globally    │
                    └────────┬─────────────────┘
                             │ /api/* requests
                             ▼
               ┌──────────────────────────────────┐
               │         Cloud Run                 │
               │    (FastAPI backend)               │
               │    1-5 instances, auto-scaled      │
               │    SSE streaming, static outbound IP│
               └──┬──────────┬──────────┬─────────┘
                  │          │          │
    ┌─────────────┘     ┌────┘          └──────────────┐
    │ Static IP         │ Private IP                   │ HTTPS
    │ (via Cloud NAT)   │                              │
    ▼                   ▼                              ▼
┌──────────┐    ┌──────────────┐              ┌───────────────┐
│ Atlas DB │    │  Cloud SQL   │              │  OpenAI /     │
│ (AWS)    │    │ (atlas_app)  │              │  Anthropic    │
│ IP-      │    │ checkpoints, │              │  APIs         │
│ whitelisted│  │ conversations│              └───────────────┘
└──────────┘    └──────────────┘
                        │
                  (optional, later)
                        │
                ┌───────────────┐
                │ Upstash Redis │
                │ (cache)       │
                └───────────────┘
```

**Key design decisions:**
- Atlas DB is on AWS, managed by your team — **you don't pay for it, but you need a static IP to access it**
- App database (Cloud SQL) is separate — only for checkpoints, conversations, user data
- Frontend is static files on Firebase Hosting (essentially free)
- Redis is optional, deferred until needed, and would use Upstash (not Memorystore) for cost reasons

### 3.2 GCP Services

#### Cloud Run (FastAPI Backend)

Best fit because:
- SSE streaming support (up to 60 minutes per connection)
- Auto-scales from 0 to N instances
- Pay-per-use pricing
- Minimal operational overhead
- Supports Direct VPC Egress for static outbound IP (see Section 4)

Why not alternatives:
- **App Engine:** Older, less flexible, being superseded
- **GKE (Kubernetes):** Massive overkill for one service
- **Compute Engine (VM):** You manage everything — OS patching, process supervision, restarts

#### Firebase Hosting (React Frontend)

- `pnpm build` → `dist/` folder → `firebase deploy`
- Automatic SSL, custom domains, SPA routing
- Can proxy `/api/**` requests directly to Cloud Run (no separate load balancer needed)
- Free tier: 10 GB storage, 360 MB/day transfer (more than enough)

#### Cloud SQL (App Database Only)

- **Not** for Atlas trade data (that's on AWS)
- Only for LangGraph checkpoints, conversation metadata, future user accounts
- Tiny workload — the smallest instance (db-f1-micro at ~$9/mo) would work
- Connected to Cloud Run via the VPC you're already setting up for the static IP

### 3.3 Frontend-to-Backend Routing in Production

In development, Vite's proxy handles `/api/*` → `localhost:8000`. In production, Firebase Hosting handles this:

```json
// firebase.json
{
  "hosting": {
    "public": "dist",
    "rewrites": [
      {
        "source": "/api/**",
        "run": { "serviceId": "ask-atlas-backend", "region": "us-east1" }
      },
      { "source": "**", "destination": "/index.html" }
    ]
  }
}
```

This means `app.askatlas.com/api/chat/stream` → routes to Cloud Run, and `app.askatlas.com/*` → serves the React SPA. No CORS issues, one domain, simple.

---

## 4. Static IP for AWS Database Access

### 4.1 The Problem

The Atlas data database on AWS requires IP whitelisting. Cloud Run doesn't have a static outbound IP by default — it shares a pool of dynamic, ephemeral IPs from Google's infrastructure. You need to route Cloud Run's traffic through a fixed IP.

### 4.2 The Solution: Direct VPC Egress + Cloud NAT

The architecture routes all outbound traffic from Cloud Run through a VPC with a Cloud NAT gateway that has a reserved static IP:

```
Cloud Run Service
    → VPC (via Direct VPC Egress)
        → Cloud NAT (with reserved static IP)
            → Internet → AWS PostgreSQL
```

**Direct VPC Egress** (GA since 2024) is the recommended approach over the older VPC Connector. It's cheaper (no always-on connector VMs), faster (higher throughput), and simpler.

### 4.3 Setup Steps

```bash
# 1. Create a VPC network and subnet
gcloud compute networks create ask-atlas-vpc --subnet-mode=custom
gcloud compute networks subnets create ask-atlas-subnet \
  --network=ask-atlas-vpc \
  --region=us-east1 \
  --range=10.124.0.0/28

# 2. Reserve a static external IP address
gcloud compute addresses create ask-atlas-static-ip --region=us-east1

# 3. See the IP (this is what you whitelist on AWS)
gcloud compute addresses describe ask-atlas-static-ip --region=us-east1

# 4. Create a Cloud Router
gcloud compute routers create ask-atlas-router \
  --network=ask-atlas-vpc \
  --region=us-east1

# 5. Create Cloud NAT with the static IP
gcloud compute routers nats create ask-atlas-nat \
  --router=ask-atlas-router \
  --region=us-east1 \
  --nat-custom-subnet-ip-ranges=ask-atlas-subnet \
  --nat-external-ip-pool=ask-atlas-static-ip

# 6. Deploy Cloud Run with Direct VPC Egress
gcloud run deploy ask-atlas-backend \
  --image=IMAGE_URL \
  --network=ask-atlas-vpc \
  --subnet=ask-atlas-subnet \
  --region=us-east1 \
  --vpc-egress=all-traffic
```

### 4.4 Cost of Static IP Setup

| Component | Monthly Cost |
|---|---|
| Reserved static IP | ~$3.60 ($0.005/hr) |
| Cloud NAT gateway | ~$1-5 (usage-based) |
| Cloud NAT data processing | ~$0.23-0.45 ($0.045/GiB, for ~5-10 GiB) |
| Direct VPC Egress | $0 (no compute cost, only network egress) |
| **Total** | **~$5-10/month** |

Compare this to the legacy VPC Connector approach which costs ~$14/month minimum (2 always-on e2-micro VMs) plus NAT costs. Direct VPC Egress saves about $10/month.

### 4.5 Important Gotchas

**All outbound traffic goes through NAT:** When you set `--vpc-egress=all-traffic`, every outbound request goes through Cloud NAT — not just database connections. This includes calls to OpenAI, Anthropic, Upstash, etc. All traffic incurs the $0.045/GiB NAT data processing charge. There's no way to selectively route only some traffic through NAT.

**Port exhaustion risk:** Cloud NAT uses source port mapping. With a single static IP connecting to one PostgreSQL endpoint, you're limited in concurrent connections. **Mitigation:** Your existing SQLAlchemy connection pooling (`pool_size=10, max_overflow=20`) already limits concurrent connections, so this shouldn't be an issue at your scale.

**Region must match:** Cloud Run, subnet, Cloud Router, Cloud NAT, and static IP must all be in the same region. Choose based on proximity to your users and the AWS database. If Atlas DB is in `us-east-1` (AWS Virginia), use `us-east1` (GCP South Carolina) — same general area, minimal cross-region latency.

**The VPC you set up for the static IP can also be used for Cloud SQL.** Since you're creating a VPC anyway, Cloud SQL can connect via private IP within the same VPC rather than requiring a separate VPC connector. This simplifies the overall network architecture.

### 4.6 Verifying It Works

After deployment, you can verify the static IP from your Cloud Run service:

```python
import httpx
response = httpx.get("https://api.ipify.org")
print(response.text)  # Should show your reserved static IP
```

Then give this IP to your software development team to whitelist on the AWS security group.

---

## 5. LLM Routing

### 5.1 OpenRouter vs Direct API: Analysis

**OpenRouter** is a managed API gateway that provides a unified interface to 200+ LLM models through a single endpoint. Instead of integrating with each provider separately, you call `openrouter.ai/api/v1/chat/completions` and it routes to the right provider.

| Factor | OpenRouter | Direct API |
|---|---|---|
| **Latency overhead** | +40-150ms per request | None (baseline) |
| **Cost markup** | 5% on top of provider prices | Provider prices only |
| **API integration** | One endpoint for all models | Separate SDK per provider |
| **Provider-specific features** | May be delayed or incomplete | Full access (prompt caching, etc.) |
| **Reliability** | Additional point of failure | Direct to provider |
| **Fallback routing** | Automatic model fallback | You implement yourself |

**Latency details:** OpenRouter adds approximately 40-150ms per request due to the extra network hop, request parsing/routing, and response relay. For a streaming chatbot where time-to-first-token matters, this is perceptible. Direct API calls to OpenAI/Anthropic have first-token latency of 200-800ms, so OpenRouter adds 5-75% overhead on the most latency-sensitive metric.

**The 5% markup in dollar terms:** At your estimated LLM spend of $150-350/month, that's $7.50-17.50/month. Not huge, but not nothing.

### 5.2 Recommendation: Direct API Access

For Ask Atlas, **connect directly to OpenAI and Anthropic APIs.** Reasons:

1. **You use only 2 providers.** Managing two API keys is trivial. The unified-API benefit of OpenRouter is marginal.

2. **Your codebase already abstracts providers.** `create_llm()` in `src/config.py` is a provider factory that routes to OpenAI/Anthropic/Google based on configuration. You already have the "unified interface" at the application layer.

3. **Latency matters for a chatbot.** Every 50-100ms of added overhead degrades the user experience of streaming responses.

4. **Prompt caching saves real money.** Both Anthropic and OpenAI offer ~90% savings on cached input tokens. OpenRouter may not expose these features fully or immediately.

5. **No additional dependency.** If OpenRouter has an outage, your entire LLM stack goes down. Direct integration has no single additional point of failure.

### 5.3 When OpenRouter Would Make Sense

- Rapid prototyping phase testing 10+ models from different providers
- Consumer apps needing automatic failover across providers
- Teams without the engineering capacity for multiple integrations

### 5.4 Alternatives Worth Knowing About

| Tool | Type | Latency | Best For |
|---|---|---|---|
| **LiteLLM** | Self-hosted (open source) | ~0ms (runs in your infra) | Unified API without third-party dependency |
| **Portkey** | Managed + self-hosted | ~50ms | Enterprise observability, guardrails |
| **Helicone** | Managed (open source, Rust-based) | ~50ms | Logging, caching, analytics |

LiteLLM could be interesting if you eventually support 3+ providers, since it adds near-zero latency and provides a unified interface without OpenRouter's markup or dependency. But with just OpenAI + Anthropic, your existing `create_llm()` factory is simpler and sufficient.

---

## 6. LLM Cost Analysis

### 6.1 Current Model Pricing (February 2026)

**Anthropic Claude Models (per million tokens):**

| Model | Input | Output | Cached Input (write) | Cached Input (read) |
|---|---|---|---|---|
| **Claude Sonnet 4.6** | $3.00 | $15.00 | $3.75 | $0.30 |
| Claude Haiku 4.5 | $1.00 | $5.00 | $1.25 | $0.10 |
| Claude Opus 4.6 | $5.00 | $25.00 | $6.25 | $0.50 |

**OpenAI Models (per million tokens):**

| Model | Input | Output | Cached Input |
|---|---|---|---|
| **GPT-5.2** | $1.75 | $14.00 | $0.175 |
| GPT-5 | $1.25 | $10.00 | $0.125 |
| GPT-5 Mini | $0.25 | $2.00 | $0.025 |
| GPT-5 Nano | $0.05 | $0.40 | $0.005 |
| GPT-4.1 | $2.00 | $8.00 | $0.50 |
| GPT-4.1 Mini | $0.40 | $1.60 | $0.10 |

### 6.2 Usage Assumptions

- 20 concurrent users, ~30 messages/user/day, 20 business days/month
- **12,000 messages/month total**
- Each message triggers **2 LLM calls:**
  - **Metadata extraction** (smaller model): product name extraction, schema detection
  - **SQL generation** (larger model): generates the actual SQL query
- **Token estimates per call:**
  - Metadata: ~1,500 input tokens (prompt + context), ~200 output tokens
  - SQL generation: ~3,000 input tokens (system prompt + schema + conversation history), ~800 output tokens
- **Context window growth multiplier:** Later messages in a conversation include more history. Average conversation = ~8 messages. Applying a 1.5x multiplier on input tokens:
  - Metadata adjusted: ~2,250 input, ~200 output
  - SQL generation adjusted: ~4,500 input, ~800 output

### 6.3 Monthly Token Volumes

| Call Type | Messages | Input Tokens | Output Tokens |
|---|---|---|---|
| Metadata (smaller model) | 12,000 | 27.0M | 2.4M |
| SQL Generation (larger model) | 12,000 | 54.0M | 9.6M |
| **Total** | 24,000 | **81.0M** | **12.0M** |

### 6.4 Cost Scenarios

#### Scenario A: Claude Sonnet 4.6 (SQL) + Claude Haiku 4.5 (Metadata)

| Component | Input Cost | Output Cost | Subtotal |
|---|---|---|---|
| Haiku 4.5: 27M in × $1.00/M, 2.4M out × $5.00/M | $27.00 | $12.00 | **$39.00** |
| Sonnet 4.6: 54M in × $3.00/M, 9.6M out × $15.00/M | $162.00 | $144.00 | **$306.00** |
| **Total without caching** | | | **$345/mo** |

With prompt caching (~60% cache hit rate on system prompts and schema context):
- Haiku cached reads: 60% of 27M = 16.2M at $0.10/M instead of $1.00/M → saves ~$14.58
- Sonnet cached reads: 60% of 54M = 32.4M at $0.30/M instead of $3.00/M → saves ~$87.48
- **Total with caching: ~$243/mo**

#### Scenario B: GPT-5.2 (SQL) + GPT-4.1 Mini (Metadata)

| Component | Input Cost | Output Cost | Subtotal |
|---|---|---|---|
| GPT-4.1 Mini: 27M in × $0.40/M, 2.4M out × $1.60/M | $10.80 | $3.84 | **$14.64** |
| GPT-5.2: 54M in × $1.75/M, 9.6M out × $14.00/M | $94.50 | $134.40 | **$228.90** |
| **Total without caching** | | | **$244/mo** |

With cached input (~60% hit rate):
- GPT-4.1 Mini cached: 16.2M at $0.10/M instead of $0.40/M → saves ~$4.86
- GPT-5.2 cached: 32.4M at $0.175/M instead of $1.75/M → saves ~$51.03
- **Total with caching: ~$187/mo**

#### Scenario C: GPT-5 (SQL) + GPT-5 Nano (Metadata) — Budget Option

| Component | Input Cost | Output Cost | Subtotal |
|---|---|---|---|
| GPT-5 Nano: 27M in × $0.05/M, 2.4M out × $0.40/M | $1.35 | $0.96 | **$2.31** |
| GPT-5: 54M in × $1.25/M, 9.6M out × $10.00/M | $67.50 | $96.00 | **$163.50** |
| **Total without caching** | | | **$166/mo** |

With caching: **~$107/mo**

### 6.5 Cost Summary

| Scenario | Without Caching | With ~60% Cache Hits |
|---|---|---|
| **A: Sonnet 4.6 + Haiku 4.5** | $345/mo | ~$243/mo |
| **B: GPT-5.2 + GPT-4.1 Mini** | $244/mo | ~$187/mo |
| **C: GPT-5 + GPT-5 Nano** | $166/mo | ~$107/mo |

### 6.6 Key Observations

1. **Output tokens dominate the SQL generation cost.** GPT-5.2 ($14/M output) and Sonnet 4.6 ($15/M output) are comparable. The input price difference ($1.75 vs $3.00) matters more than it first appears because of the high input volume.

2. **The metadata model barely matters for total cost.** It accounts for 5-15% of the bill. Using GPT-5 Nano ($0.05/$0.40) instead of Haiku ($1.00/$5.00) saves ~$35/month. The question is whether it handles metadata extraction well enough.

3. **Prompt caching is a significant lever.** Both providers offer ~90% savings on cached input tokens. Your system prompt, schema context, and few-shot examples are constant across calls, so a substantial portion of input can be cached. The ~$60-100/month savings from caching is worth implementing properly.

4. **GPT-5 (not 5.2) is an interesting value point.** Same $1.25 input as GPT-5.2 but $10 output vs $14 — saving ~30% on output tokens. Whether the quality difference justifies the 5.2 premium depends on your SQL generation accuracy requirements. Worth testing in your eval framework.

5. **At these volumes, the 5% OpenRouter markup would cost $8-17/month** — minor in absolute terms, but the latency penalty is the bigger concern for a real-time chatbot.

---

## 7. GCP Infrastructure Cost Analysis

### 7.1 Revised Cost Model

Since the Atlas data DB is on AWS (managed by your team, no cost to you), the infrastructure costs are lower than the previous estimate. You only pay for: Cloud Run, a small Cloud SQL instance for app data, the static IP/NAT setup, and frontend hosting.

### 7.2 Component Costs

#### Cloud Run (FastAPI Backend)

**Pricing:** $0.0000240/vCPU-sec, $0.0000025/GiB-sec. Free tier: 180K vCPU-sec, 360K GiB-sec, 2M requests/month.

**Low (scale-to-zero):**
- 12,000 requests × 30 sec × 2 vCPU = 720,000 vCPU-sec
- Minus free tier (180K) = 540,000 billable
- CPU: 540,000 × $0.0000240 = $12.96
- Memory within free tier
- **~$13/month**

**Medium (1 min-instance always on):**
- Always-on: 2,592,000 vCPU-sec - 180K free = 2,412,000 × $0.0000240 = $57.89
- Memory: 936,000 GiB-sec × $0.0000025 = $2.34
- Burst (2 extra instances, 4h/day, 20 days): $13.82
- **~$74/month**

**High (2 min-instances):**
- ~$120 idle + ~$20 burst
- **~$140/month**

#### Cloud SQL (App Database Only — NOT Atlas Data)

Since this is only for checkpoints and conversation metadata (tiny workload):

| Scenario | Instance | Storage | Backups | Total |
|---|---|---|---|---|
| **Low** | db-f1-micro ($9.37) | 10 GB SSD ($1.70) | $0.80 | **~$12/mo** |
| **Medium** | db-g1-small ($27.25) | 10 GB SSD ($1.70) | $0.80 | **~$30/mo** |

**Note:** The previous estimates included a VPC connector cost (~$7-10/mo). With Direct VPC Egress (which you're setting up anyway for the static IP), the VPC connector is not needed, saving $7-10/month.

#### Static IP + Cloud NAT

As detailed in Section 4: **~$5-10/month**

#### Frontend Hosting

Firebase Hosting free tier: **$0/month**

#### Redis/Cache

In-process caching: **$0/month**
Upstash (if added later): **$0/month** (free tier) or **$2-10/month** (moderate usage)

### 7.3 Total Infrastructure Cost (Excluding LLM)

| Component | Low | Medium |
|---|---|---|
| Cloud Run | $13 | $74 |
| Cloud SQL (app DB only) | $12 | $30 |
| Static IP + NAT | $5 | $10 |
| Frontend hosting | $0 | $0 |
| Cache (Upstash) | $0 | $0 |
| Other (logging, registry) | $2 | $5 |
| **Infrastructure Total** | **~$32/mo** | **~$119/mo** |

### 7.4 Grand Total (Infrastructure + LLM)

| Configuration | Infrastructure | LLM (with caching) | Grand Total |
|---|---|---|---|
| **Budget** (scale-to-zero, GPT-5 + Nano) | $32 | $107 | **~$139/mo** |
| **Moderate** (1 min-instance, GPT-5.2 + Mini) | $119 | $187 | **~$306/mo** |
| **Premium** (1 min-instance, Sonnet 4.6 + Haiku) | $119 | $243 | **~$362/mo** |

**The LLM API is your largest cost.** Infrastructure is $32-119/month. LLM costs are $107-345/month. The most impactful cost decision is which models you use, not which Cloud SQL instance size you pick.

### 7.5 Cost-Saving Strategies

1. **Scale to zero** (`min-instances=0`): Saves ~$60/month. Cost: 5-10 second cold start for the first user of the day.

2. **Use GPT-5 instead of GPT-5.2:** Saves ~$30-40/month on output tokens. Test whether quality holds up in your eval framework.

3. **Implement prompt caching:** Saves ~$60-100/month. Both OpenAI and Anthropic offer this.

4. **Use the cheapest metadata model that works:** GPT-5 Nano at $0.05/$0.40 vs Haiku at $1.00/$5.00. Test it on your eval set.

5. **Add product code caching:** Each cached HS code lookup saves one LLM call. With a good cache hit rate, you might reduce LLM costs by 20-30%.

---

## 8. Playwright E2E Testing

### 8.1 What Playwright Is (and Isn't)

Playwright is a browser automation library (by Microsoft) that controls real browser engines programmatically. It is **not** AI-agent browser testing. The key difference:

| Aspect | Playwright | AI Agent (Claude + Chrome MCP) |
|---|---|---|
| **How it works** | Deterministic scripts: "click this, type that, assert this" | AI analyzes screenshots, decides actions |
| **Speed** | ~5-30 seconds per test | Minutes per interaction |
| **Reliability** | 100% reproducible | Non-deterministic (LLM may act differently) |
| **Cost** | Free (open source) | LLM API costs per test run |
| **Headless** | Yes, by default — no browser window | Requires screenshots for the AI to "see" |
| **Best for** | Regression testing in CI/CD | Exploratory testing, ad-hoc tasks |

Playwright runs **headless** — no visible browser window, no screenshots, no AI analysis. You write a script that says "go to this URL, click this button, check that this text appears." The browser executes it deterministically.

### 8.2 How It Would Formalize Manual Testing

The manual testing we did with Chrome MCP (navigating to the app, typing a question, watching the response stream in, checking the right panel) can be encoded as a Playwright script that runs automatically on every commit:

```typescript
test('user can ask a question and see results', async ({ page }) => {
  await page.goto('/');
  await page.getByPlaceholder(/ask a question/i).fill('What did Brazil export in 2022?');
  await page.getByRole('button', { name: /send/i }).click();

  // Wait for streaming response (up to 60 seconds)
  const response = page.locator('[data-testid="assistant-message"]').last();
  await expect(response).toBeVisible({ timeout: 60000 });
  await expect(response).not.toBeEmpty();
});
```

**Two tiers of tests:**
- **Mocked backend (fast, every commit):** Mock SSE streams, test UI behavior in < 1 second per test
- **Full stack (slow, nightly):** Real backend + LLM, test end-to-end in 2-5 minutes

**Not a current priority** — address when you get to issue #39.

---

## 9. Issue Priority Discussion

### 9.1 Updated Priority Assessment

Given your decisions (cookies for persistence, Atlas DB managed externally, deployment readiness important):

**The dependency chain that matters most:**

```
Static IP setup (Cloud NAT)     ← Required for Cloud Run to reach Atlas DB on AWS
        │
Cloud Run deployment (#20)      ← Requires Dockerfile + the static IP
        │
App Database (Cloud SQL)        ← Required for conversation persistence
        │
Conversation API (#35)          ← Exposes checkpoints as REST endpoints
        │
Sidebar UI (#30)                ← Displays conversation history using localStorage UUID
```

**Deployment (#20 → #19) is the critical path** because:
1. Without deployment, nobody else can use the app
2. The static IP requirement adds complexity that should be solved early
3. Cloud Run's instance recycling behavior directly impacts your caching and persistence decisions — you need to experience it in practice
4. Setting up Cloud SQL for the app DB naturally unblocks conversation persistence

**Trade toggles (#31) are standalone** and can be done anytime without dependencies.

**Product picker (#34 + #32)** is the most complex feature and not MVP — defer as you planned.

### 9.2 Suggested Order

1. **#20 Docker + #19 Deployment** — containerize, set up Cloud Run with static IP, Cloud SQL for app DB, Firebase Hosting for frontend. This is the foundation everything else builds on.
2. **#35 + #30 Conversation history** — wire up PostgresSaver, build the API, add localStorage session ID, build the sidebar. Natural follow-up once the database exists.
3. **#31 Trade toggles** — standalone frontend feature, quick win, no dependencies.
4. **#14 Caching** — implement in-process caching first, add Upstash when needed.
5. **#39 Playwright** — formalize regression testing once features stabilize.
6. **#34 + #32 Product picker** — important for quality but highest complexity. Tackle after the app is deployed and stable.
