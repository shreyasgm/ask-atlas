#!/usr/bin/env python3
"""Generate a self-contained interactive HTML evaluation report.

Merges data from report.json, per-question result.json files, ground truth,
and eval_questions.json into a single HTML file with:
- Dashboard header with aggregate stats
- Filter bar (category, difficulty, verdict, judge mode, text search)
- Breakdown tabs (by category, by difficulty, by judge mode)
- Color-coded expandable question cards with full details

Usage:
    PYTHONPATH=$(pwd) uv run python evaluation/html_report.py evaluation/runs/20260227T133638Z/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from utils import EVALUATION_BASE_DIR, load_json_file, logging

logger = logging.getLogger(__name__)


def _load_enriched_data(run_dir: Path) -> dict[str, Any]:
    """Merge report.json with per-question results and ground truth.

    Returns a single dict ready to embed as JSON in the HTML.
    """
    report = load_json_file(run_dir / "report.json")

    # Load eval_questions.json for expected_behavior text
    eq_path = EVALUATION_BASE_DIR / "eval_questions.json"
    eq_data = load_json_file(eq_path) if eq_path.exists() else {}
    expected_behaviors: dict[str, str] = {}
    for q in eq_data.get("questions", []):
        if q.get("expected_behavior"):
            expected_behaviors[str(q["id"])] = q["expected_behavior"]

    # Enrich per-question entries
    for entry in report.get("per_question", []):
        qid = entry["question_id"]

        # Load per-question result.json (agent answer, SQL, tools_used)
        result_path = run_dir / qid / "result.json"
        if result_path.exists():
            result = load_json_file(result_path)
            entry["agent_answer"] = result.get("answer", "")
            entry["sql"] = result.get("sql", "")
            entry["tools_used"] = result.get("tools_used", [])
            entry["agent_mode"] = result.get("agent_mode", "")
            entry["step_timing"] = result.get("step_timing", [])
            # Rich observability fields
            entry["sql_history"] = result.get("sql_history", [])
            entry["pipeline_products"] = result.get("pipeline_products")
            entry["pipeline_result_columns"] = result.get("pipeline_result_columns", [])
            entry["pipeline_result_rows"] = result.get("pipeline_result_rows", [])
            entry["graphql_query"] = result.get("graphql_query")
            entry["graphql_classification"] = result.get("graphql_classification")
            entry["graphql_entity_extraction"] = result.get("graphql_entity_extraction")
            entry["graphql_resolved_params"] = result.get("graphql_resolved_params")
            entry["graphql_atlas_links"] = result.get("graphql_atlas_links", [])
            entry["graphql_api_target"] = result.get("graphql_api_target")
            entry["graphql_call_history"] = result.get("graphql_call_history", [])
            entry["sql_call_history"] = result.get("sql_call_history", [])
            entry["docs_selected_files"] = result.get("docs_selected_files", [])
            entry["tool_call_details"] = result.get("tool_call_details", [])
        else:
            entry.setdefault("agent_answer", "")
            entry.setdefault("sql", "")
            entry.setdefault("tools_used", [])
            entry.setdefault("agent_mode", "")
            entry.setdefault("step_timing", [])

        # Load ground truth
        gt_path = (
            EVALUATION_BASE_DIR / "results" / qid / "ground_truth" / "results.json"
        )
        if gt_path.exists():
            try:
                gt = load_json_file(gt_path)
                entry["ground_truth"] = gt.get("results", {}).get("data", [])
                entry["ground_truth_atlas_url"] = gt.get("atlas_url", "")
            except Exception:
                entry["ground_truth"] = None
                entry["ground_truth_atlas_url"] = ""
        else:
            entry["ground_truth"] = None
            entry["ground_truth_atlas_url"] = ""

        # Load web research
        wr_path = (
            EVALUATION_BASE_DIR / "results" / qid / "ground_truth" / "web_research.json"
        )
        if wr_path.exists():
            try:
                wr = load_json_file(wr_path)
                entry["web_research"] = {
                    "research_answer": wr.get("research_answer", ""),
                    "sources": wr.get("sources", []),
                    "confidence": wr.get("confidence", ""),
                    "provider": wr.get("provider", ""),
                    "model": wr.get("model", ""),
                }
            except Exception:
                entry["web_research"] = None
        else:
            entry["web_research"] = None

        # Load paper research
        pr_path = (
            EVALUATION_BASE_DIR / "results" / qid / "ground_truth" / "paper_research.json"
        )
        if pr_path.exists():
            try:
                pr = load_json_file(pr_path)
                entry["paper_research"] = {
                    "research_answer": pr.get("research_answer", ""),
                    "supporting_quotes": pr.get("supporting_quotes", []),
                    "data_points": pr.get("data_points", []),
                    "confidence": pr.get("confidence", ""),
                    "paper_title": pr.get("paper_title", ""),
                    "paper_year": pr.get("paper_year"),
                }
            except Exception:
                entry["paper_research"] = None
        else:
            entry["paper_research"] = None

        # Add expected_behavior for refusal questions
        if qid in expected_behaviors:
            entry["expected_behavior"] = expected_behaviors[qid]

    return report


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ask Atlas — Evaluation Report</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
/* ---------- Reset & base ---------- */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen,
               Ubuntu, Cantarell, sans-serif;
  background: #f8fafc; color: #1e293b; line-height: 1.5;
  padding: 24px; max-width: 1200px; margin: 0 auto;
}

/* ---------- Dashboard ---------- */
.dashboard {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px; margin-bottom: 24px;
}
.stat-card {
  background: #fff; border-radius: 10px; padding: 16px 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.stat-card .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .5px; }
.stat-card .value { font-size: 26px; font-weight: 700; margin-top: 4px; }
.stat-card .sub { font-size: 13px; color: #94a3b8; margin-top: 2px; }

/* ---------- Filter bar ---------- */
.filter-bar {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  margin-bottom: 20px; padding: 12px 16px; background: #fff;
  border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.filter-bar select, .filter-bar input {
  padding: 6px 10px; border: 1px solid #e2e8f0; border-radius: 6px;
  font-size: 14px; background: #fff; color: #1e293b;
}
.filter-bar input { flex: 1; min-width: 200px; }
.filter-bar .count { margin-left: auto; font-size: 13px; color: #64748b; }

/* ---------- Breakdown tabs ---------- */
.tabs { display: flex; gap: 0; margin-bottom: 16px; }
.tabs button {
  padding: 8px 18px; border: 1px solid #e2e8f0; background: #fff;
  cursor: pointer; font-size: 13px; color: #64748b;
  transition: all .15s;
}
.tabs button:first-child { border-radius: 8px 0 0 8px; }
.tabs button:last-child { border-radius: 0 8px 8px 0; }
.tabs button.active { background: #1e293b; color: #fff; border-color: #1e293b; }
.breakdown-table {
  width: 100%; border-collapse: collapse; margin-bottom: 24px;
  background: #fff; border-radius: 10px; overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}
.breakdown-table th {
  text-align: left; padding: 10px 14px; font-size: 12px;
  text-transform: uppercase; letter-spacing: .5px; color: #64748b;
  border-bottom: 2px solid #e2e8f0; background: #f8fafc;
}
.breakdown-table td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }
.breakdown-table tr:last-child td { border-bottom: none; }

/* ---------- Question cards ---------- */
.question-card {
  background: #fff; border-radius: 10px; margin-bottom: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,.06); border-left: 4px solid #94a3b8;
  overflow: hidden; transition: box-shadow .15s;
}
.question-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,.1); }
.question-card.pass { border-left-color: #22c55e; }
.question-card.partial { border-left-color: #eab308; }
.question-card.fail { border-left-color: #ef4444; }
.card-header {
  display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  cursor: pointer; user-select: none;
}
.card-header .icon { font-size: 18px; flex-shrink: 0; }
.card-header .qid { font-weight: 700; color: #475569; min-width: 38px; }
.card-header .text { flex: 1; font-size: 14px; }
.card-header .badges { display: flex; gap: 6px; flex-shrink: 0; flex-wrap: wrap; }
.badge {
  padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600;
}
.badge.easy { background: #dcfce7; color: #166534; }
.badge.medium { background: #fef9c3; color: #854d0e; }
.badge.hard { background: #fee2e2; color: #991b1b; }
.badge.cat { background: #e0e7ff; color: #3730a3; }
.badge.score { background: #f1f5f9; color: #334155; }
.badge.verdict-pass { background: #dcfce7; color: #166534; }
.badge.verdict-partial { background: #fef9c3; color: #854d0e; }
.badge.verdict-fail { background: #fee2e2; color: #991b1b; }
.badge.mode { background: #faf5ff; color: #7c3aed; }

/* ---------- Expanded detail ---------- */
.card-detail { display: none; padding: 0 16px 16px; }
.card-detail.open { display: block; }
.detail-section { margin-top: 14px; }
.detail-section h4 {
  font-size: 12px; text-transform: uppercase; letter-spacing: .5px;
  color: #64748b; margin-bottom: 6px;
}
.detail-section .content {
  background: #f8fafc; border-radius: 8px; padding: 12px 16px;
  font-size: 14px; overflow-x: auto;
}
.detail-section .content table {
  border-collapse: collapse; width: 100%; font-size: 13px;
}
.detail-section .content table th {
  text-align: left; padding: 6px 10px; border-bottom: 2px solid #e2e8f0;
  font-size: 12px; color: #64748b; background: #f1f5f9;
}
.detail-section .content table td {
  padding: 5px 10px; border-bottom: 1px solid #e2e8f0;
}
.detail-section pre {
  background: #1e293b; color: #e2e8f0; padding: 12px 16px;
  border-radius: 8px; overflow-x: auto; font-size: 13px;
  line-height: 1.5; white-space: pre-wrap; word-break: break-all;
}
.dim-bars { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
.dim-bar { display: flex; align-items: center; gap: 8px; }
.dim-bar .dim-label { font-size: 12px; color: #64748b; min-width: 140px; }
.dim-bar .bar-bg {
  flex: 1; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden;
}
.dim-bar .bar-fill { height: 100%; border-radius: 4px; transition: width .3s; }
.dim-bar .bar-fill.high { background: #22c55e; }
.dim-bar .bar-fill.mid { background: #eab308; }
.dim-bar .bar-fill.low { background: #ef4444; }
.dim-bar .dim-score { font-size: 12px; font-weight: 600; min-width: 30px; }

/* rendered markdown */
.md-rendered h1, .md-rendered h2, .md-rendered h3 { margin: 10px 0 6px; }
.md-rendered p { margin: 6px 0; }
.md-rendered table { border-collapse: collapse; margin: 8px 0; }
.md-rendered table th, .md-rendered table td {
  border: 1px solid #e2e8f0; padding: 4px 8px; font-size: 13px;
}
.md-rendered code { background: #e2e8f0; padding: 1px 4px; border-radius: 3px; font-size: 13px; }
.md-rendered pre code { background: none; padding: 0; }
.md-rendered ul, .md-rendered ol { padding-left: 20px; margin: 6px 0; }
.md-rendered strong { font-weight: 600; }

.no-results {
  text-align: center; padding: 40px; color: #94a3b8; font-size: 16px;
}

/* ---------- Debug drawer ---------- */
.debug-toggle {
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .5px;
  margin-top: 16px; padding: 6px 0; user-select: none; border: none; background: none;
}
.debug-toggle::before {
  content: ''; display: inline-block; width: 0; height: 0;
  border-left: 5px solid #94a3b8; border-top: 4px solid transparent;
  border-bottom: 4px solid transparent; transition: transform .15s;
}
.debug-toggle.open::before { transform: rotate(90deg); }
.debug-drawer { display: none; }
.debug-drawer.open { display: block; }

/* ---------- Tools badges ---------- */
.tools-list { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.tool-badge {
  padding: 2px 6px; border-radius: 4px; font-size: 11px;
  background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0;
}

/* ---------- Step timing waterfall ---------- */
.waterfall { display: flex; flex-direction: column; gap: 4px; }
.waterfall-row { display: flex; align-items: center; gap: 8px; }
.waterfall-label {
  font-size: 12px; color: #475569; min-width: 160px; text-align: right;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.waterfall-bar-bg {
  flex: 1; height: 16px; background: #f1f5f9; border-radius: 3px;
  overflow: hidden; position: relative;
}
.waterfall-bar {
  height: 100%; border-radius: 3px; display: flex; position: absolute; top: 0; left: 0;
}
.waterfall-bar .wf-llm { background: #6366f1; height: 100%; }
.waterfall-bar .wf-io { background: #06b6d4; height: 100%; }
.waterfall-bar .wf-overhead { background: #94a3b8; height: 100%; }
.waterfall-time {
  font-size: 11px; color: #64748b; min-width: 50px; text-align: right;
}
.waterfall-legend {
  display: flex; gap: 12px; margin-top: 6px; font-size: 11px; color: #64748b;
}
.waterfall-legend span::before {
  content: ''; display: inline-block; width: 10px; height: 10px;
  border-radius: 2px; margin-right: 4px; vertical-align: middle;
}
.waterfall-legend .leg-llm::before { background: #6366f1; }
.waterfall-legend .leg-io::before { background: #06b6d4; }
.waterfall-legend .leg-overhead::before { background: #94a3b8; }
</style>
</head>
<body>

<h1 style="margin-bottom: 6px;">Ask Atlas — Evaluation Report</h1>
<p id="subtitle" style="color: #64748b; margin-bottom: 20px;"></p>

<div class="dashboard" id="dashboard"></div>
<div class="filter-bar" id="filter-bar"></div>
<div id="tabs-container"></div>
<div id="breakdown-container"></div>
<div id="questions-container"></div>

<script>
// Embedded report data
const REPORT = __REPORT_JSON__;

// ---------- Init ----------
document.addEventListener('DOMContentLoaded', () => {
  renderSubtitle();
  renderDashboard();
  renderFilterBar();
  renderBreakdownTabs();
  renderQuestions();
});

function renderSubtitle() {
  const r = REPORT;
  const parts = [];
  if (r.timestamp) parts.push(r.timestamp.replace('T', ' ').replace('Z', ' UTC'));
  if (r.judge_model && r.judge_model !== 'unknown')
    parts.push('Judge: ' + r.judge_model + ' (' + (r.judge_provider || '') + ')');
  document.getElementById('subtitle').textContent = parts.join(' — ');
}

function renderDashboard() {
  const a = REPORT.aggregate || {};
  const rs = REPORT.run_stats || {};
  const dims = REPORT.dimension_averages || {};

  const stats = [
    { label: 'Questions', value: a.count || 0 },
    { label: 'Pass Rate', value: (a.pass_rate || 0) + '%', sub: `${a.pass_count || 0}P / ${a.partial_count || 0}M / ${a.fail_count || 0}F` },
    { label: 'Avg Pass Count', value: (a.avg_pass_count != null ? a.avg_pass_count : a.avg_weighted_score || 0).toFixed(1), sub: '/ 4' },
    { label: 'Duration', value: formatDuration(rs.total_duration_s || 0), sub: 'avg ' + formatDuration(rs.avg_question_duration_s || 0) + '/q' },
  ];

  // Latency badges
  const la = REPORT.latency_analysis || {};
  if (la.p50_total_ms != null) {
    stats.push({ label: 'P50 Latency', value: (la.p50_total_ms / 1000).toFixed(1) + 's', sub: 'p90 ' + (la.p90_total_ms / 1000).toFixed(1) + 's' });
  }

  // Cost badge
  const ca = REPORT.cost_analysis || {};
  if (ca.total_cost_usd != null) {
    stats.push({ label: 'Total Cost', value: '$' + ca.total_cost_usd.toFixed(4), sub: 'avg $' + (ca.avg_cost_per_question_usd || 0).toFixed(4) + '/q' });
  }

  // Budget violations badge
  const bv = REPORT.budget_violations || {};
  if (bv.total_violations) {
    stats.push({ label: 'Budget Violations', value: bv.total_violations, sub: bv.duration_violations + ' duration, ' + bv.cost_violations + ' cost' });
  }

  // Add dimension pass rates
  for (const [dim, rate] of Object.entries(dims)) {
    // v2: rate is 0.0-1.0 fraction; v1 compat: rate > 1.0 means old Likert average
    const pct = rate <= 1.0 ? (rate * 100).toFixed(1) : ((rate / 5.0) * 100).toFixed(1);
    stats.push({ label: dim.replace(/_/g, ' '), value: pct + '%', sub: 'pass rate' });
  }

  // Failure category summary cards
  const fc = REPORT.failure_categories || {};
  const fcEntries = Object.entries(fc);
  if (fcEntries.length > 0) {
    const fcColors = {
      fabricated_data: '#dc2626', wrong_entity_or_metric: '#d97706',
      numeric_inaccuracy: '#ea580c', missing_required_data: '#0891b2',
      unsupported_embellishment: '#7c3aed', scope_refusal_failure: '#be185d',
      methodology_error: '#4338ca'
    };
    for (const [cat, data] of fcEntries) {
      const color = fcColors[cat] || '#64748b';
      stats.push({ label: cat.replace(/_/g, ' '), value: data.count, sub: data.pct + '% of failures' });
    }
  }

  // Per-mode summary cards
  const bjm = REPORT.by_judge_mode || {};
  for (const [mode, ms] of Object.entries(bjm)) {
    const modeLabel = mode.replace(/_/g, ' ');
    const avgPC = ms.avg_pass_count != null ? ms.avg_pass_count : ms.avg_weighted_score || 0;
    stats.push({ label: modeLabel, value: (ms.pass_rate || 0).toFixed(1) + '%', sub: ms.count + ' qs · avg ' + avgPC.toFixed(1) + '/4' });
  }

  // Link judge aggregate
  const lja = REPORT.link_judge_aggregate || {};
  if (lja.count) {
    const ljaAvg = lja.avg_pass_count != null ? lja.avg_pass_count : (lja.avg_weighted_score || 0);
    const ljaMax = lja.avg_pass_count != null ? '4' : '5';
    stats.push({ label: 'Link Judge', value: ljaAvg.toFixed(1) + '/' + ljaMax, sub: lja.count + ' links · ' + (lja.pass_rate || 0) + '% pass' });
  }

  const el = document.getElementById('dashboard');
  el.innerHTML = stats.map(s =>
    `<div class="stat-card">
      <div class="label">${esc(s.label)}</div>
      <div class="value">${esc(String(s.value))}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}
    </div>`
  ).join('');
}

// ---------- Filters ----------
let currentFilters = { category: '', difficulty: '', verdict: '', judgeMode: '', search: '' };

function renderFilterBar() {
  const pq = REPORT.per_question || [];
  const categories = [...new Set(pq.map(q => q.category))].sort();
  const difficulties = [...new Set(pq.map(q => q.difficulty))].sort();
  const verdicts = [...new Set(pq.map(q => q.verdict))].sort();
  const judgeModes = [...new Set(pq.map(q => q.judge_mode))].sort();

  const bar = document.getElementById('filter-bar');
  bar.innerHTML = `
    <select id="f-category"><option value="">All Categories</option>${categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}</select>
    <select id="f-difficulty"><option value="">All Difficulties</option>${difficulties.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}</select>
    <select id="f-verdict"><option value="">All Verdicts</option>${verdicts.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select>
    <select id="f-judgemode"><option value="">All Judge Modes</option>${judgeModes.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('')}</select>
    <input type="text" id="f-search" placeholder="Search questions...">
    <span class="count" id="filter-count"></span>
  `;

  bar.querySelector('#f-category').addEventListener('change', e => { currentFilters.category = e.target.value; applyFilters(); });
  bar.querySelector('#f-difficulty').addEventListener('change', e => { currentFilters.difficulty = e.target.value; applyFilters(); });
  bar.querySelector('#f-verdict').addEventListener('change', e => { currentFilters.verdict = e.target.value; applyFilters(); });
  bar.querySelector('#f-judgemode').addEventListener('change', e => { currentFilters.judgeMode = e.target.value; applyFilters(); });

  let debounce;
  bar.querySelector('#f-search').addEventListener('input', e => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { currentFilters.search = e.target.value.toLowerCase(); applyFilters(); }, 200);
  });
}

function getFilteredQuestions() {
  return (REPORT.per_question || []).filter(q => {
    if (currentFilters.category && q.category !== currentFilters.category) return false;
    if (currentFilters.difficulty && q.difficulty !== currentFilters.difficulty) return false;
    if (currentFilters.verdict && q.verdict !== currentFilters.verdict) return false;
    if (currentFilters.judgeMode && q.judge_mode !== currentFilters.judgeMode) return false;
    if (currentFilters.search && !q.question_text.toLowerCase().includes(currentFilters.search)
        && !q.question_id.includes(currentFilters.search)) return false;
    return true;
  });
}

function applyFilters() {
  const filtered = getFilteredQuestions();
  document.getElementById('filter-count').textContent = `${filtered.length} of ${(REPORT.per_question || []).length} questions`;
  renderQuestions(filtered);
}

// ---------- Breakdown tabs ----------
function renderBreakdownTabs() {
  const container = document.getElementById('tabs-container');
  container.innerHTML = `
    <div class="tabs">
      <button class="active" data-tab="category">By Category</button>
      <button data-tab="difficulty">By Difficulty</button>
      <button data-tab="judgeMode">By Judge Mode</button>
      <button data-tab="pipeline">By Pipeline</button>
      <button data-tab="pipelineLatency">By Pipeline Latency</button>
      <button data-tab="nodeLatency">By Node</button>
    </div>
  `;
  container.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      renderBreakdown(btn.dataset.tab);
    });
  });
  renderBreakdown('category');
}

function renderBreakdown(tab) {
  const el = document.getElementById('breakdown-container');
  let data;

  if (tab === 'category') {
    data = REPORT.by_category || {};
  } else if (tab === 'difficulty') {
    data = REPORT.by_difficulty || {};
  } else if (tab === 'judgeMode') {
    // Build from per_question
    data = buildBreakdown(q => q.judge_mode || 'n/a');
  } else if (tab === 'pipeline') {
    data = buildBreakdown(q => q.pipeline_used || 'unknown');
  } else if (tab === 'pipelineLatency') {
    const la = REPORT.latency_analysis || {};
    const avgByPipeline = la.avg_by_pipeline || {};
    if (Object.keys(avgByPipeline).length === 0) {
      document.getElementById('breakdown-container').innerHTML = '<p class="no-results">No per-pipeline timing data available</p>';
      return;
    }
    const rows = Object.entries(avgByPipeline).map(([pipe, d]) => {
      const llmPct = d.avg_wall_time_ms ? (d.avg_llm_time_ms / d.avg_wall_time_ms * 100).toFixed(1) : '0.0';
      const ioPct = d.avg_wall_time_ms ? (d.avg_io_time_ms / d.avg_wall_time_ms * 100).toFixed(1) : '0.0';
      return `<tr><td>${esc(pipe)}</td><td>${d.avg_wall_time_ms.toFixed(0)}ms</td><td>${d.pct_of_total.toFixed(1)}%</td><td>${llmPct}%</td><td>${ioPct}%</td><td>${d.appearances}</td></tr>`;
    }).join('');
    document.getElementById('breakdown-container').innerHTML = `
      <table class="breakdown-table">
        <thead><tr><th>Pipeline</th><th>Avg Time</th><th>% of Total</th><th>LLM %</th><th>I/O %</th><th>Appearances</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    return;
  } else if (tab === 'nodeLatency') {
    const la = REPORT.latency_analysis || {};
    const avgByNode = la.avg_by_node || {};
    if (Object.keys(avgByNode).length === 0) {
      document.getElementById('breakdown-container').innerHTML = '<p class="no-results">No per-node timing data available</p>';
      return;
    }
    const rows = Object.entries(avgByNode).map(([node, d]) => {
      const llmPct = d.avg_wall_time_ms ? (d.avg_llm_time_ms / d.avg_wall_time_ms * 100).toFixed(1) : '0.0';
      const ioPct = d.avg_wall_time_ms ? (d.avg_io_time_ms / d.avg_wall_time_ms * 100).toFixed(1) : '0.0';
      return `<tr><td>${esc(node)}</td><td>${d.avg_wall_time_ms.toFixed(0)}ms</td><td>${d.pct_of_total.toFixed(1)}%</td><td>${llmPct}%</td><td>${ioPct}%</td><td>${d.appearances}</td></tr>`;
    }).join('');
    const tb = la.time_breakdown || {};
    document.getElementById('breakdown-container').innerHTML = `
      <p style="margin-bottom:12px;color:#64748b;font-size:13px">Time breakdown: LLM ${(tb.llm_pct||0).toFixed(1)}% · I/O ${(tb.io_pct||0).toFixed(1)}% · Overhead ${(tb.overhead_pct||0).toFixed(1)}%</p>
      <table class="breakdown-table">
        <thead><tr><th>Node</th><th>Avg Time</th><th>% of Total</th><th>LLM %</th><th>I/O %</th><th>Appearances</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
    return;
  }

  if (!data || Object.keys(data).length === 0) {
    el.innerHTML = '<p class="no-results">No breakdown data available</p>';
    return;
  }

  el.innerHTML = `
    <table class="breakdown-table">
      <thead><tr><th>${esc(tab === 'pipeline' ? 'Pipeline' : tab.charAt(0).toUpperCase() + tab.slice(1))}</th><th>Count</th><th>Avg Pass Count</th><th>Pass Rate</th><th>Pass</th><th>Partial</th><th>Fail</th></tr></thead>
      <tbody>${Object.entries(data).sort((a,b) => a[0].localeCompare(b[0])).map(([k, v]) => {
        const avg = v.avg_pass_count != null ? v.avg_pass_count : (v.avg_weighted_score || 0);
        return `<tr><td>${esc(k)}</td><td>${v.count}</td><td>${avg.toFixed(1)}/4</td><td>${(v.pass_rate || 0).toFixed(1)}%</td><td>${v.pass_count || 0}</td><td>${v.partial_count || 0}</td><td>${v.fail_count || 0}</td></tr>`;
      }).join('')}</tbody>
    </table>
  `;
}

function buildBreakdown(keyFn) {
  const groups = {};
  for (const q of (REPORT.per_question || [])) {
    const key = keyFn(q);
    if (!groups[key]) groups[key] = [];
    groups[key].push(q);
  }
  const result = {};
  for (const [key, qs] of Object.entries(groups)) {
    const scores = qs.filter(q => q.weighted_score != null).map(q => q.weighted_score);
    result[key] = {
      count: qs.length,
      avg_weighted_score: scores.length ? scores.reduce((a,b)=>a+b,0)/scores.length : 0,
      pass_count: qs.filter(q => q.verdict === 'pass').length,
      partial_count: qs.filter(q => q.verdict === 'partial').length,
      fail_count: qs.filter(q => q.verdict === 'fail').length,
      pass_rate: qs.length ? qs.filter(q => q.verdict === 'pass').length / qs.length * 100 : 0,
    };
  }
  return result;
}

// ---------- Question cards ----------
function renderQuestions(questions) {
  questions = questions || REPORT.per_question || [];
  const el = document.getElementById('questions-container');
  document.getElementById('filter-count').textContent = `${questions.length} of ${(REPORT.per_question || []).length} questions`;

  if (questions.length === 0) {
    el.innerHTML = '<p class="no-results">No questions match the current filters</p>';
    return;
  }

  el.innerHTML = questions.map((q, i) => {
    const icon = q.verdict === 'pass' ? '&#x1f7e2;' : q.verdict === 'partial' ? '&#x1f7e1;' : q.verdict === 'fail' ? '&#x1f534;' : '&#x26aa;';
    const verdictClass = q.verdict === 'pass' ? 'verdict-pass' : q.verdict === 'partial' ? 'verdict-partial' : q.verdict === 'fail' ? 'verdict-fail' : '';
    return `
    <div class="question-card ${esc(q.verdict || '')}" id="qcard-${i}">
      <div class="card-header" onclick="toggleDetail(${i})">
        <span class="icon">${icon}</span>
        <span class="qid">Q${esc(q.question_id)}</span>
        <span class="text">${esc(q.question_text || '')}</span>
        <div class="badges">
          <span class="badge ${esc(q.difficulty || '')}">${esc(q.difficulty || '')}</span>
          <span class="badge cat">${esc(q.category || '')}</span>
          <span class="badge score">${Math.round(q.pass_count != null ? q.pass_count : (q.weighted_score || 0))}/4</span>
          <span class="badge ${verdictClass}">${esc(q.verdict || 'n/a')}</span>
          ${q.judge_mode ? `<span class="badge mode">${esc(q.judge_mode)}</span>` : ''}
        </div>
      </div>
      <div class="card-detail" id="detail-${i}"></div>
    </div>`;
  }).join('');
}

function toggleDetail(idx) {
  const detail = document.getElementById('detail-' + idx);
  if (detail.classList.contains('open')) {
    detail.classList.remove('open');
    return;
  }
  // Lazy render
  if (!detail.dataset.rendered) {
    const questions = getFilteredQuestions();
    const q = questions[idx];
    if (!q) return;
    detail.innerHTML = buildDetailHTML(q);
    detail.dataset.rendered = '1';
  }
  detail.classList.add('open');
}

// ---------- Hash-based tool color palette ----------
const TOOL_PALETTE = ['#6366f1','#059669','#d97706','#0891b2','#7c3aed',
                      '#dc2626','#2563eb','#ca8a04','#0d9488','#c026d3'];
function toolColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = ((hash << 5) - hash) + name.charCodeAt(i) | 0;
  return TOOL_PALETTE[Math.abs(hash) % TOOL_PALETTE.length];
}

// ---------- Per-call pipeline metadata (inline in tool call cards) ----------

function buildPipelineMetadataHTML(meta) {
  if (!meta) return '';
  const sectionStyle = 'padding:6px 12px;border-top:1px solid #e2e8f0;';
  const labelStyle = 'font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;';
  let html = '';

  // Question (extracted by pipeline)
  if (meta.question) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Extracted Question</p>';
    html += '<p style="font-size:12px;font-style:italic;margin:0;">' + esc(meta.question) + '</p>';
    html += '</div>';
  }

  // API target (GraphQL)
  if (meta.api_target) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">API Target</p>';
    html += '<p style="font-size:12px;margin:0;"><code style="background:#f1f5f9;padding:2px 6px;border-radius:3px;">' + esc(meta.api_target) + '</code></p>';
    html += '</div>';
  }

  // Classification
  const cls = meta.classification;
  if (cls) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Classification</p>';
    html += '<table style="font-size:12px;"><tbody>';
    for (const [k, v] of Object.entries(cls)) {
      html += '<tr><td style="font-weight:600;min-width:120px;padding:2px 8px 2px 0;">' + esc(k) + '</td><td style="padding:2px 0;">' + esc(String(v)) + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }

  // Entity extraction
  const ee = meta.entity_extraction;
  if (ee) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Entity Extraction</p>';
    html += '<pre style="font-size:12px;max-height:200px;overflow-y:auto;">' + esc(JSON.stringify(ee, null, 2)) + '</pre>';
    html += '</div>';
  }

  // Resolved params
  const rp = meta.resolved_params;
  if (rp) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Resolved Parameters</p>';
    html += '<pre style="font-size:12px;max-height:200px;overflow-y:auto;">' + esc(JSON.stringify(rp, null, 2)) + '</pre>';
    html += '</div>';
  }

  // GraphQL query
  if (meta.query) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">GraphQL Query</p>';
    html += '<pre style="font-size:12px;max-height:300px;overflow-y:auto;">' + esc(meta.query) + '</pre>';
    html += '</div>';
  }

  // Atlas links
  const links = meta.atlas_links || [];
  if (links.length > 0) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Atlas Links</p>';
    html += links.map(function(l) {
      const url = l.url || l.link || '';
      const label = l.label || l.title || url;
      return '<a href="' + esc(url) + '" target="_blank" rel="noopener" style="color:#3b82f6;text-decoration:underline;font-size:12px;">' + esc(label) + '</a>';
    }).join('<br>');
    html += '</div>';
  }

  // SQL pipeline: products
  const products = meta.products || [];
  if (products.length > 0) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Products</p>';
    html += '<pre style="font-size:12px;max-height:200px;overflow-y:auto;">' + esc(JSON.stringify(products, null, 2)) + '</pre>';
    html += '</div>';
  }

  // SQL pipeline: lookup codes
  if (meta.codes) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Lookup Codes</p>';
    html += '<p style="font-size:12px;margin:0;"><code style="background:#f1f5f9;padding:2px 6px;border-radius:3px;">' + esc(meta.codes) + '</code></p>';
    html += '</div>';
  }

  // SQL pipeline: final SQL
  if (meta.final_sql) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Final SQL</p>';
    html += '<pre style="font-size:12px;max-height:300px;overflow-y:auto;">' + esc(meta.final_sql) + '</pre>';
    html += '</div>';
  }

  // SQL pipeline: result summary
  if (meta.result_row_count !== undefined) {
    html += '<div style="' + sectionStyle + '">';
    html += '<p style="' + labelStyle + '">Result Summary</p>';
    html += '<table style="font-size:12px;"><tbody>';
    html += '<tr><td style="font-weight:600;min-width:120px;padding:2px 8px 2px 0;">Rows</td><td>' + meta.result_row_count + '</td></tr>';
    if (meta.result_columns) html += '<tr><td style="font-weight:600;min-width:120px;padding:2px 8px 2px 0;">Columns</td><td>' + esc(meta.result_columns.join(', ')) + '</td></tr>';
    if (meta.execution_time_ms) html += '<tr><td style="font-weight:600;min-width:120px;padding:2px 8px 2px 0;">Time</td><td>' + meta.execution_time_ms + 'ms</td></tr>';
    html += '</tbody></table></div>';
  }

  return html;
}

// ---------- Detail section helpers ----------

function buildVerdictSummary(q) {
  const jd = q.judge_details || {};
  let html = '';

  // Duration badge inline in header
  const durationBadge = q.duration_s != null
    ? ' <span style="font-size:11px;color:#94a3b8;text-transform:none;letter-spacing:0;font-weight:400;">(' + formatDuration(q.duration_s) + ')</span>'
    : '';

  // Data-driven dimension discovery — supports both v2 (passed) and v1 (score)
  const dimEntries = Object.entries(jd).filter(
    ([k, v]) => v && typeof v === 'object' && (v.passed != null || v.score != null) && v.reasoning != null
  );

  const hasRefusal = jd.judge_mode === 'refusal';
  const hasContent = dimEntries.length > 0 || q.judge_comment || hasRefusal;
  if (!hasContent) return '';

  html += '<div class="detail-section"><h4>Judge Verdict' + durationBadge + '</h4><div class="content">';

  // Dimension pass/fail badges (v2) or score bars (v1 fallback)
  if (dimEntries.length > 0) {
    html += '<div class="dim-bars">';
    for (const [d, v] of dimEntries) {
      const label = d.replace(/_/g, ' ');
      if (v.passed != null) {
        // Binary pass/fail badge
        const cls = v.passed ? 'high' : 'low';
        const pct = v.passed ? 100 : 0;
        const badge = v.passed ? 'PASS' : 'FAIL';
        html += `<div class="dim-bar">
          <span class="dim-label">${esc(label)}</span>
          <div class="bar-bg"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
          <span class="dim-score" style="color:${v.passed ? '#22c55e' : '#ef4444'};font-weight:700;">${badge}</span>
        </div>`;
      } else {
        // Legacy 1-5 score bar
        const score = v.score;
        const pct = score / 5 * 100;
        const cls = score >= 4 ? 'high' : score >= 3 ? 'mid' : 'low';
        html += `<div class="dim-bar">
          <span class="dim-label">${esc(label)}</span>
          <div class="bar-bg"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
          <span class="dim-score">${score}/5</span>
        </div>`;
      }
    }
    html += '</div>';
    // Dimension reasoning
    for (const [d, v] of dimEntries) {
      if (v.reasoning) {
        html += '<p style="font-size:12px;color:#64748b;margin-top:6px;"><strong>' + esc(d.replace(/_/g, ' ')) + ':</strong> ' + esc(v.reasoning) + '</p>';
      }
    }
  }

  // Judge commentary
  if (q.judge_comment) {
    html += '<p style="font-size:13px;margin-top:8px;">' + esc(q.judge_comment) + '</p>';
  }

  // Failure category badge
  if (jd.failure_category) {
    const fcColors = {
      fabricated_data: '#dc2626', wrong_entity_or_metric: '#d97706',
      numeric_inaccuracy: '#ea580c', missing_required_data: '#0891b2',
      unsupported_embellishment: '#7c3aed', scope_refusal_failure: '#be185d',
      methodology_error: '#4338ca'
    };
    const color = fcColors[jd.failure_category] || '#64748b';
    html += '<div style="margin-top:8px;">';
    html += '<span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + color + ';">' + esc(jd.failure_category.replace(/_/g, ' ')) + '</span>';
    if (jd.secondary_failure_categories && jd.secondary_failure_categories.length > 0) {
      for (const sec of jd.secondary_failure_categories) {
        const secColor = fcColors[sec] || '#64748b';
        html += ' <span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;color:' + secColor + ';background:#f1f5f9;border:1px solid ' + secColor + ';">' + esc(sec.replace(/_/g, ' ')) + '</span>';
      }
    }
    html += '</div>';
  }

  // Refusal evaluation
  if (hasRefusal) {
    html += '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e2e8f0;">';
    html += '<p style="font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;">Refusal Evaluation</p>';
    html += '<p>Appropriate refusal: <strong>' + (jd.appropriate_refusal ? 'Yes' : 'No') + '</strong></p>';
    html += '<p>Graceful: <strong>' + (jd.graceful ? 'Yes' : 'No') + '</strong></p>';
    if (jd.reasoning) html += '<p>' + esc(jd.reasoning) + '</p>';
    html += '</div>';
  }

  html += '</div></div>';
  return html;
}

function buildLinkJudgeSection(q) {
  const lj = q.link_judge;
  if (!lj) return '';

  const verdictClass = lj.verdict === 'pass' ? 'verdict-pass' : lj.verdict === 'partial' ? 'verdict-partial' : 'verdict-fail';
  const scoreLabel = lj.pass_count != null
    ? (lj.pass_count + '/4')
    : ((lj.weighted_score || 0).toFixed(2) + '/5');
  let html = '<div class="detail-section"><h4>Link Judge <span class="badge ' + verdictClass + '" style="vertical-align:middle;">' + esc(lj.verdict) + '</span> <span style="font-size:11px;color:#94a3b8;text-transform:none;letter-spacing:0;font-weight:400;">(' + scoreLabel + ')</span></h4><div class="content">';

  // Dimension pass/fail badges (v2) or score bars (v1 legacy)
  const linkDims = ['link_presence', 'content_relevance', 'entity_correctness', 'parameter_accuracy'];
  const dimEntries = linkDims.filter(d => lj[d] && (lj[d].passed != null || lj[d].score != null));
  if (dimEntries.length > 0) {
    html += '<div class="dim-bars">';
    for (const d of dimEntries) {
      const v = lj[d];
      const label = d.replace(/_/g, ' ');
      if (v.passed != null) {
        // Binary pass/fail badge
        const cls = v.passed ? 'high' : 'low';
        const pct = v.passed ? 100 : 0;
        const badge = v.passed ? 'PASS' : 'FAIL';
        html += '<div class="dim-bar"><span class="dim-label">' + esc(label) + '</span><div class="bar-bg"><div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div></div><span class="dim-score" style="color:' + (v.passed ? '#22c55e' : '#ef4444') + ';font-weight:700;">' + badge + '</span></div>';
      } else {
        // Legacy 1-5 score bar
        const score = v.score;
        const pct = score / 5 * 100;
        const cls = score >= 4 ? 'high' : score >= 3 ? 'mid' : 'low';
        html += '<div class="dim-bar"><span class="dim-label">' + esc(label) + '</span><div class="bar-bg"><div class="bar-fill ' + cls + '" style="width:' + pct + '%"></div></div><span class="dim-score">' + score + '/5</span></div>';
      }
    }
    html += '</div>';
    // Dimension reasoning
    for (const d of dimEntries) {
      const v = lj[d];
      if (v.reasoning) {
        html += '<p style="font-size:12px;color:#64748b;margin-top:6px;"><strong>' + esc(d.replace(/_/g, ' ')) + ':</strong> ' + esc(v.reasoning) + '</p>';
      }
    }
  }

  // Overall comment
  if (lj.overall_comment) {
    html += '<p style="font-size:13px;margin-top:8px;">' + esc(lj.overall_comment) + '</p>';
  }

  // Show generated links and ground truth URL
  html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid #e2e8f0;">';
  const links = q.graphql_atlas_links || [];
  if (links.length > 0) {
    html += '<p style="font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;">Generated Links</p>';
    html += links.map(function(l) {
      const url = l.url || l.link || '';
      const label = l.label || l.title || '';
      let linkHtml = '';
      if (label && label !== url) {
        linkHtml += '<span style="font-size:12px;color:#475569;font-weight:500;">' + esc(label) + '</span><br>';
      }
      linkHtml += '<a href="' + esc(url) + '" target="_blank" rel="noopener" style="color:#3b82f6;text-decoration:underline;font-size:12px;">' + esc(url) + '</a>';
      return linkHtml;
    }).join('<div style="margin-top:6px;"></div>');
  }
  if (q.ground_truth_atlas_url) {
    html += '<p style="font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;margin-top:8px;">Ground Truth URL</p>';
    html += '<a href="' + esc(q.ground_truth_atlas_url) + '" target="_blank" rel="noopener" style="color:#059669;text-decoration:underline;font-size:12px;">' + esc(q.ground_truth_atlas_url) + '</a>';
  }
  html += '</div>';

  html += '</div></div>';
  return html;
}

function buildToolCallLog(q) {
  const toolCalls = q.tool_call_details || [];
  if (toolCalls.length === 0) {
    return buildLegacyPipelineLog(q);
  }

  // Compute per-tool round counters: { "query_tool": 3, "atlas_graphql": 1 }
  const toolTotals = {};
  for (const tc of toolCalls) {
    toolTotals[tc.tool_name] = (toolTotals[tc.tool_name] || 0) + 1;
  }
  const toolSeen = {};

  let logHTML = '';
  for (const tc of toolCalls) {
    toolSeen[tc.tool_name] = (toolSeen[tc.tool_name] || 0) + 1;
    const round = toolSeen[tc.tool_name];
    const total = toolTotals[tc.tool_name];
    const color = toolColor(tc.tool_name);

    logHTML += '<div style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">';
    logHTML += '<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">';
    logHTML += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + color + ';">' + esc(tc.tool_name) + '</span>';
    // Show per-tool round when called more than once; always show global index
    if (total > 1) {
      logHTML += '<span style="font-size:12px;color:#475569;font-weight:600;">Round ' + round + '/' + total + '</span>';
    }
    logHTML += '<span style="font-size:11px;color:#94a3b8;">Call #' + tc.index + '</span>';
    if (tc.arguments && tc.arguments.question) {
      logHTML += '<span style="font-size:13px;color:#475569;width:100%;">' + esc(tc.arguments.question) + '</span>';
    }
    logHTML += '</div>';
    // Per-call pipeline metadata (entity extraction, classification, query planning, atlas links)
    if (tc.pipeline_metadata) {
      logHTML += buildPipelineMetadataHTML(tc.pipeline_metadata);
    }
    if (tc.result_content) {
      const content = tc.result_content;
      let formattedContent;
      const trimmed = content.trim();
      if ((trimmed.startsWith('{') || trimmed.startsWith('[')) && (trimmed.endsWith('}') || trimmed.endsWith(']'))) {
        try {
          const jsonStart = content.indexOf(trimmed.startsWith('[') ? '[' : '{');
          JSON.parse(content.substring(jsonStart));
          formattedContent = '<pre>' + esc(content) + '</pre>';
        } catch(e) {
          formattedContent = '<pre>' + esc(content) + '</pre>';
        }
      } else if (content.includes('\\n') || content.length > 200) {
        formattedContent = '<pre style="max-height:400px;overflow-y:auto;">' + esc(content) + '</pre>';
      } else {
        formattedContent = '<pre>' + esc(content) + '</pre>';
      }
      logHTML += '<div style="padding:8px 12px;">' + formattedContent + '</div>';
    } else {
      logHTML += '<div style="padding:8px 12px;color:#94a3b8;font-size:13px;font-style:italic;">No response captured</div>';
    }
    logHTML += '</div>';
  }
  return `<div class="detail-section">
    <h4>Tool Call Log <span style="font-size:11px;color:#94a3b8;text-transform:none;letter-spacing:0;">(${toolCalls.length} call${toolCalls.length !== 1 ? 's' : ''})</span></h4>
    <div class="content">${logHTML}</div>
  </div>`;
}

// Legacy fallback: synthesize a unified pipeline log from separate fields
// (sql_history, graphql_query, pipeline_result_columns/rows) for old runs
// that didn't capture per-call tool_call_details.
function buildLegacyPipelineLog(q) {
  const sqlHistory = q.sql_history || [];
  const graphqlQuery = q.graphql_query;
  const resCols = q.pipeline_result_columns || [];
  const resRows = q.pipeline_result_rows || [];
  const tools = q.tools_used || [];

  const hasSQL = sqlHistory.length > 0 || q.sql;
  const hasGraphQL = !!graphqlQuery;
  const hasResults = resCols.length > 0;
  if (!hasSQL && !hasGraphQL && !hasResults) return '';

  const callCounts = {};
  for (const t of tools) callCounts[t] = (callCounts[t] || 0) + 1;

  let logHTML = '';

  // --- GraphQL query (only the last query is captured in legacy data) ---
  if (hasGraphQL) {
    const gqlTotal = callCounts['atlas_graphql'] || 1;
    const color = toolColor('atlas_graphql');
    logHTML += '<div style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">';
    logHTML += '<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">';
    logHTML += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + color + ';">atlas_graphql</span>';
    if (gqlTotal > 1) {
      logHTML += '<span style="font-size:11px;color:#94a3b8;">last of ' + gqlTotal + ' calls — only final query captured</span>';
    }
    logHTML += '</div>';
    logHTML += '<div style="padding:8px 12px;"><pre>' + esc(graphqlQuery) + '</pre></div>';
    logHTML += '</div>';
  }

  // --- SQL history with round grouping ---
  if (sqlHistory.length > 0) {
    const stageColors = { generated: '#6366f1', validated: '#22c55e', execution_error: '#ef4444' };
    const color = toolColor('query_tool');
    let round = 0;
    for (let i = 0; i < sqlHistory.length; i++) {
      const h = sqlHistory[i];
      const isNewRound = i === 0 || h.stage === 'generated';
      if (isNewRound) {
        if (round > 0) logHTML += '</div></div>'; // close content + card
        round++;
        const sqlTotal = callCounts['query_tool'] || round;
        logHTML += '<div style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">';
        logHTML += '<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">';
        logHTML += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + color + ';">query_tool</span>';
        if (sqlTotal > 1) {
          logHTML += '<span style="font-size:12px;color:#475569;font-weight:600;">Round ' + round + '/' + sqlTotal + '</span>';
        }
        logHTML += '</div>';
        logHTML += '<div style="padding:8px 12px;">';
      }
      const sColor = stageColors[h.stage] || '#94a3b8';
      const hasErrors = h.errors && h.errors.length > 0;
      logHTML += '<div style="margin-top:' + (isNewRound ? '0' : '8') + 'px;">';
      logHTML += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + sColor + ';">' + esc(h.stage) + '</span>';
      if (hasErrors) {
        logHTML += '<span style="margin-left:8px;color:#ef4444;font-size:12px;">' + h.errors.map(e => esc(e)).join('; ') + '</span>';
      }
      logHTML += '<pre style="margin-top:4px;">' + esc(h.sql || '') + '</pre>';
      logHTML += '</div>';
    }
    if (round > 0) logHTML += '</div></div>'; // close content + card
  } else if (q.sql) {
    const color = toolColor('query_tool');
    logHTML += '<div style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">';
    logHTML += '<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">';
    logHTML += '<span style="display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;color:#fff;background:' + color + ';">query_tool</span>';
    logHTML += '</div>';
    logHTML += '<div style="padding:8px 12px;"><pre>' + esc(q.sql) + '</pre></div>';
    logHTML += '</div>';
  }

  // --- Pipeline results table ---
  if (hasResults) {
    // Attribute results when possible
    let resultsLabel = 'Pipeline Results';
    if (tools.length === 1) resultsLabel = esc(tools[0]) + ' Results';
    else if (tools.length > 1) resultsLabel = 'Pipeline Results (' + tools.map(t => esc(t)).join(' + ') + ')';
    logHTML += '<div style="margin-bottom:16px;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">';
    logHTML += '<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:#f8fafc;border-bottom:1px solid #e2e8f0;">';
    logHTML += '<span style="font-size:12px;font-weight:600;color:#475569;">' + resultsLabel + '</span>';
    logHTML += '<span class="badge score">' + resRows.length + ' row' + (resRows.length !== 1 ? 's' : '') + '</span>';
    logHTML += '</div>';
    logHTML += '<div style="padding:8px 12px;max-height:400px;overflow-y:auto;">';
    logHTML += '<table><thead><tr>' + resCols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>';
    for (const row of resRows) {
      logHTML += '<tr>' + row.map(v => '<td>' + esc(String(v ?? '')) + '</td>').join('') + '</tr>';
    }
    logHTML += '</tbody></table></div></div>';
  }

  return `<div class="detail-section">
    <h4>Pipeline Call Log <span style="font-size:11px;color:#94a3b8;text-transform:none;letter-spacing:0;">(legacy — per-call results not captured)</span></h4>
    <div class="content">${logHTML}</div>
  </div>`;
}

function buildEntityExtraction(q) {
  const pp = q.pipeline_products;
  // Skip top-level GraphQL entity display when per-call history is available
  // (entity extraction is already shown inline in each tool call card)
  const hasPerCallHistory = (q.graphql_call_history || []).length > 0;
  const gee = hasPerCallHistory ? null : q.graphql_entity_extraction;
  if (!pp && !gee) return '';

  let entityHTML = '';
  if (pp) {
    entityHTML += '<p style="font-size:12px;color:#64748b;margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;">SQL Pipeline Entities</p>';
    if (pp.classification_schemas && pp.classification_schemas.length) {
      entityHTML += '<p style="font-size:12px;color:#64748b;margin-bottom:6px;">Schemas: <strong>' + pp.classification_schemas.map(s => esc(s)).join(', ') + '</strong></p>';
    }
    if (pp.products && pp.products.length) {
      entityHTML += '<table><thead><tr><th>Product</th><th>Schema</th><th>Codes</th></tr></thead><tbody>';
      for (const p of pp.products) {
        entityHTML += '<tr><td>' + esc(p.name || '') + '</td><td>' + esc(p.schema || '') + '</td><td>' + esc((p.codes || []).join(', ')) + '</td></tr>';
      }
      entityHTML += '</tbody></table>';
    }
    if (pp.countries && pp.countries.length) {
      entityHTML += '<table style="margin-top:8px"><thead><tr><th>Country</th><th>ISO3</th></tr></thead><tbody>';
      for (const c of pp.countries) {
        entityHTML += '<tr><td>' + esc(c.name || '') + '</td><td>' + esc(c.iso3_code || '') + '</td></tr>';
      }
      entityHTML += '</tbody></table>';
    }
  }
  if (gee) {
    entityHTML += '<p style="font-size:12px;color:#64748b;margin-top:8px;">GraphQL entities (last call only):</p><pre>' + esc(JSON.stringify(gee, null, 2)) + '</pre>';
  }
  if (!entityHTML) return '';
  return '<div class="detail-section"><h4>Entity Extraction</h4><div class="content">' + entityHTML + '</div></div>';
}

function buildQueryPlanning(q) {
  // Skip top-level GraphQL query planning when per-call history is available
  // (classification, resolved params, atlas links are shown inline in each tool call card)
  const hasPerCallHistory = (q.graphql_call_history || []).length > 0;
  if (hasPerCallHistory) return '';

  let html = '';
  // GraphQL Classification (legacy: last call only)
  const gc = q.graphql_classification;
  if (gc) {
    let gcHTML = '<table><tbody>';
    for (const [k, v] of Object.entries(gc)) {
      gcHTML += '<tr><td style="font-weight:600;min-width:120px;">' + esc(k) + '</td><td>' + esc(String(v)) + '</td></tr>';
    }
    gcHTML += '</tbody></table>';
    html += gcHTML;
  }
  // Atlas Links (legacy: last call only)
  const links = q.graphql_atlas_links || [];
  if (links.length > 0) {
    if (gc) html += '<div style="margin-top:10px;padding-top:8px;border-top:1px solid #e2e8f0;">';
    html += '<p style="font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;">Atlas Links</p>';
    html += links.map(l => {
      const url = l.url || l.link || '';
      const label = l.label || l.title || url;
      return '<a href="' + esc(url) + '" target="_blank" rel="noopener" style="color:#3b82f6;text-decoration:underline;">' + esc(label) + '</a>';
    }).join('<br>');
    if (gc) html += '</div>';
  }
  if (!html) return '';
  return '<div class="detail-section"><h4>Query Planning</h4><div class="content">' + html + '</div></div>';
}

function buildTimingSection(q) {
  const steps = q.step_timing || [];
  if (steps.length === 0) return '';

  const maxMs = Math.max(...steps.map(s => s.wall_time_ms || 0), 1);
  const rows = steps.map(s => {
    const wall = s.wall_time_ms || 0;
    const llm = s.llm_time_ms || 0;
    const io = s.io_time_ms || 0;
    const overhead = Math.max(0, wall - llm - io);
    const barPct = (wall / maxMs * 100).toFixed(1);
    const llmPct = wall ? (llm / wall * 100).toFixed(1) : '0';
    const ioPct = wall ? (io / wall * 100).toFixed(1) : '0';
    const overheadPct = wall ? (overhead / wall * 100).toFixed(1) : '0';
    const label = s.node + (s.tool_pipeline && s.tool_pipeline !== s.node ? ' (' + s.tool_pipeline + ')' : '');
    return `<div class="waterfall-row">
      <span class="waterfall-label" title="${esc(label)}">${esc(label)}</span>
      <div class="waterfall-bar-bg">
        <div class="waterfall-bar" style="width:${barPct}%">
          <span class="wf-llm" style="width:${llmPct}%"></span>
          <span class="wf-io" style="width:${ioPct}%"></span>
          <span class="wf-overhead" style="width:${overheadPct}%"></span>
        </div>
      </div>
      <span class="waterfall-time">${(wall/1000).toFixed(1)}s</span>
    </div>`;
  }).join('');
  return `<div class="detail-section">
    <h4>Step Timing</h4>
    <div class="content">
      <div class="waterfall">${rows}</div>
      <div class="waterfall-legend">
        <span class="leg-llm">LLM</span>
        <span class="leg-io">I/O</span>
        <span class="leg-overhead">Overhead</span>
      </div>
    </div>
  </div>`;
}

// ---------- Main detail builder: two-tier layout ----------
let _debugIdCounter = 0;

function buildDetailHTML(q) {
  // --- Primary tier (always visible) ---
  let primary = '';

  // Agent answer (rendered markdown)
  if (q.agent_answer) {
    let rendered;
    try { rendered = marked.parse(q.agent_answer); }
    catch(e) { rendered = '<pre>' + esc(q.agent_answer) + '</pre>'; }
    primary += `<div class="detail-section">
      <h4>Agent Answer</h4>
      <div class="content md-rendered">${rendered}</div>
    </div>`;
  }

  // Expected behavior (for refusal questions)
  if (q.expected_behavior) {
    primary += `<div class="detail-section">
      <h4>Expected Behavior</h4>
      <div class="content">${esc(q.expected_behavior)}</div>
    </div>`;
  }

  // Ground truth table
  if (q.ground_truth && q.ground_truth.length > 0) {
    const cols = Object.keys(q.ground_truth[0]);
    primary += `<div class="detail-section">
      <h4>Ground Truth Data</h4>
      <div class="content">
        <table>
          <thead><tr>${cols.map(c => '<th>' + esc(c) + '</th>').join('')}</tr></thead>
          <tbody>${q.ground_truth.map(row =>
            '<tr>' + cols.map(c => '<td>' + esc(String(row[c] ?? '')) + '</td>').join('') + '</tr>'
          ).join('')}</tbody>
        </table>
      </div>
    </div>`;
  }

  // Web research content (for web_research judge mode)
  if (q.web_research && q.web_research.research_answer) {
    const confBadge = q.web_research.confidence
      ? ' <span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:' +
        (q.web_research.confidence === 'high' ? '#d1fae5;color:#065f46' : q.web_research.confidence === 'medium' ? '#fef3c7;color:#92400e' : '#fee2e2;color:#991b1b') +
        '">' + esc(q.web_research.confidence) + ' confidence</span>'
      : '';
    const metaLine = [q.web_research.provider, q.web_research.model].filter(Boolean).join(' / ');
    let wrRendered;
    try { wrRendered = marked.parse(q.web_research.research_answer); }
    catch(e) { wrRendered = '<pre>' + esc(q.web_research.research_answer) + '</pre>'; }
    let sourcesHtml = '';
    if (q.web_research.sources && q.web_research.sources.length > 0) {
      sourcesHtml = '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;font-size:12px;"><strong>Sources:</strong><ol style="margin:4px 0 0 20px;padding:0;">' +
        q.web_research.sources.map(s => '<li><a href="' + esc(s) + '" target="_blank" rel="noopener" style="color:#2563eb;word-break:break-all;">' + esc(s) + '</a></li>').join('') +
        '</ol></div>';
    }
    primary += `<div class="detail-section">
      <h4>Web Research Reference${confBadge}</h4>
      ${metaLine ? '<div style="font-size:11px;color:#6b7280;margin-bottom:6px;">' + esc(metaLine) + '</div>' : ''}
      <div class="content md-rendered" style="max-height:400px;overflow-y:auto;border:1px solid #e5e7eb;border-radius:6px;padding:12px;">${wrRendered}${sourcesHtml}</div>
    </div>`;
  }

  // Paper research content (for paper_research judge mode)
  if (q.paper_research && q.paper_research.research_answer) {
    const confBadge = q.paper_research.confidence
      ? ' <span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:' +
        (q.paper_research.confidence === 'high' ? '#d1fae5;color:#065f46' : q.paper_research.confidence === 'medium' ? '#fef3c7;color:#92400e' : '#fee2e2;color:#991b1b') +
        '">' + esc(q.paper_research.confidence) + ' confidence</span>'
      : '';
    const paperMeta = [q.paper_research.paper_title, q.paper_research.paper_year].filter(Boolean).join(' (') + (q.paper_research.paper_year ? ')' : '');
    let prRendered;
    try { prRendered = marked.parse(q.paper_research.research_answer); }
    catch(e) { prRendered = '<pre>' + esc(q.paper_research.research_answer) + '</pre>'; }
    let quotesHtml = '';
    if (q.paper_research.supporting_quotes && q.paper_research.supporting_quotes.length > 0) {
      quotesHtml = '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;font-size:12px;"><strong>Supporting Quotes:</strong><ul style="margin:4px 0 0 20px;padding:0;">' +
        q.paper_research.supporting_quotes.map(s => '<li style="margin-bottom:4px;font-style:italic;color:#4b5563;">"' + esc(s) + '"</li>').join('') +
        '</ul></div>';
    }
    let dataPointsHtml = '';
    if (q.paper_research.data_points && q.paper_research.data_points.length > 0) {
      dataPointsHtml = '<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;font-size:12px;"><strong>Data Points:</strong><table style="width:100%;margin-top:4px;border-collapse:collapse;font-size:12px;">' +
        '<tr style="background:#f9fafb;"><th style="padding:4px 8px;text-align:left;border:1px solid #e5e7eb;">Metric</th><th style="padding:4px 8px;text-align:left;border:1px solid #e5e7eb;">Value</th><th style="padding:4px 8px;text-align:left;border:1px solid #e5e7eb;">Year</th><th style="padding:4px 8px;text-align:left;border:1px solid #e5e7eb;">Country</th></tr>' +
        q.paper_research.data_points.map(dp => '<tr><td style="padding:4px 8px;border:1px solid #e5e7eb;">' + esc(dp.metric || '') + '</td><td style="padding:4px 8px;border:1px solid #e5e7eb;">' + esc(dp.value || '') + '</td><td style="padding:4px 8px;border:1px solid #e5e7eb;">' + esc(dp.year || '') + '</td><td style="padding:4px 8px;border:1px solid #e5e7eb;">' + esc(dp.country || '') + '</td></tr>').join('') +
        '</table></div>';
    }
    primary += `<div class="detail-section">
      <h4>Paper Research Reference${confBadge}</h4>
      ${paperMeta ? '<div style="font-size:11px;color:#6b7280;margin-bottom:6px;">' + esc(paperMeta) + '</div>' : ''}
      <div class="content md-rendered" style="max-height:400px;overflow-y:auto;border:1px solid #e5e7eb;border-radius:6px;padding:12px;">${prRendered}${quotesHtml}${dataPointsHtml}</div>
    </div>`;
  }

  // Judge Verdict (merged: dimension scores + commentary + refusal)
  primary += buildVerdictSummary(q);

  // Link Judge Verdict
  primary += buildLinkJudgeSection(q);

  // --- Debug tier (collapsed by default) ---
  let debug = '';
  debug += buildToolCallLog(q);
  debug += buildEntityExtraction(q);
  debug += buildQueryPlanning(q);

  // Tools used badges
  if (q.tools_used && q.tools_used.length > 0) {
    debug += `<div class="detail-section">
      <h4>Tools Used</h4>
      <div class="tools-list">${q.tools_used.map(t => `<span class="tool-badge">${esc(t)}</span>`).join('')}</div>
    </div>`;
  }

  debug += buildTimingSection(q);

  // Wrap debug tier in collapsible drawer
  if (debug) {
    const did = 'debug-' + (_debugIdCounter++);
    primary += `<button class="debug-toggle" onclick="toggleDebug('${did}',this)">Show debug details</button>`;
    primary += `<div class="debug-drawer" id="${did}">${debug}</div>`;
  }

  return primary;
}

function toggleDebug(id, btn) {
  const el = document.getElementById(id);
  el.classList.toggle('open');
  btn.classList.toggle('open');
  btn.textContent = el.classList.contains('open') ? 'Hide debug details' : 'Show debug details';
}

// ---------- Helpers ----------
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function formatDuration(s) {
  if (!s) return '0s';
  if (s < 60) return s.toFixed(1) + 's';
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(0);
  return m + 'm ' + sec + 's';
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Review-mode HTML template additions
# ---------------------------------------------------------------------------

_REVIEW_CSS = """\
/* ---------- Review mode ---------- */
.review-banner {
  background: linear-gradient(135deg, #1e40af 0%, #7c3aed 100%);
  color: #fff; padding: 14px 20px; border-radius: 10px; margin-bottom: 20px;
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;
}
.review-banner .title { font-weight: 700; font-size: 15px; }
.review-banner .meta { font-size: 13px; opacity: .85; }
.badge.review-classified { background: #dbeafe; color: #1e40af; }
.badge.review-agent_error { background: #fee2e2; color: #991b1b; }
.badge.review-gt_data_needs_correction { background: #fef3c7; color: #92400e; }
.badge.review-gt_url_needs_correction { background: #fef3c7; color: #92400e; }
.badge.review-link_generation_issue { background: #fce7f3; color: #9d174d; }
.badge.review-reviewed_ok { background: #dcfce7; color: #166534; }

/* Review toggle (mirrors debug-toggle styling) */
.review-toggle {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .5px;
  margin-top: 6px; padding: 6px 0; user-select: none; border: none; background: none;
  width: fit-content;
}
.review-toggle::before {
  content: ''; display: inline-block; width: 0; height: 0;
  border-left: 5px solid #94a3b8; border-top: 4px solid transparent;
  border-bottom: 4px solid transparent; transition: transform .15s;
}
.review-toggle.open::before { transform: rotate(90deg); }
.review-drawer { display: none; }
.review-drawer.open { display: block; }

/* Review panel */
.review-panel {
  margin-top: 8px; padding: 14px 16px; background: #f0f4ff;
  border-radius: 8px; border: 1px solid #c7d2fe;
}
.review-panel h4 {
  font-size: 12px; text-transform: uppercase; letter-spacing: .5px;
  color: #4338ca; margin-bottom: 10px;
}

/* GT editor sections */
.gt-editor-section {
  margin-top: 12px; padding: 12px 14px; background: #f8fafc;
  border-radius: 6px; border: 1px solid #e2e8f0;
}
.gt-editor-section h5 {
  font-size: 11px; text-transform: uppercase; letter-spacing: .5px;
  color: #64748b; margin-bottom: 8px; font-weight: 600;
}
.review-panel label { font-size: 13px; color: #475569; display: block; margin-bottom: 4px; }
.review-panel select, .review-panel input[type="text"], .review-panel textarea {
  width: 100%; padding: 8px 10px; border: 1px solid #c7d2fe; border-radius: 6px;
  font-size: 13px; font-family: inherit; background: #fff; color: #1e293b;
}
.review-panel textarea { min-height: 200px; font-family: 'SF Mono', Consolas, monospace; font-size: 12px; resize: vertical; }
.review-panel .btn-row { display: flex; gap: 8px; margin-top: 10px; align-items: center; }
.review-panel .btn {
  padding: 7px 16px; border-radius: 6px; border: none; font-size: 13px; font-weight: 600;
  cursor: pointer; transition: all .15s;
}
.review-panel .btn-primary { background: #4338ca; color: #fff; }
.review-panel .btn-primary:hover { background: #3730a3; }
.review-panel .btn-secondary { background: #e0e7ff; color: #4338ca; }
.review-panel .btn-secondary:hover { background: #c7d2fe; }
.review-panel .btn-success { background: #059669; color: #fff; }
.review-panel .btn-success:hover { background: #047857; }
.review-panel .btn-danger { background: #dc2626; color: #fff; }
.review-panel .btn-danger:hover { background: #b91c1c; }
.review-panel .btn:disabled { opacity: .5; cursor: not-allowed; }
.review-panel .status-msg { font-size: 12px; color: #059669; }
.review-panel .error-msg { font-size: 12px; color: #dc2626; }

/* GT preview table */
.gt-preview-table {
  margin-top: 8px; max-height: 300px; overflow: auto;
  border: 1px solid #e2e8f0; border-radius: 6px; background: #fff;
}
.gt-preview-table table { border-collapse: collapse; width: 100%; font-size: 12px; }
.gt-preview-table th {
  text-align: left; padding: 6px 8px; border-bottom: 2px solid #e2e8f0;
  font-size: 11px; color: #64748b; background: #f8fafc; position: sticky; top: 0;
}
.gt-preview-table td { padding: 4px 8px; border-bottom: 1px solid #f1f5f9; }

/* Collapsible editor sections */
.editor-toggle {
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 12px; color: #4338ca; font-weight: 600; text-transform: uppercase;
  letter-spacing: .5px; margin-top: 10px; padding: 6px 0; border: none; background: none;
}
.editor-toggle::before {
  content: ''; display: inline-block; width: 0; height: 0;
  border-left: 5px solid #6366f1; border-top: 4px solid transparent;
  border-bottom: 4px solid transparent; transition: transform .15s;
}
.editor-toggle.open::before { transform: rotate(90deg); }
.editor-body { display: none; margin-top: 8px; }
.editor-body.open { display: block; }

/* Spinner */
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #c7d2fe;
  border-top-color: #4338ca; border-radius: 50%; animation: spin .6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Toast notifications */
.toast-container {
  position: fixed; top: 20px; right: 20px; z-index: 9999;
  display: flex; flex-direction: column; gap: 8px;
}
.toast {
  padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 500;
  box-shadow: 0 4px 12px rgba(0,0,0,.15); animation: slideIn .3s ease;
  max-width: 400px;
}
.toast.success { background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }
.toast.error { background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }
@keyframes slideIn { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
"""

_REVIEW_JS = """\
// ========== Review Mode JavaScript ==========
let REVIEWER = null;

function showToast(message, type) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity .3s'; setTimeout(() => toast.remove(), 300); }, 4000);
}

async function apiCall(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'Request failed');
  }
  return res.json();
}

async function loadReviewer() {
  if (REVIEWER) return REVIEWER;
  try {
    REVIEWER = await apiCall('GET', '/api/reviewer');
  } catch(e) {
    REVIEWER = { name: '', email: '', display: 'anonymous' };
  }
  return REVIEWER;
}

function getReviewedCount() {
  return (REPORT.per_question || []).filter(q => q.review && q.review.classification).length;
}

function updateReviewBanner() {
  const banner = document.getElementById('review-banner-meta');
  if (banner) {
    const count = getReviewedCount();
    const total = (REPORT.per_question || []).length;
    banner.textContent = count + ' of ' + total + ' questions classified';
  }
}

function classificationBadgeHTML(review) {
  if (!review || !review.classification) return '';
  const cls = review.classification;
  return '<span class="badge review-' + esc(cls) + '">' + esc(cls.replace(/_/g, ' ')) + '</span>';
}

// Override renderQuestions to add classification badges
const _origRenderQuestions = renderQuestions;
renderQuestions = function(questions) {
  _origRenderQuestions(questions);
  // Inject classification badges into card headers
  questions = questions || REPORT.per_question || [];
  questions.forEach((q, i) => {
    if (q.review && q.review.classification) {
      const card = document.getElementById('qcard-' + i);
      if (card) {
        const badges = card.querySelector('.badges');
        if (badges && !badges.querySelector('.review-classified, .review-agent_error, .review-gt_data_needs_correction, .review-gt_url_needs_correction, .review-link_generation_issue, .review-reviewed_ok')) {
          badges.insertAdjacentHTML('beforeend', ' ' + classificationBadgeHTML(q.review));
        }
      }
    }
  });
};

// Override buildDetailHTML to add review panels
const _origBuildDetailHTML = buildDetailHTML;
buildDetailHTML = function(q) {
  let html = _origBuildDetailHTML(q);
  html += buildReviewPanel(q);
  return html;
};

let _reviewIdCounter = 0;

function buildReviewPanel(q) {
  const qid = q.question_id;
  const review = q.review || {};
  const currentClass = review.classification || '';
  const currentNote = review.note || '';
  const rid = 'review-' + (_reviewIdCounter++);

  // Collapsible toggle (like debug details)
  let html = '<button class="review-toggle" onclick="toggleReview(\\'' + rid + '\\',this)">Review actions</button>';
  html += '<div class="review-drawer" id="' + rid + '">';
  html += '<div class="review-panel" id="review-panel-' + esc(qid) + '">';

  // Classification dropdown
  html += '<div style="margin-bottom: 12px;">';
  html += '<label>Classification</label>';
  html += '<div style="display:flex;gap:8px;align-items:center;">';
  html += '<select id="classify-select-' + esc(qid) + '" style="flex:1;">';
  html += '<option value="">-- Select --</option>';
  const options = ['agent_error', 'gt_data_needs_correction', 'gt_url_needs_correction', 'link_generation_issue', 'reviewed_ok'];
  for (const opt of options) {
    const sel = opt === currentClass ? ' selected' : '';
    html += '<option value="' + opt + '"' + sel + '>' + opt.replace(/_/g, ' ') + '</option>';
  }
  html += '</select>';
  html += '<input type="text" id="classify-note-' + esc(qid) + '" placeholder="Note (optional)" value="' + esc(currentNote) + '" style="flex:1;">';
  html += '<button class="btn btn-primary" onclick="saveClassification(\\'' + esc(qid) + '\\')">Save</button>';
  html += '</div>';
  if (review.reviewed_by) {
    html += '<p style="font-size:11px;color:#94a3b8;margin-top:4px;">Last reviewed by ' + esc(review.reviewed_by) + ' at ' + esc(review.reviewed_at || '') + '</p>';
  }
  html += '</div>';

  // GT Data Editor (in its own section)
  if (q.ground_truth && q.ground_truth.length > 0) {
    html += '<div class="gt-editor-section">';
    html += '<h5>Ground Truth Data</h5>';
    html += '<button class="editor-toggle" onclick="toggleEditor(\\'' + esc(qid) + '-gt\\', this)">Edit Ground Truth Data</button>';
    html += '<div class="editor-body" id="editor-' + esc(qid) + '-gt">';
    html += '<textarea id="gt-editor-' + esc(qid) + '" oninput="previewGT(\\'' + esc(qid) + '\\')">' + esc(JSON.stringify(q.ground_truth, null, 2)) + '</textarea>';
    html += '<div class="gt-preview-table" id="gt-preview-' + esc(qid) + '"></div>';
    html += '<label style="margin-top:8px;">Correction note (required)</label>';
    html += '<input type="text" id="gt-note-' + esc(qid) + '" placeholder="Describe what changed and why">';
    html += '<div class="btn-row">';
    html += '<button class="btn btn-primary" onclick="saveGTCorrection(\\'' + esc(qid) + '\\')">Save GT Correction</button>';
    html += '<span id="gt-status-' + esc(qid) + '"></span>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
  }

  // GT URL Editor (in its own section)
  if (q.ground_truth_atlas_url) {
    html += '<div class="gt-editor-section">';
    html += '<h5>Ground Truth URL</h5>';
    html += '<button class="editor-toggle" onclick="toggleEditor(\\'' + esc(qid) + '-url\\', this)">Edit Ground Truth URL</button>';
    html += '<div class="editor-body" id="editor-' + esc(qid) + '-url">';
    html += '<label>Atlas URL</label>';
    html += '<input type="text" id="gt-url-editor-' + esc(qid) + '" value="' + esc(q.ground_truth_atlas_url) + '">';
    html += '<label style="margin-top:8px;">Correction note (required)</label>';
    html += '<input type="text" id="gt-url-note-' + esc(qid) + '" placeholder="Describe what changed and why">';
    html += '<div class="btn-row">';
    html += '<button class="btn btn-primary" onclick="saveGTUrlCorrection(\\'' + esc(qid) + '\\')">Save URL Correction</button>';
    html += '<span id="gt-url-status-' + esc(qid) + '"></span>';
    html += '</div>';
    html += '</div>';
    html += '</div>';
  }

  // Re-judge button
  html += '<div style="margin-top:12px;padding-top:12px;border-top:1px solid #c7d2fe;">';
  html += '<button class="btn btn-success" id="rejudge-btn-' + esc(qid) + '" onclick="rejudge(\\'' + esc(qid) + '\\')">Re-judge with current GT</button>';
  html += ' <span id="rejudge-status-' + esc(qid) + '"></span>';
  html += '</div>';

  // Delete question button
  html += '<div style="margin-top:12px;padding-top:12px;border-top:1px solid #fca5a5;">';
  html += '<button class="btn btn-danger" id="delete-btn-' + esc(qid) + '" onclick="deleteQuestion(\\'' + esc(qid) + '\\')">Delete question from eval set</button>';
  html += ' <span id="delete-status-' + esc(qid) + '"></span>';
  html += '</div>';

  html += '</div>'; // close review-panel
  html += '</div>'; // close review-drawer
  return html;
}

function toggleReview(id, btn) {
  const el = document.getElementById(id);
  el.classList.toggle('open');
  btn.classList.toggle('open');
  btn.textContent = el.classList.contains('open') ? 'Hide review actions' : 'Review actions';
}

function toggleEditor(id, btn) {
  const el = document.getElementById('editor-' + id);
  el.classList.toggle('open');
  btn.classList.toggle('open');
}

function previewGT(qid) {
  const textarea = document.getElementById('gt-editor-' + qid);
  const preview = document.getElementById('gt-preview-' + qid);
  try {
    const data = JSON.parse(textarea.value);
    if (!Array.isArray(data) || data.length === 0) {
      preview.innerHTML = '<p style="padding:8px;color:#94a3b8;font-size:12px;">Empty or invalid array</p>';
      return;
    }
    const cols = Object.keys(data[0]);
    let tableHTML = '<table><thead><tr>' + cols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>';
    for (const row of data) {
      tableHTML += '<tr>' + cols.map(c => '<td>' + esc(String(row[c] ?? '')) + '</td>').join('') + '</tr>';
    }
    tableHTML += '</tbody></table>';
    preview.innerHTML = tableHTML;
    textarea.style.borderColor = '#c7d2fe';
  } catch(e) {
    preview.innerHTML = '<p style="padding:8px;color:#dc2626;font-size:12px;">Invalid JSON: ' + esc(e.message) + '</p>';
    textarea.style.borderColor = '#fca5a5';
  }
}

async function saveClassification(qid) {
  const select = document.getElementById('classify-select-' + qid);
  const noteInput = document.getElementById('classify-note-' + qid);
  const classification = select.value;
  if (!classification) { showToast('Please select a classification', 'error'); return; }
  try {
    const result = await apiCall('POST', '/api/classify/' + qid, { classification, note: noteInput.value || null });
    // Update local REPORT data
    const q = (REPORT.per_question || []).find(q => String(q.question_id) === String(qid));
    if (q) q.review = result.review;
    updateReviewBanner();
    showToast('Q' + qid + ' classified as ' + classification.replace(/_/g, ' '), 'success');
    // Force re-render of questions to update badges
    renderQuestions(getFilteredQuestions());
  } catch(e) {
    showToast('Error: ' + e.message, 'error');
  }
}

async function saveGTCorrection(qid) {
  const textarea = document.getElementById('gt-editor-' + qid);
  const noteInput = document.getElementById('gt-note-' + qid);
  const statusEl = document.getElementById('gt-status-' + qid);
  if (!noteInput.value.trim()) { showToast('A correction note is required', 'error'); return; }
  let data;
  try { data = JSON.parse(textarea.value); }
  catch(e) { showToast('Invalid JSON: ' + e.message, 'error'); return; }
  if (!Array.isArray(data)) { showToast('Data must be a JSON array', 'error'); return; }

  statusEl.innerHTML = '<span class="spinner"></span>';
  try {
    const result = await apiCall('POST', '/api/correct-gt/' + qid, { data, note: noteInput.value });
    // Update local data
    const q = (REPORT.per_question || []).find(q => String(q.question_id) === String(qid));
    if (q) q.ground_truth = data;
    statusEl.innerHTML = '<span class="status-msg">Saved (' + result.archived_count + ' rows archived, ' + result.new_count + ' new)</span>';
    showToast('Q' + qid + ' ground truth data corrected', 'success');
  } catch(e) {
    statusEl.innerHTML = '<span class="error-msg">' + esc(e.message) + '</span>';
    showToast('Error: ' + e.message, 'error');
  }
}

async function saveGTUrlCorrection(qid) {
  const urlInput = document.getElementById('gt-url-editor-' + qid);
  const noteInput = document.getElementById('gt-url-note-' + qid);
  const statusEl = document.getElementById('gt-url-status-' + qid);
  if (!noteInput.value.trim()) { showToast('A correction note is required', 'error'); return; }
  if (!urlInput.value.trim()) { showToast('URL cannot be empty', 'error'); return; }

  statusEl.innerHTML = '<span class="spinner"></span>';
  try {
    const result = await apiCall('POST', '/api/correct-gt-url/' + qid, { atlas_url: urlInput.value, note: noteInput.value });
    // Update local data
    const q = (REPORT.per_question || []).find(q => String(q.question_id) === String(qid));
    if (q) q.ground_truth_atlas_url = urlInput.value;
    statusEl.innerHTML = '<span class="status-msg">Saved</span>';
    showToast('Q' + qid + ' ground truth URL corrected', 'success');
  } catch(e) {
    statusEl.innerHTML = '<span class="error-msg">' + esc(e.message) + '</span>';
    showToast('Error: ' + e.message, 'error');
  }
}

async function rejudge(qid) {
  const btn = document.getElementById('rejudge-btn-' + qid);
  const statusEl = document.getElementById('rejudge-status-' + qid);
  btn.disabled = true;
  statusEl.innerHTML = '<span class="spinner"></span> Re-judging...';
  try {
    const result = await apiCall('POST', '/api/rejudge/' + qid);
    // Update local REPORT data
    const q = (REPORT.per_question || []).find(q => String(q.question_id) === String(qid));
    if (q) {
      q.verdict = result.verdict;
      q.weighted_score = result.weighted_score;
      q.judge_details = result.judge_details;
      q.judge_comment = result.judge_details.overall_comment || '';
      q.judge_mode = result.judge_details.judge_mode || q.judge_mode;
      if (result.link_verdict) q.link_judge = result.link_verdict;
    }
    statusEl.innerHTML = '<span class="status-msg">New verdict: <strong>' + esc(result.verdict) + '</strong> (' + (result.pass_count != null ? result.pass_count : (result.weighted_score || 0)) + '/4)</span>';
    showToast('Q' + qid + ' re-judged: ' + result.verdict, 'success');
    // Force detail re-render on next toggle
    const questions = getFilteredQuestions();
    const idx = questions.findIndex(q => String(q.question_id) === String(qid));
    if (idx >= 0) {
      const detail = document.getElementById('detail-' + idx);
      if (detail) { detail.dataset.rendered = ''; detail.innerHTML = ''; }
    }
    renderQuestions(getFilteredQuestions());
  } catch(e) {
    statusEl.innerHTML = '<span class="error-msg">' + esc(e.message) + '</span>';
    showToast('Re-judge error: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

function recalcAggregates() {
  const qs = REPORT.per_question || [];
  const judged = qs.filter(q => q.verdict);
  const count = judged.length;
  const passCount = judged.filter(q => q.verdict === 'pass').length;
  const partialCount = judged.filter(q => q.verdict === 'partial').length;
  const failCount = judged.filter(q => q.verdict === 'fail').length;
  const passRate = count > 0 ? Math.round((passCount / count) * 1000) / 10 : 0;
  const totalPC = judged.reduce((s, q) => s + (q.pass_count != null ? q.pass_count : (q.weighted_score || 0)), 0);
  const avgPC = count > 0 ? Math.round((totalPC / count) * 10) / 10 : 0;

  REPORT.aggregate = Object.assign(REPORT.aggregate || {}, {
    count: qs.length,
    pass_count: passCount,
    partial_count: partialCount,
    fail_count: failCount,
    pass_rate: passRate,
    avg_pass_count: avgPC,
  });
}

async function deleteQuestion(qid) {
  if (!confirm('Permanently delete question ' + qid + ' from the eval set?\\n\\nThis will remove it from eval_questions.json and delete its results folder. This cannot be undone.')) {
    return;
  }
  const btn = document.getElementById('delete-btn-' + qid);
  const statusEl = document.getElementById('delete-status-' + qid);
  btn.disabled = true;
  statusEl.innerHTML = '<span class="spinner"></span> Deleting...';
  try {
    const result = await apiCall('DELETE', '/api/question/' + qid);
    // Remove from local REPORT data
    REPORT.per_question = (REPORT.per_question || []).filter(q => String(q.question_id) !== String(qid));
    recalcAggregates();
    renderDashboard();
    renderQuestions(getFilteredQuestions());
    updateReviewBanner();
    showToast('Question ' + qid + ' deleted (' + result.eval_questions_count + ' questions remaining)', 'success');
  } catch(e) {
    statusEl.innerHTML = '<span class="error-msg">' + esc(e.message) + '</span>';
    showToast('Delete error: ' + e.message, 'error');
    btn.disabled = false;
  }
}

// Initialize review banner on load
document.addEventListener('DOMContentLoaded', async () => {
  const reviewer = await loadReviewer();
  const banner = document.getElementById('review-banner');
  if (banner) {
    const nameEl = document.getElementById('review-banner-reviewer');
    if (nameEl) nameEl.textContent = 'Reviewer: ' + reviewer.display;
  }
  updateReviewBanner();
  // Trigger initial GT preview for any open editors
});
"""


def generate_review_html(run_dir: Path) -> str:
    """Generate a review-mode HTML report as a string (served by review_server).

    Extends the static report with classification, GT editing, and re-judge UI.

    Args:
        run_dir: Path to the timestamped run directory containing report.json.

    Returns:
        Complete HTML string.
    """
    report_json = run_dir / "report.json"
    if not report_json.exists():
        raise FileNotFoundError(f"No report.json found in {run_dir}")

    enriched = _load_enriched_data(run_dir)
    json_blob = json.dumps(enriched, indent=None, default=str)

    # Build the review HTML by injecting review CSS and JS into the static template
    html = _HTML_TEMPLATE.replace("__REPORT_JSON__", json_blob)

    # Inject review CSS before </style>
    html = html.replace("</style>", _REVIEW_CSS + "\n</style>", 1)

    # Inject review banner after <body>
    review_banner = (
        '\n<div class="review-banner" id="review-banner">'
        '<span class="title">Review Mode</span>'
        '<span class="meta" id="review-banner-meta">Loading...</span>'
        '<span class="meta" id="review-banner-reviewer"></span>'
        "</div>\n"
    )
    html = html.replace(
        '<h1 style="margin-bottom: 6px;">Ask Atlas',
        review_banner + '<h1 style="margin-bottom: 6px;">Ask Atlas',
        1,
    )

    # Inject review JS before the final </script> (not the marked.min.js one)
    # Find the last </script> which closes the main inline script block
    last_script_close = html.rfind("</script>")
    html = (
        html[:last_script_close] + "\n" + _REVIEW_JS + "\n" + html[last_script_close:]
    )

    return html


def generate_html_report(run_dir: Path) -> Path:
    """Generate a self-contained HTML report from a completed eval run.

    Args:
        run_dir: Path to the timestamped run directory containing report.json
                 and per-question subdirectories.

    Returns:
        Path to the generated HTML file.
    """
    report_json = run_dir / "report.json"
    if not report_json.exists():
        raise FileNotFoundError(f"No report.json found in {run_dir}")

    enriched = _load_enriched_data(run_dir)
    json_blob = json.dumps(enriched, indent=None, default=str)

    html_content = _HTML_TEMPLATE.replace("__REPORT_JSON__", json_blob)

    html_path = run_dir / "report.html"
    html_path.write_text(html_content, encoding="utf-8")
    logger.info("Saved HTML report: %s", html_path)
    return html_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) < 2:
        logger.error("Usage: python html_report.py <run_dir>")
        sys.exit(1)
    run_dir = Path(sys.argv[1])
    generate_html_report(run_dir)
    logger.info("HTML report generated: %s", run_dir / "report.html")
