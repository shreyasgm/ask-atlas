import { Check, ChevronRight, Copy } from 'lucide-react';
import { useState } from 'react';
import type { QueryAggregateStats, QueryResult } from '@/types/chat';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';

interface QueriesTabProps {
  currentQueries: Array<QueryResult>;
  queryStats: QueryAggregateStats | null;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      aria-label="Copy SQL"
      className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
      onClick={handleCopy}
      type="button"
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function QueriesTab({ currentQueries, queryStats }: QueriesTabProps) {
  if (currentQueries.length === 0) {
    return (
      <p className="py-8 text-center text-xs text-muted-foreground">No queries executed yet.</p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <p className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
            Queries
          </p>
          <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            {currentQueries.length}
          </span>
        </div>
      </div>

      {currentQueries.map((query, index) => (
        <QueryCard index={index} key={query.sql} query={query} />
      ))}

      {/* Aggregate footer */}
      {queryStats && (
        <div className="space-y-1.5 rounded-md border border-border bg-card p-3">
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">Total rows</span>
            <span className="font-mono text-[10px]">{queryStats.totalRows}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">Total time</span>
            <span className="font-mono text-[10px]">{formatDuration(queryStats.totalTimeMs)}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-muted-foreground">Execution time</span>
            <span className="font-mono text-[10px]">
              {formatDuration(queryStats.totalExecutionTimeMs)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function QueryCard({ index, query }: { index: number; query: QueryResult }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible onOpenChange={setOpen} open={open}>
      <div className="rounded-md border border-border bg-card">
        <CollapsibleTrigger className="flex w-full items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium">Q{index + 1}</span>
            {query.rowCount > 0 && <Check className="h-3 w-3 text-green-600 dark:text-green-400" />}
            <span className="text-[10px] text-muted-foreground">
              {formatDuration(query.executionTimeMs)}
            </span>
          </div>
          <ChevronRight
            className={cn(
              'h-3.5 w-3.5 text-muted-foreground transition-transform',
              open && 'rotate-90',
            )}
          />
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t border-border px-3 py-2">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10px] text-muted-foreground">SQL</span>
              <CopyButton text={query.sql} />
            </div>
            <pre className="overflow-x-auto rounded bg-muted p-2 font-mono text-[10px] leading-relaxed">
              {query.sql}
            </pre>
            {query.rowCount > 0 && (
              <p className="mt-2 text-[10px] text-muted-foreground">
                &rarr; {query.rowCount} rows returned
              </p>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}
