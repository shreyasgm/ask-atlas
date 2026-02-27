import { ChevronRight, Globe, Timer, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import type { GraphqlSummary } from '@/types/chat';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { cn } from '@/lib/utils';
import { getEntityBadgeClass } from '@/utils/entity-colors';
import AtlasLinks from './atlas-links';

interface GraphqlSummaryBlockProps {
  summary: GraphqlSummary;
}

export default function GraphqlSummaryBlock({ summary }: GraphqlSummaryBlockProps) {
  const [open, setOpen] = useState(false);
  const { classification } = summary;

  const validEntities = Object.entries(summary.entities).filter(
    ([, val]) => val != null && val !== '' && String(val).length <= 60,
  );

  return (
    <div className="flex flex-col gap-2">
      <Collapsible onOpenChange={setOpen} open={open}>
        <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
          <Globe className="h-3.5 w-3.5 text-violet-500" />
          <span>Atlas API Query</span>
          <ChevronRight className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-90')} />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-2 flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-violet-100 px-2 py-0.5 font-mono text-[11px] font-semibold text-violet-800 dark:bg-violet-950 dark:text-violet-300">
                {classification.queryType}
              </span>
              {classification.isRejected && (
                <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-800 dark:bg-red-950 dark:text-red-300">
                  Rejected
                </span>
              )}
              {summary.apiTarget && (
                <span className="text-[11px] text-muted-foreground">{summary.apiTarget}</span>
              )}
            </div>
            {classification.isRejected && classification.rejectionReason && (
              <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950">
                <TriangleAlert className="h-4 w-4 shrink-0 text-red-500" />
                <p className="text-xs text-red-700 dark:text-red-300">
                  {classification.rejectionReason}
                </p>
              </div>
            )}
            {validEntities.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">
                  Entities:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {validEntities.map(([key, val]) => (
                    <span
                      className={cn(
                        'rounded border px-2 py-0.5 font-mono text-[11px] font-medium',
                        getEntityBadgeClass(key),
                      )}
                      key={key}
                    >
                      {key}: {String(val)}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {summary.executionTimeMs > 0 && (
              <div className="flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                <Timer className="h-3 w-3" />
                <span>{summary.executionTimeMs}ms</span>
              </div>
            )}
            <AtlasLinks links={summary.links} />
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
