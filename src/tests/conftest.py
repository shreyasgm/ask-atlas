import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import pytest

# Define BASE_DIR
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require integration with external services (like LLM)",
    )
    config.addinivalue_line(
        "markers",
        "db: marks tests that require a live database connection",
    )
    config.addinivalue_line(
        "markers",
        "eval: marks eval-based integration tests (real LLM + production DB + LLM-as-judge)",
    )

    # Set up logging configuration for stdout only
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],  # This prints to stdout
    )


# ---------------------------------------------------------------------------
# LLM cost tracking — fires for any real API call, zero per-test setup
# ---------------------------------------------------------------------------

_session_usage_handler = None  # populated by fixture, read by hooks

# Accumulator for xdist controller: collects worker data as workers finish
_xdist_worker_data: list[dict] = []


def _handler_to_serializable(handler) -> dict:
    """Convert handler.usage_metadata to a JSON-serialisable dict.

    handler.usage_metadata is dict[str, UsageMetadata] where UsageMetadata
    is a TypedDict with int values and optional nested dicts.
    """
    result = {}
    for model_name, usage in handler.usage_metadata.items():
        entry = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        details = usage.get("input_token_details")
        if details:
            entry["input_token_details"] = {
                "cache_read": details.get("cache_read", 0) or 0,
                "cache_creation": details.get("cache_creation", 0) or 0,
            }
        result[model_name] = entry
    return result


def _usage_data_to_records(data: dict) -> list:
    """Convert serialised usage data (model→counts) to UsageRecord list."""
    from src.token_usage import make_usage_record

    records = []
    for model_name, counts in data.items():
        records.append(
            make_usage_record(
                node="test",
                tool_pipeline="test",
                input_tokens=counts.get("input_tokens", 0),
                output_tokens=counts.get("output_tokens", 0),
                total_tokens=counts.get("total_tokens", 0),
                model_name=model_name,
                input_token_details=counts.get("input_token_details"),
            )
        )
    return records


def _merge_usage_data(target: dict, source: dict) -> None:
    """Merge source usage data into target, summing token counts per model."""
    for model_name, counts in source.items():
        if model_name not in target:
            target[model_name] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
        existing = target[model_name]
        existing["input_tokens"] += counts.get("input_tokens", 0)
        existing["output_tokens"] += counts.get("output_tokens", 0)
        existing["total_tokens"] += counts.get("total_tokens", 0)
        # Merge cache details
        src_details = counts.get("input_token_details")
        if src_details:
            if "input_token_details" not in existing:
                existing["input_token_details"] = {"cache_read": 0, "cache_creation": 0}
            existing["input_token_details"]["cache_read"] += src_details.get(
                "cache_read", 0
            )
            existing["input_token_details"]["cache_creation"] += src_details.get(
                "cache_creation", 0
            )


def _print_cost_report(terminalreporter, usage_data: dict) -> None:
    """Format and print the LLM cost report table."""
    from src.token_usage import estimate_cost

    records = _usage_data_to_records(usage_data)
    if not records:
        return

    cost_info = estimate_cost(records)

    terminalreporter.write_sep("=", "LLM Cost Report")

    # Header
    header = f"{'Model':<30} {'Input Tok':>10} {'Output Tok':>11} {'Cost (USD)':>11}"
    terminalreporter.write_line(header)
    terminalreporter.write_line("-" * len(header))

    # Per-model rows
    total_input = 0
    total_output = 0
    for rec in records:
        model = rec["model_name"] or "unknown"
        inp = rec["input_tokens"]
        out = rec["output_tokens"]
        total_input += inp
        total_output += out
        # Compute per-record cost
        from src.token_usage import _estimate_single_record_cost

        row_cost = _estimate_single_record_cost(rec)
        terminalreporter.write_line(
            f"{model:<30} {inp:>10,} {out:>11,} ${row_cost:>9.3f}"
        )

    # Total row
    terminalreporter.write_line("-" * len(header))
    total_cost = cost_info["total_cost_usd"]
    terminalreporter.write_line(
        f"{'Total':<30} {total_input:>10,} {total_output:>11,} ${total_cost:>9.3f}"
    )
    terminalreporter.write_sep("=", "")


@pytest.fixture(scope="session", autouse=True)
def _track_llm_costs():
    """Session-scoped fixture that tracks LLM token usage across all tests."""
    global _session_usage_handler
    try:
        from langchain_core.callbacks import get_usage_metadata_callback
    except ImportError:
        yield
        return
    with get_usage_metadata_callback() as cb:
        _session_usage_handler = cb
        yield


# --- pytest-xdist hooks ---


def pytest_testnodedown(node, error):
    """Controller-side hook: collect cost data from each finished worker."""
    worker_data = node.workeroutput.get("llm_cost_data")
    if worker_data:
        _xdist_worker_data.append(json.loads(worker_data))


def pytest_sessionfinish(session, exitstatus):
    """Worker-side hook: serialize cost data into workeroutput for controller."""
    if not hasattr(session.config, "workerinput"):
        # Not a worker — skip (serial mode or controller)
        return
    if _session_usage_handler is None or not _session_usage_handler.usage_metadata:
        return
    data = _handler_to_serializable(_session_usage_handler)
    session.config.workeroutput["llm_cost_data"] = json.dumps(data)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print LLM cost report at the end of the test session."""
    # Determine if we're in xdist controller mode
    is_controller = hasattr(config, "pluginmanager") and config.pluginmanager.hasplugin(
        "dsession"
    )

    if is_controller:
        # Aggregate data from all workers
        if not _xdist_worker_data:
            return
        merged: dict = {}
        for worker_data in _xdist_worker_data:
            _merge_usage_data(merged, worker_data)
        _print_cost_report(terminalreporter, merged)
    else:
        # Serial mode — use handler directly
        if _session_usage_handler is None or not _session_usage_handler.usage_metadata:
            return
        data = _handler_to_serializable(_session_usage_handler)
        _print_cost_report(terminalreporter, data)


@pytest.fixture(scope="session")
def base_dir():
    return BASE_DIR


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset all in-process caches between tests for isolation."""
    from src.cache import registry

    registry.clear_all()


@pytest.fixture
def logger():
    """Fixture to provide a logger instance to tests."""
    return logging.getLogger("test_logger")


@pytest.fixture
def db_available():
    """Skip test if database is not available."""
    from src.config import get_settings

    settings = get_settings()
    if not settings.atlas_db_url:
        pytest.skip("ATLAS_DB_URL not configured in settings")
    return settings.atlas_db_url


@pytest.fixture
def checkpoint_db_url():
    """Provide the app-db URL for persistence integration tests."""
    return os.environ.get(
        "CHECKPOINT_DB_URL",
        "postgresql://ask_atlas_app:testpass@localhost:5434/ask_atlas_app",
    )
