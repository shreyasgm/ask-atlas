import { Check, ChevronDown, ChevronRight, Loader } from 'lucide-react';
import { useState } from 'react';
import type { PipelineStep, PipelineType } from '@/types/chat';
import { cn } from '@/lib/utils';
import { getStepDetail } from '@/utils/step-detail';

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
    dot: 'bg-amber-500',
    iconColor: 'text-amber-500',
    label: 'Docs',
    text: 'text-amber-700 dark:text-amber-400',
  },
  graphql: {
    dot: 'bg-violet-500',
    iconColor: 'text-violet-500',
    label: 'Atlas API',
    text: 'text-violet-700 dark:text-violet-400',
  },
  sql: {
    dot: 'bg-blue-500',
    iconColor: 'text-blue-500',
    label: 'SQL',
    text: 'text-blue-700 dark:text-blue-400',
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

export default function PipelineStepper({ steps }: PipelineStepperProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  if (steps.length === 0) {
    return null;
  }

  const groups = groupSteps(steps);

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
    <div className="rounded-[10px] border border-slate-200 bg-slate-50 px-4 py-3 dark:border-border dark:bg-card">
      {groups.map((group, gi) => {
        const colors = PIPELINE_COLORS[group.type];
        const allCompleted = group.steps.every((s) => s.status === 'completed');
        const completedCount = group.steps.filter((s) => s.status === 'completed').length;
        const isExpanded = group.hasActive || expanded.has(gi);

        return (
          <div key={`group-${gi}`}>
            {gi > 0 && <div className="my-0.5 h-px bg-slate-200 dark:bg-border" />}
            {!isExpanded && allCompleted ? (
              /* Collapsed summary row */
              <button
                className="flex w-full items-center gap-2.5 py-1.5"
                onClick={() => toggleGroup(gi)}
                type="button"
              >
                <div className={cn('h-2 w-2 shrink-0 rounded-full', colors.dot)} />
                <span className={cn('text-xs font-medium', colors.text)}>
                  {group.label}: {completedCount} steps completed
                </span>
                <Check className="h-3 w-3 text-green-500" />
                <ChevronRight className="h-3 w-3 text-slate-400" />
              </button>
            ) : (
              /* Expanded step rows */
              <div className="flex min-w-0 flex-col">
                <button
                  className="flex w-full items-center gap-2 py-1.5"
                  onClick={() => toggleGroup(gi)}
                  type="button"
                >
                  <span className={cn('text-xs font-semibold', colors.text)}>{group.label}</span>
                  {allCompleted && <ChevronDown className="h-3 w-3 text-slate-400" />}
                </button>
                {group.steps.map((step, si) => {
                  const detail =
                    step.status === 'completed' ? getStepDetail(step.node, step.detail) : null;
                  return (
                    <div key={`${step.node}-${si}`}>
                      <div className="flex w-full items-center gap-2.5 py-1.5">
                        <div className={cn('h-2 w-2 shrink-0 rounded-full', colors.dot)} />
                        <span
                          className={cn(
                            'min-w-0 truncate text-xs',
                            step.status === 'completed'
                              ? 'text-slate-500 dark:text-slate-400'
                              : cn('font-semibold', colors.text),
                          )}
                        >
                          {step.label}
                          {step.status === 'active' && '...'}
                        </span>
                        {step.status === 'completed' && (
                          <Check className="h-3 w-3 text-green-500" />
                        )}
                        {step.status === 'active' && (
                          <Loader className={cn('h-3 w-3 animate-spin', colors.iconColor)} />
                        )}
                      </div>
                      {detail && (
                        <p className="w-full min-w-0 truncate pl-[18px] text-[11px] leading-tight text-slate-400 dark:text-slate-500">
                          {detail}
                        </p>
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
}
