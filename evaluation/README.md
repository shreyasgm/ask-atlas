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
```

**Options:**

| Flag | Description |
|------|-------------|
| `--questions 1 2 6` | Run specific question IDs |
| `--smoke` | Run a small curated subset for fast feedback |
| `--balanced N` | Auto-select N questions with even category/difficulty coverage |
| `--concurrency N` | Parallel agent runs |
| `--mode MODE` | Agent mode: `auto`, `sql_only`, `graphql_sql`, `graphql_only` |
| `--skip-judge` | Run agent only, skip judging |
| `--judge-model MODEL` | Override judge LLM model |
| `--judge-provider PROVIDER` | Override judge LLM provider |

## Pipeline Flow

```
eval_questions.json
        │
        ▼
  ┌─────────────┐
  │ run_eval.py  │  ← orchestrator
  └──────┬──────┘
         │
    ┌────▼─────────────────┐
    │ run_agent_evals.py   │  → runs AtlasTextToSQL agent per question
    └────┬─────────────────┘    saves: {run_dir}/{qid}/result.json
         │
    ┌────▼─────┐
    │ judge.py │  → LLM-as-judge (3 modes, see below)
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

Each question is auto-routed to one of three judge modes:

| Mode | When | What it checks |
|------|------|----------------|
| **ground_truth** | Ground truth data exists in `results/{qid}/ground_truth/` | Factual correctness (0.35), data accuracy (0.30), completeness (0.20), reasoning quality (0.15) |
| **refusal** | Question has `expected_behavior` set | Whether the agent appropriately refuses or flags limitations |
| **plausibility** | No ground truth, no expected behavior | Broad plausibility check using the judge LLM's knowledge |

**Link judging** runs separately for GraphQL questions that have a ground truth `atlas_url`. Evaluates link presence (0.35), content relevance (0.30), entity correctness (0.25), and parameter accuracy (0.10).

See `judge.py` and `link_judge.py` for current weights, dimensions, and verdict thresholds.

## Directory Structure

```
evaluation/
├── run_eval.py                  # Main orchestrator (entry point)
├── run_agent_evals.py           # Agent execution engine
├── judge.py                     # Answer quality judge
├── link_judge.py                # Atlas link quality judge
├── report.py                    # JSON + Markdown report generation
├── html_report.py               # Interactive HTML report
├── review_server.py             # Local server for GT review/correction
├── compare_runs.py              # Regression detection between runs
├── execution_accuracy.py        # SQL accuracy comparison
├── utils.py                     # Shared utilities
│
├── eval_questions.json          # Question corpus with categories & difficulty levels
├── setup_questions.py           # One-time question directory setup
│
├── questions/{qid}/             # Per-question metadata
├── results/{qid}/ground_truth/  # Ground truth data (shared across runs)
│   ├── results.json             #   Data rows + atlas_url
│   ├── execution_log.json       #   Collection metadata
│   └── browser_verification.json
│
├── runs/                        # Timestamped evaluation runs
│   ├── history.jsonl            #   Cumulative run history
│   └── {timestamp}/
│       ├── summary.json         #   Run metadata & agent results
│       ├── report.json          #   Structured eval results
│       ├── report.md            #   Human-readable report
│       ├── report.html          #   Interactive dashboard
│       └── {qid}/result.json   #   Per-question agent output
│
├── collect_country_page_data.py # Ground truth collection: Country Pages API
├── collect_explore_page_data.py # Ground truth collection: Explore Pages API
│
├── evaluation_strategy.md       # Comprehensive evaluation framework doc
└── *.md                         # API references & collection guides
```

## Question Corpus

Questions in `eval_questions.json` span three difficulty levels (easy/medium/hard) and are organized into category groups:

- **SQL-based**: total exports, sectoral composition, trade partners, growth/performance, complexity, diversification, edge cases, out-of-scope, data boundaries
- **Country Page**: profile overview, total exports, sectoral composition, bilateral trade, growth dynamics, complexity, products
- **Explore Page**: product-level metrics, regional aggregates, opportunity products, visualization types

Each question includes an `id`, `text`, `category_id`, `difficulty`, and optional fields like `expected_behavior` (for refusal/edge-case questions), `expected_classification`, and `expected_api_target`.

## Ground Truth

Ground truth was assembled by navigating the [Atlas website](https://atlas.hks.harvard.edu/) and mapping the data points shown on its pages to the kinds of questions users are likely to ask the Ask Atlas system. The collected data lives in `results/{qid}/ground_truth/results.json`.

Collection scripts (`collect_country_page_data.py`, `collect_explore_page_data.py`) automate retrieval from the Atlas APIs. Questions without ground truth fall back to plausibility judging.

**Updating ground truth:** Use the review server for interactive correction:

```bash
PYTHONPATH=$(pwd) uv run python evaluation/review_server.py --run {timestamp} --port 8777
```

This serves a web UI to classify questions, correct data/URLs, and re-judge individual questions with an audit trail.

## Run Outputs

Each run creates a timestamped directory under `runs/` containing:

- **summary.json** — Agent model, provider, mode, per-question results (answer, SQL, tools used, token usage, step timing, tool call history)
- **report.json** — Aggregate scores, per-dimension averages, breakdowns by category/difficulty, latency/cost analysis, budget violations
- **report.md** — Human-readable markdown summary
- **report.html** — Self-contained interactive dashboard with filters and expandable question cards

Run summaries are appended to `runs/history.jsonl` for trend analysis across runs.

## Comparing Runs

```bash
PYTHONPATH=$(pwd) python evaluation/compare_runs.py {timestamp_a} {timestamp_b}
```

## Pytest Integration

```bash
# Eval-based integration tests (needs API keys + DB)
PYTHONPATH=$(pwd) uv run pytest -m "eval" -v
```

Runs a curated subset through the full pipeline (agent -> judge -> verdict) as part of the test suite. See `src/tests/test_eval_integration.py`.
