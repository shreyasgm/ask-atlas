#!/usr/bin/env python3
"""Local review server for evaluating and correcting ground truth data.

Serves an enhanced HTML report with inline classification, GT editing,
and re-judging capabilities.

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/review_server.py --run 20260301T120000Z
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from html_report import generate_review_html
from judge import judge_answer
from link_judge import judge_links
from pydantic import BaseModel
from utils import (
    EVALUATION_BASE_DIR,
    get_timestamp,
    load_json_file,
    logging,
    save_json_file,
)

# ---------------------------------------------------------------------------
# CLI & globals
# ---------------------------------------------------------------------------

app = FastAPI(title="Ask-Atlas Review Server")


def _resolve_run_dir(run_name: str) -> Path:
    """Resolve a run directory from a timestamp name."""
    run_dir = EVALUATION_BASE_DIR / "runs" / run_name
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")
    if not (run_dir / "report.json").exists():
        raise FileNotFoundError(f"No report.json in {run_dir}")
    return run_dir


# Set at startup by main()
RUN_DIR: Path | None = None


def _get_run_dir() -> Path:
    if RUN_DIR is None:
        raise RuntimeError("RUN_DIR not initialized")
    return RUN_DIR


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    classification: str
    note: str | None = None


class CorrectGTDataRequest(BaseModel):
    data: list[dict[str, Any]]
    note: str


class CorrectGTUrlRequest(BaseModel):
    atlas_url: str
    note: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_reviewer() -> dict[str, str]:
    """Get reviewer identity from git config."""
    name = ""
    email = ""
    try:
        name = (
            subprocess.check_output(
                ["git", "config", "user.name"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        pass
    try:
        email = (
            subprocess.check_output(
                ["git", "config", "user.email"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        pass
    display = name or email or "anonymous"
    return {"name": name, "email": email, "display": display}


def _gt_path(qid: str) -> Path:
    """Return the ground truth results.json path for a question."""
    return EVALUATION_BASE_DIR / "results" / qid / "ground_truth" / "results.json"


def _load_report() -> dict[str, Any]:
    """Load report.json for the current run."""
    return load_json_file(_get_run_dir() / "report.json")


def _save_report(report: dict[str, Any]) -> None:
    """Save report.json for the current run."""
    save_json_file(_get_run_dir() / "report.json", report)


def _find_question_entry(report: dict[str, Any], qid: str) -> dict[str, Any] | None:
    """Find a per_question entry by question_id."""
    for entry in report.get("per_question", []):
        if str(entry.get("question_id")) == str(qid):
            return entry
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def serve_report() -> str:
    """Serve the enhanced review HTML report."""
    return generate_review_html(_get_run_dir())


@app.get("/api/reviewer")
async def get_reviewer() -> dict[str, str]:
    """Return reviewer identity from git config."""
    return _get_reviewer()


@app.post("/api/classify/{qid}")
async def classify_question(qid: str, req: ClassifyRequest) -> dict[str, Any]:
    """Save a classification to report.json for a question."""
    report = _load_report()
    entry = _find_question_entry(report, qid)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    reviewer = _get_reviewer()
    entry["review"] = {
        "classification": req.classification,
        "note": req.note,
        "reviewed_by": reviewer["display"],
        "reviewed_at": get_timestamp(),
    }
    _save_report(report)
    logging.info(f"Question {qid}: classified as {req.classification}")
    return {"status": "ok", "review": entry["review"]}


@app.post("/api/correct-gt/{qid}")
async def correct_gt_data(qid: str, req: CorrectGTDataRequest) -> dict[str, Any]:
    """Archive old GT data and save corrected data."""
    gt_file = _gt_path(qid)
    if not gt_file.exists():
        raise HTTPException(status_code=404, detail=f"No GT file for question {qid}")

    gt = load_json_file(gt_file)
    reviewer = _get_reviewer()

    # Archive current data entries
    if "data_archived" not in gt:
        gt["data_archived"] = []
    old_data = gt.get("results", {}).get("data", [])
    for row in old_data:
        archived_entry = dict(row)
        archived_entry["archived_at"] = get_timestamp()
        archived_entry["archived_by"] = reviewer["display"]
        archived_entry["note"] = req.note
        gt["data_archived"].append(archived_entry)

    # Set new data
    if "results" not in gt:
        gt["results"] = {}
    gt["results"]["data"] = req.data
    gt["execution_timestamp"] = get_timestamp()

    save_json_file(gt_file, gt)
    logging.info(f"Question {qid}: GT data corrected ({len(req.data)} rows)")
    return {"status": "ok", "archived_count": len(old_data), "new_count": len(req.data)}


@app.post("/api/correct-gt-url/{qid}")
async def correct_gt_url(qid: str, req: CorrectGTUrlRequest) -> dict[str, Any]:
    """Archive old atlas_url and save new URL."""
    gt_file = _gt_path(qid)
    if not gt_file.exists():
        raise HTTPException(status_code=404, detail=f"No GT file for question {qid}")

    gt = load_json_file(gt_file)
    reviewer = _get_reviewer()

    # Archive old URL if it exists
    old_url = gt.get("atlas_url")
    if old_url:
        if "data_archived" not in gt:
            gt["data_archived"] = []
        gt["data_archived"].append(
            {
                "type": "atlas_url_archived",
                "value": old_url,
                "archived_at": get_timestamp(),
                "archived_by": reviewer["display"],
                "note": req.note,
            }
        )

    gt["atlas_url"] = req.atlas_url
    save_json_file(gt_file, gt)
    logging.info(f"Question {qid}: GT URL corrected → {req.atlas_url}")
    return {"status": "ok", "old_url": old_url, "new_url": req.atlas_url}


@app.post("/api/rejudge/{qid}")
async def rejudge_question(qid: str) -> dict[str, Any]:
    """Re-run judge(s) on a question with current GT and update report.json."""
    run_dir = _get_run_dir()
    report = _load_report()
    entry = _find_question_entry(report, qid)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Question {qid} not found")

    # Load agent result
    result_path = run_dir / qid / "result.json"
    if not result_path.exists():
        raise HTTPException(
            status_code=404, detail=f"No result.json for question {qid}"
        )
    result = load_json_file(result_path)

    if result.get("status") != "success" or not result.get("answer"):
        raise HTTPException(
            status_code=400, detail="Agent did not produce a successful answer"
        )

    # Load eval question metadata for expected_behavior / expected_classification
    eq_path = EVALUATION_BASE_DIR / "eval_questions.json"
    eq_data = load_json_file(eq_path) if eq_path.exists() else {}
    meta: dict[str, Any] = {}
    for q in eq_data.get("questions", []):
        if str(q["id"]) == str(qid):
            meta = q
            break

    expected_behavior = meta.get("expected_behavior")

    # Load current GT
    gt_file = _gt_path(qid)
    ground_truth = None
    atlas_url = None
    if gt_file.exists():
        gt = load_json_file(gt_file)
        data = gt.get("results", {}).get("data", [])
        ground_truth = data if data else None
        atlas_url = gt.get("atlas_url")

    # Build classification note
    classification_note = None
    if meta.get("expected_classification"):
        classification_note = (
            "Note: The ground truth was collected from the Atlas Country Pages API using "
            "HS 1992 (HS92) product classification. The Country Pages API only supports HS92. "
            "If the agent's answer uses different product names or codes (e.g., from HS 2012), "
            "this may explain discrepancies in product-specific data."
        )

    # Use judge model/provider from original report metadata
    judge_model = report.get("judge_model", "gpt-5-mini")
    judge_provider = report.get("judge_provider", "openai")
    tools_used = result.get("tools_used", [])
    question_text = meta.get(
        "text", result.get("question_text", entry.get("question_text", ""))
    )

    # Run answer judge
    verdict = await judge_answer(
        question=question_text,
        agent_answer=result["answer"],
        ground_truth_data=ground_truth,
        expected_behavior=expected_behavior,
        model=judge_model,
        provider=judge_provider,
        tools_used=tools_used,
        classification_note=classification_note,
    )
    logging.info(
        f"Question {qid}: re-judged → verdict={verdict.get('verdict')} "
        f"score={verdict.get('weighted_score')}"
    )

    # Update report entry with new verdict
    entry["verdict"] = verdict.get("verdict")
    entry["weighted_score"] = verdict.get("weighted_score")
    entry["pass_count"] = verdict.get("pass_count")
    entry["judge_mode"] = verdict.get("judge_mode")
    entry["judge_comment"] = verdict.get("overall_comment", "")
    entry["judge_details"] = verdict

    # Link judge if applicable
    link_verdict = None
    used_graphql = tools_used and "atlas_graphql" in tools_used
    agent_links = result.get("graphql_atlas_links", [])
    if used_graphql and atlas_url:
        try:
            link_verdict = await judge_links(
                question=question_text,
                agent_links=agent_links,
                ground_truth_url=atlas_url,
                model=judge_model,
                provider=judge_provider,
            )
            entry["link_judge"] = link_verdict
            logging.info(
                f"Question {qid}: link re-judged → verdict={link_verdict.get('verdict')} "
                f"score={link_verdict.get('weighted_score')}"
            )
        except Exception as e:
            logging.error(f"Question {qid}: link re-judge error — {e}")

    _save_report(report)

    return {
        "status": "ok",
        "verdict": verdict.get("verdict"),
        "pass_count": verdict.get("pass_count"),
        "weighted_score": verdict.get("weighted_score"),
        "judge_details": verdict,
        "link_verdict": link_verdict,
    }


@app.delete("/api/question/{qid}")
async def delete_question(qid: str) -> dict[str, Any]:
    """Permanently delete a question from eval_questions.json, its results folder, and the run report.

    This removes:
    1. The question entry from eval_questions.json
    2. The results/{qid}/ folder (ground truth files)
    3. The question from the current run's report.json (per_question list)
    """
    # 1. Remove from eval_questions.json
    eq_path = EVALUATION_BASE_DIR / "eval_questions.json"
    if not eq_path.exists():
        raise HTTPException(status_code=500, detail="eval_questions.json not found")

    eq_data = load_json_file(eq_path)
    original_count = len(eq_data.get("questions", []))
    eq_data["questions"] = [
        q for q in eq_data.get("questions", []) if str(q["id"]) != str(qid)
    ]
    new_count = len(eq_data["questions"])

    if new_count == original_count:
        raise HTTPException(
            status_code=404, detail=f"Question {qid} not found in eval_questions.json"
        )

    save_json_file(eq_path, eq_data)
    logging.info(
        f"Question {qid}: removed from eval_questions.json ({original_count} → {new_count})"
    )

    # 2. Remove results folder
    results_dir = EVALUATION_BASE_DIR / "results" / str(qid)
    results_deleted = False
    if results_dir.exists():
        shutil.rmtree(results_dir)
        results_deleted = True
        logging.info(f"Question {qid}: deleted results folder {results_dir}")

    # 3. Remove from current run's report.json
    report = _load_report()
    report["per_question"] = [
        q
        for q in report.get("per_question", [])
        if str(q.get("question_id")) != str(qid)
    ]
    _save_report(report)
    logging.info(f"Question {qid}: removed from run report")

    return {
        "status": "ok",
        "question_id": qid,
        "eval_questions_count": new_count,
        "results_deleted": results_deleted,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask-Atlas GT Review Server")
    parser.add_argument(
        "--run",
        required=True,
        help="Timestamp name of the run directory (e.g. 20260301T120000Z)",
    )
    parser.add_argument("--port", type=int, default=8777, help="Port to serve on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    global RUN_DIR
    RUN_DIR = _resolve_run_dir(args.run)
    logging.info(f"Review server for run: {RUN_DIR}")
    logging.info(f"Serving at http://{args.host}:{args.port}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
