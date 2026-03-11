import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { ReasoningTraceEntry, ReasoningTraceToolCall } from '@/types/chat';
import { cn } from '@/lib/utils';

interface GraphqlReasoningTraceProps {
  entries: Array<ReasoningTraceEntry>;
  isActive: boolean;
}

/** One tool action in the correction loop. */
interface Action {
  assistantContent: string;
  resultContent: string;
  status: 'error' | 'success';
  statusLine: string;
  toolArgs: Record<string, unknown>;
  toolName: string;
}

const TOOL_LABELS: Record<string, string> = {
  execute_graphql_freeform: 'Freeform query',
  execute_graphql_template: 'Template query',
  explore_catalog: 'Catalog lookup',
  introspect_schema: 'Schema introspection',
};

const GRAPHQL_TOOL_NAMES = new Set([
  'execute_graphql_freeform',
  'execute_graphql_template',
  'explore_catalog',
  'introspect_schema',
]);

/** Walk an object tree (max 3 levels) and return the first array found with its key and length. */
function findFirstArray(obj: unknown, depth = 0): { count: number; key: string } | null {
  if (depth > 3 || !obj || typeof obj !== 'object' || Array.isArray(obj)) {
    return null;
  }
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (Array.isArray(v)) {
      return { count: v.length, key: k };
    }
    const nested = findFirstArray(v, depth + 1);
    if (nested) {
      return nested;
    }
  }
  return null;
}

/** Derive a human-readable one-liner from a tool result, replacing raw JSON dumps. */
function summarizeToolResult(content: string): { isError: boolean; summary: string } {
  const isError = content.toLowerCase().startsWith('error') || content.includes('[Error]');

  if (isError) {
    // Extract the GraphQL error message from the JSON envelope
    const msgMatch = content.match(/"message"\s*:\s*"([^"]{1,100})/);
    const raw = msgMatch?.[1] ?? content.split('\n')[0] ?? content;
    return { isError: true, summary: raw.length > 100 ? raw.slice(0, 100) + '\u2026' : raw };
  }

  // Try full JSON parse
  try {
    const parsed = JSON.parse(content) as Record<string, unknown>;

    // Schema introspection: { __type: { name, fields: [...] } }
    const typeInfo = (parsed.__type ??
      (parsed.data as Record<string, unknown> | undefined)?.__type) as
      | Record<string, unknown>
      | undefined;
    if (typeInfo?.name && Array.isArray(typeInfo.fields)) {
      return {
        isError: false,
        summary: `${typeInfo.name as string}: ${(typeInfo.fields as Array<unknown>).length} fields`,
      };
    }

    // Data response: find first array
    const arr = findFirstArray(parsed);
    if (arr) {
      return {
        isError: false,
        summary: `${arr.key}: ${arr.count} row${arr.count === 1 ? '' : 's'}`,
      };
    }
  } catch {
    // Truncated JSON — estimate via regex
    if (content.includes('"__type"')) {
      const nameMatch = content.match(/"name"\s*:\s*"(\w+)"/);
      if (nameMatch) {
        return { isError: false, summary: `Schema: ${nameMatch[1]}` };
      }
    }
    const keyMatch = content.match(/"(\w+)"\s*:\s*\[/);
    if (keyMatch) {
      const separators = content.match(/\}\s*,\s*\{/g);
      const count = separators ? separators.length + 1 : 1;
      return { isError: false, summary: `${keyMatch[1]}: ~${count} rows` };
    }
  }

  const line = content.split('\n')[0] ?? content;
  return { isError: false, summary: line.length > 100 ? line.slice(0, 100) + '\u2026' : line };
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

function countToolCalls(entries: Array<ReasoningTraceEntry>): number {
  return entries.filter(
    (e) =>
      e.role === 'tool' && typeof e.tool_name === 'string' && GRAPHQL_TOOL_NAMES.has(e.tool_name),
  ).length;
}

/** Group trace entries into actions (each GraphQL tool call = one action). */
function buildActions(entries: Array<ReasoningTraceEntry>): Array<Action> {
  const actions: Array<Action> = [];
  let currentAssistant = '';
  let currentToolName = '';
  let currentToolArgs: Record<string, unknown> = {};

  for (const entry of entries) {
    if (entry.role === 'assistant') {
      if (entry.content) {
        currentAssistant += (currentAssistant ? '\n' : '') + entry.content;
      }
      if (entry.tool_calls) {
        const graphqlCall = entry.tool_calls.find((tc) => GRAPHQL_TOOL_NAMES.has(tc.name));
        if (graphqlCall) {
          currentToolName = graphqlCall.name;
          currentToolArgs = graphqlCall.args;
          const reasoning =
            typeof graphqlCall.args.reasoning === 'string' ? graphqlCall.args.reasoning : '';
          if (reasoning) {
            currentAssistant += (currentAssistant ? '\n' : '') + reasoning;
          }
        }
      }
    } else if (
      entry.role === 'tool' &&
      typeof entry.tool_name === 'string' &&
      GRAPHQL_TOOL_NAMES.has(entry.tool_name)
    ) {
      const content = typeof entry.content === 'string' ? entry.content : '';
      const { isError, summary } = summarizeToolResult(content);
      actions.push({
        assistantContent: currentAssistant,
        resultContent: content,
        status: isError ? 'error' : 'success',
        statusLine: summary,
        toolArgs: currentToolArgs,
        toolName: currentToolName || entry.tool_name,
      });
      currentAssistant = '';
      currentToolName = '';
      currentToolArgs = {};
    } else if (entry.role === 'tool') {
      const label = entry.tool_name ?? 'tool';
      const content = typeof entry.content === 'string' ? entry.content : '';
      currentAssistant += (currentAssistant ? '\n' : '') + `[${label}] ${content}`;
    }
  }

  return actions;
}

function CollapsibleSection({ children, label }: { children: React.ReactNode; label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        className="flex items-center gap-1 rounded px-1 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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

function ReasoningContent({ content }: { content: string }) {
  if (!content) {
    return null;
  }
  if (content.length <= 200) {
    return (
      <p className="mt-0.5 text-[10px] leading-tight whitespace-pre-wrap text-muted-foreground">
        {content}
      </p>
    );
  }
  return (
    <CollapsibleSection label="Agent reasoning">
      <div className="mt-0.5 max-h-64 overflow-auto rounded bg-muted p-1.5 text-[10px] leading-tight whitespace-pre-wrap text-muted-foreground">
        {content}
      </div>
    </CollapsibleSection>
  );
}

function ActionBlock({ action, index, total }: { action: Action; index: number; total: number }) {
  const label = TOOL_LABELS[action.toolName] ?? action.toolName;
  const queryType =
    typeof action.toolArgs.query_type === 'string' ? action.toolArgs.query_type : null;

  return (
    <div className="flex gap-2 py-0.5">
      <div className="flex flex-col items-center pt-[5px]">
        <div
          className={cn(
            'h-1.5 w-1.5 rounded-full',
            action.status === 'success' ? 'bg-info' : 'bg-destructive',
          )}
        />
        {index < total - 1 && <div className="mt-0.5 w-px flex-1 bg-border" />}
      </div>

      <div className="min-w-0 flex-1 pb-1">
        <div className="flex items-baseline gap-1.5">
          <span className="text-[10px] font-medium whitespace-nowrap text-foreground">
            {label}
            {queryType ? ` (${queryType})` : ''}
          </span>
          <span
            className={cn(
              'min-w-0 truncate text-[10px]',
              action.status === 'error' ? 'text-destructive' : 'text-muted-foreground',
            )}
          >
            {'\u2014'} {action.statusLine}
          </span>
        </div>

        <ReasoningContent content={action.assistantContent} />

        {action.resultContent.length > 200 && (
          <CollapsibleSection label="Full result">
            <pre className="mt-0.5 max-h-48 overflow-auto rounded bg-muted p-1.5 font-mono text-[10px] leading-tight text-muted-foreground">
              {action.resultContent}
            </pre>
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}

export default function GraphqlReasoningTrace({ entries, isActive }: GraphqlReasoningTraceProps) {
  const [expanded, setExpanded] = useState(false);
  const isOpen = isActive || expanded;

  const assessment = extractAssessment(entries);
  const actionCount = countToolCalls(entries);
  const actions = isOpen ? buildActions(entries) : [];

  const summaryText = assessment
    ? assessment.length > 100
      ? assessment.slice(0, 100) + '\u2026'
      : assessment
    : actionCount > 0
      ? `GraphQL correction: ${actionCount} action${actionCount === 1 ? '' : 's'}`
      : 'GraphQL correction agent';

  return (
    <div className="mt-1 ml-[18px]">
      <button
        className="flex items-center gap-1 text-[11px] text-info hover:text-info/80"
        onClick={() => setExpanded((prev) => !prev)}
        type="button"
      >
        <ChevronRight className={cn('h-3 w-3 transition-transform', isOpen && 'rotate-90')} />
        {summaryText}
      </button>
      {isOpen && (
        <div className="mt-1 border-l border-border pl-2">
          {actions.map((action, i) => (
            <ActionBlock action={action} index={i} key={i} total={actions.length} />
          ))}
          {assessment && (
            <div className="mt-1 rounded bg-muted p-1.5">
              <span className="text-[10px] font-medium text-foreground">Assessment</span>
              <p className="mt-0.5 text-[11px] leading-tight text-foreground">{assessment}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
