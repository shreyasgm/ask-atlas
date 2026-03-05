# scripts/

Ad-hoc developer scripts for testing, debugging, and experimenting with the Ask Atlas pipelines. These are **not** part of the test suite — they require real DB connections and LLM API keys, and are meant to be run manually.

## Usage

All scripts should be run from the repo root:

```bash
PYTHONPATH=$(pwd) uv run python scripts/<script_name>.py
```

## Scripts

### Pipeline smoke tests

| Script | Purpose |
|---|---|
| `smoke_test_sql_pipeline.py` | Runs 21 diverse questions through `AtlasTextToSQL` in `sql_only` mode. Records status (SUCCESS/TIMEOUT/ERROR), timing, generated SQL, and answer previews. Good for quick "did I break anything?" checks after pipeline changes. |
| `test_e2e_pipelines.py` | End-to-end tests for both SQL and GraphQL pipelines through the full agent graph. |
| `e2e_observability_test.py` | Runs representative questions through each pipeline path (SQL, GraphQL, Docs, mixed) and reports node execution order, per-node timing, tool calls, and answer snippets. |

### Debugging

| Script | Purpose |
|---|---|
| `trace_sql_pipeline.py` | Streams the SQL pipeline node-by-node for a set of queries, logging every state transition: SQL changes, validation/execution errors, retry counts, and agent decisions with timing. Use this to diagnose why a query is looping, retrying, or slow. |

### Experiments

| Script | Purpose |
|---|---|
| `reasoning_field_experiment.py` | A/B test comparing structured output schemas with vs. without a `reasoning` chain-of-thought field. Tests three call sites (GraphQL planning, docs selection, product extraction). Results in `reasoning_experiment_results.json`. See [GitHub issue #103](https://github.com/shreyasgm/ask-atlas/issues/103) for findings. |

### Infrastructure verification

| Script | Purpose |
|---|---|
| `verify_async_db.py` | Verifies dual DB engine setup (sync psycopg2 + async psycopg3), confirms async query execution, and tests concurrent query parallelism. |
