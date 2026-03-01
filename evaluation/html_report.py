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
            except Exception:
                entry["ground_truth"] = None
        else:
            entry["ground_truth"] = None

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
    { label: 'Avg Score', value: (a.avg_weighted_score || 0).toFixed(2), sub: 'out of 5.0' },
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

  // Add dimension averages
  for (const [dim, score] of Object.entries(dims)) {
    stats.push({ label: dim.replace(/_/g, ' '), value: score.toFixed(2), sub: '/5.0' });
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
    data = buildBreakdown(q => {
      const tools = q.tools_used || [];
      if (tools.length === 0) return 'unknown';
      if (tools.includes('atlas_graphql') && tools.includes('query_tool')) return 'mixed';
      if (tools.includes('atlas_graphql')) return 'graphql';
      if (tools.includes('query_tool')) return 'sql';
      return tools.join('+');
    });
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
      <thead><tr><th>${esc(tab === 'pipeline' ? 'Pipeline' : tab.charAt(0).toUpperCase() + tab.slice(1))}</th><th>Count</th><th>Avg Score</th><th>Pass Rate</th><th>Pass</th><th>Partial</th><th>Fail</th></tr></thead>
      <tbody>${Object.entries(data).sort((a,b) => a[0].localeCompare(b[0])).map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${v.count}</td><td>${(v.avg_weighted_score || 0).toFixed(2)}</td><td>${(v.pass_rate || 0).toFixed(1)}%</td><td>${v.pass_count || 0}</td><td>${v.partial_count || 0}</td><td>${v.fail_count || 0}</td></tr>`
      ).join('')}</tbody>
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
          <span class="badge score">${(q.weighted_score || 0).toFixed(1)}/5</span>
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

function buildDetailHTML(q) {
  let sections = '';

  // Agent answer (rendered markdown)
  if (q.agent_answer) {
    let rendered;
    try { rendered = marked.parse(q.agent_answer); }
    catch(e) { rendered = '<pre>' + esc(q.agent_answer) + '</pre>'; }
    sections += `
    <div class="detail-section">
      <h4>Agent Answer</h4>
      <div class="content md-rendered">${rendered}</div>
    </div>`;
  }

  // Expected behavior (for refusal questions)
  if (q.expected_behavior) {
    sections += `
    <div class="detail-section">
      <h4>Expected Behavior</h4>
      <div class="content">${esc(q.expected_behavior)}</div>
    </div>`;
  }

  // Ground truth table
  if (q.ground_truth && q.ground_truth.length > 0) {
    const cols = Object.keys(q.ground_truth[0]);
    sections += `
    <div class="detail-section">
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

  // SQL
  if (q.sql) {
    sections += `
    <div class="detail-section">
      <h4>SQL Query</h4>
      <pre>${esc(q.sql)}</pre>
    </div>`;
  }

  // Tools used
  if (q.tools_used && q.tools_used.length > 0) {
    sections += `
    <div class="detail-section">
      <h4>Tools Used</h4>
      <div class="tools-list">${q.tools_used.map(t => `<span class="tool-badge">${esc(t)}</span>`).join('')}</div>
    </div>`;
  }

  // Judge commentary
  if (q.judge_comment) {
    sections += `
    <div class="detail-section">
      <h4>Judge Commentary</h4>
      <div class="content">${esc(q.judge_comment)}</div>
    </div>`;
  }

  // Dimension scores (for ground_truth judge mode)
  const jd = q.judge_details || {};
  const dims = ['factual_correctness', 'data_accuracy', 'completeness', 'reasoning_quality'];
  const hasDims = dims.some(d => jd[d] && typeof jd[d] === 'object' && jd[d].score != null);
  if (hasDims) {
    sections += `
    <div class="detail-section">
      <h4>Dimension Scores</h4>
      <div class="content">
        <div class="dim-bars">${dims.filter(d => jd[d] && jd[d].score != null).map(d => {
          const score = jd[d].score;
          const pct = score / 5 * 100;
          const cls = score >= 4 ? 'high' : score >= 3 ? 'mid' : 'low';
          const label = d.replace(/_/g, ' ');
          return `<div class="dim-bar">
            <span class="dim-label">${esc(label)}</span>
            <div class="bar-bg"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
            <span class="dim-score">${score}/5</span>
          </div>`;
        }).join('')}</div>
        ${dims.filter(d => jd[d] && jd[d].reasoning).map(d =>
          `<p style="font-size:12px;color:#64748b;margin-top:6px;"><strong>${esc(d.replace(/_/g,' '))}:</strong> ${esc(jd[d].reasoning)}</p>`
        ).join('')}
      </div>
    </div>`;
  }

  // Refusal-specific details
  if (jd.judge_mode === 'refusal') {
    sections += `
    <div class="detail-section">
      <h4>Refusal Evaluation</h4>
      <div class="content">
        <p>Appropriate refusal: <strong>${jd.appropriate_refusal ? 'Yes' : 'No'}</strong></p>
        <p>Graceful: <strong>${jd.graceful ? 'Yes' : 'No'}</strong></p>
        ${jd.reasoning ? '<p>' + esc(jd.reasoning) + '</p>' : ''}
      </div>
    </div>`;
  }

  // Duration
  if (q.duration_s != null) {
    sections += `
    <div class="detail-section">
      <h4>Duration</h4>
      <div class="content">${formatDuration(q.duration_s)}</div>
    </div>`;
  }

  // Step timing waterfall
  const steps = q.step_timing || [];
  if (steps.length > 0) {
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
    sections += `
    <div class="detail-section">
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

  return sections;
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
    logging.info(f"Saved HTML report: {html_path}")
    return html_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python html_report.py <run_dir>")
        sys.exit(1)
    run_dir = Path(sys.argv[1])
    generate_html_report(run_dir)
    print(f"HTML report generated: {run_dir / 'report.html'}")
