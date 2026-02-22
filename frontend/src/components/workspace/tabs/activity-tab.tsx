import { Brain, CheckCircle, Database } from 'lucide-react';
import type { PipelineStep, QueryAggregateStats } from '@/types/chat';
import { cn } from '@/lib/utils';

interface ActivityTabProps {
  isRestoredThread: boolean;
  isStreaming: boolean;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
}

const NODE_ICONS: Record<string, typeof Brain> = {
  execute_sql: Database,
  extract_products: Brain,
  generate_sql: Database,
  lookup_codes: Brain,
};

function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function ActivityTab({
  isRestoredThread,
  isStreaming,
  pipelineSteps,
  queryStats,
}: ActivityTabProps) {
  if (pipelineSteps.length === 0 && !queryStats) {
    return (
      <p className="py-8 text-center text-xs text-muted-foreground">
        {isRestoredThread
          ? 'Activity data is only available for the current session. Send a new message to see pipeline activity.'
          : 'No activity yet. Send a message to begin.'}
      </p>
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Activity
        </p>
        {isStreaming ? (
          <span className="flex items-center gap-1 text-[10px] text-primary">
            <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
            In progress
          </span>
        ) : queryStats ? (
          <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-medium text-green-600 dark:text-green-400">
            Complete
          </span>
        ) : null}
      </div>

      <div className="relative">
        {pipelineSteps.map((step, index) => {
          const Icon = NODE_ICONS[step.node] ?? Brain;
          const duration =
            step.completedAt && step.startedAt
              ? formatDuration(step.completedAt - step.startedAt)
              : undefined;
          const isLast = index === pipelineSteps.length - 1 && !queryStats;

          return (
            <div className="flex gap-3" key={`${step.node}-${String(index)}`}>
              {/* Timeline column */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    'flex h-6 w-6 shrink-0 items-center justify-center rounded-full',
                    step.status === 'completed'
                      ? 'bg-primary/10 text-primary'
                      : 'bg-secondary text-muted-foreground',
                  )}
                >
                  <Icon className="h-3 w-3" />
                </div>
                {!isLast && <div className="w-px flex-1 bg-border" />}
              </div>

              {/* Content column */}
              <div className="min-w-0 pb-4">
                <p className="text-xs font-medium">{step.label}</p>
                {duration && <p className="text-[10px] text-muted-foreground">{duration}</p>}
              </div>
            </div>
          );
        })}

        {/* Response delivered entry */}
        {queryStats && (
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10 text-green-600 dark:text-green-400">
                <CheckCircle className="h-3 w-3" />
              </div>
            </div>
            <div className="min-w-0 pb-4">
              <p className="text-xs font-medium">Response delivered</p>
              <p className="text-[10px] text-muted-foreground">
                {queryStats.totalQueries} {queryStats.totalQueries === 1 ? 'query' : 'queries'}
                {' \u00b7 '}
                {queryStats.totalRows} rows
                {' \u00b7 '}
                {formatDuration(queryStats.totalTimeMs)} total
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
