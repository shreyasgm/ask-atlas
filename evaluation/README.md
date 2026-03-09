# Evaluation Pipeline

End-to-end evaluation harness for the Ask Atlas agent. Runs questions through the agent, judges answers against ground truth, and generates interactive reports.

## Quick Start

```bash
# Smoke test (curated subset, fastest feedback)
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --smoke

# Run specific questions
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --questions 1 2 6

# Balanced sample across categories/difficulties
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --balanced 30

# Full run (all questions)
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py

# Re-judge an existing run (no agent re-run)
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --rejudge 20260308T023103Z

# Generate web research ground truth concurrently with agent evals
PYTHONPATH=$(pwd) uv run python evaluation/run_eval.py --generate-web-research
```

**Options:**

| Flag | Description |
|------|-------------|
| `--questions 1 2 6` | Run specific question IDs |
| `--smoke` | Run a small curated subset for fast feedback |
| `--balanced N` | Auto-select N questions with even category/difficulty coverage |
| `--concurrency N` | Parallel agent runs (default: 10) |
| `--mode MODE` | Agent mode: `auto`, `sql_only`, `graphql_sql`, `graphql_only` |
| `--skip-judge` | Run agent only, skip judging |
| `--judge-model MODEL` | Override judge LLM model (default: gpt-5-mini) |
| `--judge-provider PROVIDER` | Override judge LLM provider (default: openai) |
| `--rejudge RUN_ID` | Re-judge an existing run without re-running the agent |
| `--generate-web-research` | Auto-generate web research ground truth for questions lacking SQL ground truth |
| `--web-research-provider` | Provider for web research generation: `openai` or `anthropic` (default: openai) |
| `--web-research-model` | Model for web research generation (default: provider-specific) |

## Pipeline Flow

```
eval_questions.json
        │
        ▼
  ┌─────────────┐
  │ run_eval.py  │  ← orchestrator
  └──────┬──────┘
         │
    ┌────▼─────────────────┐     ┌──────────────────────────────┐
    │ run_agent_evals.py   │     │ generate_web_ground_truth.py │  (parallel, optional)
    └────┬─────────────────┘     └──────────────┬───────────────┘
         │  saves: {run_dir}/{qid}/result.json  │  saves: results/{qid}/ground_truth/web_research.json
         ├──────────────────────────────────────-┘
    ┌────▼─────┐
    │ judge.py │  → LLM-as-judge (4 modes, see below)
    └────┬─────┘
         │
    ┌────▼──────────┐
    │ link_judge.py │  → evaluates Atlas URLs (GraphQL questions only)
    └────┬──────────┘
         │
    ┌────▼────────────────────┐
    │ report.py / html_report │  → JSON + Markdown + interactive HTML
    └────┬────────────────────┘
         │
         ▼
  runs/history.jsonl  ← one-line summary appended per run
```

## Judging Modes

Each question is auto-routed to one of four judge modes:

| Mode | When | What it checks |
|------|------|----------------|
| **ground_truth** | Ground truth data exists in `results/{qid}/ground_truth/results.json` | Factual correctness (0.35), data accuracy (0.30), completeness (0.20), reasoning quality (0.15) |
| **refusal** | Question has `expected_behavior` set | Whether the agent appropriately refuses or flags limitations |
| **web_research** | No SQL ground truth, but `web_research.json` exists | Scores against LLM-researched reference answers |
| **plausibility** | No ground truth, no expected behavior, no web research | Broad plausibility check using the judge LLM's knowledge |

**Verdicts:** pass (≥3 dimensions pass), partial (2 dimensions or critical failure), fail (<2).

**Link judging** runs separately for GraphQL questions that have a ground truth `atlas_url`. Evaluates link presence (0.35), content relevance (0.30), entity correctness (0.25), and parameter accuracy (0.10).

See `judge.py` and `link_judge.py` for current weights, dimensions, and verdict thresholds.

## Directory Structure

```
evaluation/
├── run_eval.py                     # Main orchestrator (entry point)
├── run_agent_evals.py              # Agent execution engine
├── judge.py                        # Answer quality judge (4 modes)
├── link_judge.py                   # Atlas link quality judge
├── report.py                       # JSON + Markdown report generation
├── html_report.py                  # Interactive HTML report
├── utils.py                        # Shared utilities
│
├── eval_questions.json             # Question corpus with categories & difficulty levels
├── feedback_candidates.json        # Candidate questions from user feedback (staging area)
├── setup_questions.py              # One-time question directory setup
│
├── review_server.py                # Local server for GT review/correction
├── compare_runs.py                 # Regression detection between two runs
├── compare_cohorts.py              # Performance comparison across question cohorts
├── execution_accuracy.py           # SQL accuracy comparison between runs
│
├── collect_country_page_data.py    # Ground truth collection: Country Pages API
├── collect_explore_page_data.py    # Ground truth collection: Explore Pages API
├── generate_ground_truth.py        # Execute verified SQL to generate ground truth
├── generate_web_ground_truth.py    # LLM-powered web research ground truth
│
├── feedback_to_eval.py             # Convert negative user feedback → candidate eval questions
├── promote_feedback.py             # Promote approved candidates into eval_questions.json
├── test_feedback_to_eval.py        # Tests for feedback_to_eval
│
├── questions/{qid}/                # Per-question metadata
├── results/{qid}/ground_truth/     # Ground truth data (shared across runs)
│   ├── results.json                #   Data rows + atlas_url
│   ├── web_research.json           #   LLM-researched reference answers
│   ├── execution_log.json          #   Collection metadata
│   └── browser_verification.json
│
├── runs/                           # Timestamped evaluation runs
│   ├── history.jsonl               #   Cumulative run history
│   └── {timestamp}/
│       ├── summary.json            #   Run metadata & agent results
│       ├── report.json             #   Structured eval results
│       ├── report.md               #   Human-readable report
│       ├── report.html             #   Interactive dashboard
│       └── {qid}/result.json       #   Per-question agent output
│
├── evaluation_strategy.md          # Comprehensive evaluation framework doc
├── country_page_collection_guide.md
├── explore_page_collection_guide.md
├── atlas_country_pages_exploration.md
├── atlas_explore_pages_exploration.md
├── graphql_api_official_docs.md    # GraphQL API reference
├── graphql_pipeline_analysis.md    # Analysis of GraphQL pipeline behavior
└── system_prompt.md                # Agent system prompt reference
```

## Question Corpus

Questions in `eval_questions.json` span three difficulty levels (easy/medium/hard) and are organized into category groups:

- **SQL-based**: total exports, sectoral composition, trade partners, growth/performance, complexity, diversification, edge cases, out-of-scope, data boundaries
- **Country Page**: profile overview, total exports, sectoral composition, bilateral trade, growth dynamics, complexity, products, strategies, opportunities
- **Explore Page**: product-level metrics, regional aggregates, opportunity products, visualization types

Each question includes an `id`, `text`, `category_id`, `difficulty`, and optional fields like `expected_behavior` (for refusal/edge-case questions), `expected_classification`, and `expected_api_target`.

## Ground Truth

Ground truth was assembled by navigating the [Atlas website](https://atlas.hks.harvard.edu/) and mapping the data points shown on its pages to the kinds of questions users are likely to ask the Ask Atlas system. The collected data lives in `results/{qid}/ground_truth/results.json`.

**Collection methods:**

- **API scraping** — `collect_country_page_data.py` and `collect_explore_page_data.py` automate retrieval from Atlas APIs
- **Verified SQL** — `generate_ground_truth.py` executes manually reviewed SQL queries against the Atlas database
- **Web research** — `generate_web_ground_truth.py` uses LLMs with web search to produce reference answers for questions without SQL ground truth

Questions without any ground truth fall back to plausibility judging.

**Updating ground truth:** Use the review server for interactive correction:

```bash
PYTHONPATH=$(pwd) uv run python evaluation/review_server.py --run {timestamp} --port 8777
```

This serves a web UI to classify questions, correct data/URLs, and re-judge individual questions with an audit trail.

## Feedback Loop

User feedback from the production app can be converted into eval questions:

```bash
# Pull negative feedback and generate candidate eval questions
uv run python evaluation/feedback_to_eval.py

# Review candidates in feedback_candidates.json, then promote approved ones
uv run python evaluation/promote_feedback.py --ids 42 43 55
uv run python evaluation/promote_feedback.py --all        # promote all
uv run python evaluation/promote_feedback.py --ids 42 --dry-run
```

## Run Outputs

Each run creates a timestamped directory under `runs/` containing:

- **summary.json** — Agent model, provider, mode, per-question results (answer, SQL, tools used, token usage, step timing, tool call history)
- **report.json** — Aggregate scores, per-dimension averages, breakdowns by category/difficulty, latency/cost analysis, budget violations
- **report.md** — Human-readable markdown summary
- **report.html** — Self-contained interactive dashboard with filters and expandable question cards

Run summaries are appended to `runs/history.jsonl` for trend analysis across runs.

## Comparing Runs

```bash
# Compare two runs side-by-side (regression detection)
PYTHONPATH=$(pwd) python evaluation/compare_runs.py {timestamp_a} {timestamp_b}

# List available runs
PYTHONPATH=$(pwd) python evaluation/compare_runs.py --list

# Compare performance across predefined question cohorts
PYTHONPATH=$(pwd) uv run python evaluation/compare_cohorts.py {new_run_id}
PYTHONPATH=$(pwd) uv run python evaluation/compare_cohorts.py {new_run_id} --baseline {baseline_id} --save
```

## Pytest Integration

```bash
# Eval-based integration tests (needs API keys + DB)
PYTHONPATH=$(pwd) uv run pytest -m "eval" -v
```

Runs a curated subset through the full pipeline (agent → judge → verdict) as part of the test suite. See `src/tests/test_eval_integration.py`, `src/tests/test_eval_report.py`, and `src/tests/test_eval_guardrails.py`.
