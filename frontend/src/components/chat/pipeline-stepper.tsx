import { Check, ChevronDown, ChevronRight, Loader } from 'lucide-react';
import { memo, useMemo, useState } from 'react';
import type { PipelineStep, PipelineType, ReasoningTraceEntry } from '@/types/chat';
import { cn } from '@/lib/utils';
import { getStepDetail } from '@/utils/step-detail';
import GraphqlReasoningTrace from './graphql-reasoning-trace';
import ReasoningTrace from './reasoning-trace';

interface PipelineStepperProps {
  steps: Array<PipelineStep>;
}

interface PipelineGroup {
  hasActive: boolean;
  label: string;
  queryIndex?: number;
  steps: Array<PipelineStep>;
  type: PipelineType;
}

const PIPELINE_COLORS: Record<
  PipelineType,
  { dot: string; iconColor: string; label: string; text: string }
> = {
  docs: {
    dot: 'bg-warning',
    iconColor: 'text-warning',
    label: 'Docs',
    text: 'text-warning',
  },
  graphql: {
    dot: 'bg-info',
    iconColor: 'text-info',
    label: 'Atlas API',
    text: 'text-info',
  },
  sql: {
    dot: 'bg-primary',
    iconColor: 'text-primary',
    label: 'SQL',
    text: 'text-primary',
  },
};

function groupSteps(steps: Array<PipelineStep>): Array<PipelineGroup> {
  const groups: Array<PipelineGroup> = [];
  for (const step of steps) {
    const last = groups.at(-1);
    if (last && last.type === step.pipelineType && last.queryIndex === step.queryIndex) {
      last.steps.push(step);
      if (step.status === 'active') {
        last.hasActive = true;
      }
    } else {
      groups.push({
        hasActive: step.status === 'active',
        label: PIPELINE_COLORS[step.pipelineType].label,
        queryIndex: step.queryIndex,
        steps: [step],
        type: step.pipelineType,
      });
    }
  }
  return groups;
}

export default memo(function PipelineStepper({ steps }: PipelineStepperProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const groups = useMemo(() => groupSteps(steps), [steps]);

  if (steps.length === 0) {
    return null;
  }

  function toggleGroup(index: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  return (
    <div className="rounded-[10px] border border-border bg-card px-4 py-3">
      {groups.map((group, gi) => {
        const colors = PIPELINE_COLORS[group.type];
        const allCompleted = group.steps.every((s) => s.status === 'completed');
        const completedCount = group.steps.filter((s) => s.status === 'completed').length;
        const isExpanded = group.hasActive || expanded.has(gi);

        return (
          <div key={`group-${gi}`}>
            {gi > 0 && <div className="my-0.5 h-px bg-border" />}
            {!isExpanded && allCompleted ? (
              /* Collapsed summary row */
              <button
                className="flex w-full items-center gap-2.5 rounded py-1.5 transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                onClick={() => toggleGroup(gi)}
                type="button"
              >
                <div className={cn('h-2 w-2 shrink-0 rounded-full', colors.dot)} />
                <span className={cn('text-xs font-medium', colors.text)}>
                  {group.label}: {completedCount} steps completed
                </span>
                <Check className="h-3 w-3 text-success" />
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              </button>
            ) : (
              /* Expanded step rows */
              <div className="flex min-w-0 flex-col">
                <button
                  className="flex w-full items-center gap-2 rounded py-1.5 transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  onClick={() => toggleGroup(gi)}
                  type="button"
                >
                  <span className={cn('text-xs font-semibold', colors.text)}>{group.label}</span>
                  {allCompleted && <ChevronDown className="h-3 w-3 text-muted-foreground" />}
                </button>
                {group.steps.map((step, si) => {
                  const detail =
                    step.status === 'completed' ? getStepDetail(step.node, step.detail) : null;
                  const assessVerdict =
                    step.node === 'assess_graphql_result'
                      ? (step.detail?.verdict as string | undefined)
                      : undefined;
                  return (
                    <div key={`${step.node}-${si}`}>
                      <div className="flex w-full min-w-0 items-center gap-2.5 py-1.5">
                        <div className={cn('h-2 w-2 shrink-0 rounded-full', colors.dot)} />
                        <span
                          className={cn(
                            'min-w-0 truncate text-xs',
                            step.status === 'completed'
                              ? 'text-muted-foreground'
                              : cn('font-semibold', colors.text),
                          )}
                        >
                          {step.label}
                          {step.status === 'active' && '...'}
                        </span>
                        {step.status === 'completed' && <Check className="h-3 w-3 text-success" />}
                        {step.status === 'active' && (
                          <Loader className={cn('h-3 w-3 animate-spin', colors.iconColor)} />
                        )}
                      </div>
                      {detail && (
                        <p
                          className={cn(
                            'line-clamp-5 w-full min-w-0 pl-[18px] text-[11px] leading-tight',
                            assessVerdict === 'fail'
                              ? 'text-destructive'
                              : assessVerdict === 'suspicious'
                                ? 'text-warning'
                                : 'text-muted-foreground',
                          )}
                        >
                          {detail}
                        </p>
                      )}
                      {step.node === 'sql_query_agent' &&
                        Array.isArray(step.detail?.reasoning_trace) &&
                        (step.detail.reasoning_trace as Array<ReasoningTraceEntry>).length > 0 && (
                          <ReasoningTrace
                            entries={step.detail.reasoning_trace as Array<ReasoningTraceEntry>}
                            isActive={step.status === 'active'}
                          />
                        )}
                      {step.node === 'graphql_correction_agent' &&
                        Array.isArray(step.detail?.reasoning_trace) &&
                        (step.detail.reasoning_trace as Array<ReasoningTraceEntry>).length > 0 && (
                          <GraphqlReasoningTrace
                            entries={step.detail.reasoning_trace as Array<ReasoningTraceEntry>}
                            isActive={step.status === 'active'}
                          />
                        )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
});
