import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { ReasoningTraceEntry, ReasoningTraceToolCall } from '@/types/chat';
import { cn } from '@/lib/utils';

interface ReasoningTraceProps {
  entries: Array<ReasoningTraceEntry>;
  isActive: boolean;
}

/** A single execute_sql cycle: AI message (with SQL) + tool result. */
interface Iteration {
  assistantContent: string;
  resultContent: string;
  resultData: Array<Array<string>> | null;
  sql: string;
  status: 'error' | 'success';
  statusLine: string;
}

function extractAssessment(entries: Array<ReasoningTraceEntry>): string | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.role === 'assistant' && entry.tool_calls) {
      const reportCall = entry.tool_calls.find(
        (tc: ReasoningTraceToolCall) => tc.name === 'report_results',
      );
      if (reportCall) {
        const assessment = reportCall.args.assessment;
        return typeof assessment === 'string' ? assessment : null;
      }
    }
  }
  return null;
}

/**
 * Parse result content into a status line and optional table data.
 * Format: "Success. N rows returned:\n\ncol1 | col2\nval1 | val2"
 */
function parseResultContent(content: string): {
  data: Array<Array<string>> | null;
  statusLine: string;
} {
  const lines = content.split('\n');
  const statusLine = (lines[0] ?? content).replace(/:$/, '');

  // Look for pipe-separated data after the status line
  const dataLines = lines.slice(1).filter((l) => l.trim() && l.includes('|'));
  if (dataLines.length >= 2) {
    const data = dataLines.map((line) => line.split('|').map((cell) => cell.trim()));
    return { data, statusLine };
  }

  return { data: null, statusLine };
}

/**
 * Group trace entries into iterations (each execute_sql call = one iteration).
 * Non-execute_sql entries (explore_schema, lookup_products) are folded into
 * the assistant reasoning for the next iteration.
 */
function buildIterations(entries: Array<ReasoningTraceEntry>): Array<Iteration> {
  const iterations: Array<Iteration> = [];
  let currentAssistant = '';
  let currentSql = '';

  for (const entry of entries) {
    if (entry.role === 'assistant') {
      if (entry.content) {
        currentAssistant += (currentAssistant ? '\n' : '') + entry.content;
      }
      const sqlCall = entry.tool_calls?.find((tc) => tc.name === 'execute_sql');
      if (sqlCall) {
        currentSql = typeof sqlCall.args.sql === 'string' ? sqlCall.args.sql : '';
        // Extract reasoning from tool call args
        const reasoning = typeof sqlCall.args.reasoning === 'string' ? sqlCall.args.reasoning : '';
        if (reasoning) {
          currentAssistant += (currentAssistant ? '\n' : '') + reasoning;
        }
      }
    } else if (entry.role === 'tool' && entry.tool_name === 'execute_sql') {
      const content = typeof entry.content === 'string' ? entry.content : '';
      const isSuccess = content.startsWith('Success');
      const { data, statusLine } = parseResultContent(content);
      iterations.push({
        assistantContent: currentAssistant,
        resultContent: content,
        resultData: data,
        sql: currentSql,
        status: isSuccess ? 'success' : 'error',
        statusLine,
      });
      currentAssistant = '';
      currentSql = '';
    }
    // explore_schema / lookup_products results: fold into assistant reasoning (un-truncated)
    else if (entry.role === 'tool') {
      const label = entry.tool_name ?? 'tool';
      const content = typeof entry.content === 'string' ? entry.content : '';
      currentAssistant += (currentAssistant ? '\n' : '') + `[${label}] ${content}`;
    }
  }

  return iterations;
}

function countExecuteSqlCalls(entries: Array<ReasoningTraceEntry>): number {
  return entries.filter((e) => e.role === 'tool' && e.tool_name === 'execute_sql').length;
}

function CollapsibleSection({ children, label }: { children: React.ReactNode; label: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="mt-0.5">
      <button
        className="flex items-center gap-1 text-[10px] font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-300"
        onClick={() => setOpen((p) => !p)}
        type="button"
      >
        <ChevronRight className={cn('h-2.5 w-2.5 transition-transform', open && 'rotate-90')} />
        {label}
      </button>
      {open && children}
    </div>
  );
}

function ResultTable({ data }: { data: Array<Array<string>> }) {
  const [header, ...rows] = data;
  if (!header) {
    return null;
  }

  return (
    <div className="mt-0.5 max-h-48 overflow-auto rounded bg-slate-50 dark:bg-slate-800/50">
      <table className="w-full text-left text-[10px]">
        <thead>
          <tr>
            {header.map((cell, i) => (
              <th
                className="border-b border-slate-200 px-1.5 py-0.5 font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300"
                key={i}
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => (
                <td
                  className="border-b border-slate-100 px-1.5 py-0.5 text-slate-500 dark:border-slate-700/50 dark:text-slate-400"
                  key={ci}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReasoningContent({ content }: { content: string }) {
  if (!content) {
    return null;
  }
  // Short reasoning: show inline
  if (content.length <= 200) {
    return (
      <p className="mt-0.5 text-[10px] leading-tight whitespace-pre-wrap text-slate-500 dark:text-slate-400">
        {content}
      </p>
    );
  }
  // Long reasoning: collapsible
  return (
    <CollapsibleSection label="Agent reasoning">
      <div className="mt-0.5 max-h-64 overflow-auto rounded bg-slate-50 p-1.5 text-[10px] leading-tight whitespace-pre-wrap text-slate-500 dark:bg-slate-800/50 dark:text-slate-400">
        {content}
      </div>
    </CollapsibleSection>
  );
}

function IterationBlock({
  index,
  iteration,
  total,
}: {
  index: number;
  iteration: Iteration;
  total: number;
}) {
  const label = total === 1 ? 'Query' : `Query ${String(index + 1)}`;

  return (
    <div className="flex gap-2 py-0.5">
      {/* Timeline dot */}
      <div className="flex flex-col items-center pt-[5px]">
        <div
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            iteration.status === 'success' ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600',
          )}
        />
        {/* Connecting line (except last) */}
        {index < total - 1 && <div className="mt-0.5 w-px flex-1 bg-slate-200 dark:bg-slate-700" />}
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[10px] font-medium whitespace-nowrap text-slate-600 dark:text-slate-300">
            {label}
          </span>
          <span className="text-[10px] text-slate-400 dark:text-slate-500">
            {'\u2014'} {iteration.statusLine}
          </span>
        </div>

        {/* Agent reasoning */}
        <ReasoningContent content={iteration.assistantContent} />

        {/* Result data table */}
        {iteration.resultData && (
          <CollapsibleSection label="Result data">
            <ResultTable data={iteration.resultData} />
          </CollapsibleSection>
        )}

        {/* Collapsible SQL */}
        {iteration.sql && (
          <CollapsibleSection label="SQL query">
            <pre className="mt-0.5 max-h-48 overflow-auto rounded bg-slate-50 p-1.5 font-mono text-[10px] leading-tight text-slate-600 dark:bg-slate-800/50 dark:text-slate-300">
              {iteration.sql}
            </pre>
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}

export default function ReasoningTrace({ entries, isActive }: ReasoningTraceProps) {
  const [expanded, setExpanded] = useState(false);
  const isOpen = isActive || expanded;

  const assessment = extractAssessment(entries);
  const attemptCount = countExecuteSqlCalls(entries);
  const iterations = isOpen ? buildIterations(entries) : [];

  // Build collapsed summary
  const lastIteration = iterations.at(-1);
  const succeeded = lastIteration?.status === 'success';
  const summaryText = assessment
    ? assessment.length > 100
      ? assessment.slice(0, 100) + '\u2026'
      : assessment
    : attemptCount > 0
      ? `SQL agent: ${attemptCount} quer${attemptCount === 1 ? 'y' : 'ies'}, ${succeeded ? 'success' : 'in progress'}`
      : 'SQL agent reasoning';

  return (
    <div className="mt-1 ml-[18px]">
      <button
        className="flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
        onClick={() => setExpanded((prev) => !prev)}
        type="button"
      >
        <ChevronRight className={cn('h-3 w-3 transition-transform', isOpen && 'rotate-90')} />
        {summaryText}
      </button>
      {isOpen && (
        <div className="mt-1 border-l border-slate-300 pl-2 dark:border-slate-600">
          {iterations.map((iter, i) => (
            <IterationBlock index={i} iteration={iter} key={i} total={iterations.length} />
          ))}
          {/* Assessment block */}
          {assessment && (
            <div className="mt-1 rounded bg-slate-100 p-1.5 dark:bg-slate-800/50">
              <span className="text-[10px] font-medium text-slate-600 dark:text-slate-300">
                Assessment
              </span>
              <p className="mt-0.5 text-[11px] leading-tight text-slate-700 dark:text-slate-200">
                {assessment}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
