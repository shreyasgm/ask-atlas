import { Activity, ChevronLeft, ChevronRight, Layers, Terminal } from 'lucide-react';
import { useState } from 'react';
import type { EntitiesData, PipelineStep, QueryAggregateStats, QueryResult } from '@/types/chat';
import { cn } from '@/lib/utils';
import ActivityTab from './tabs/activity-tab';
import EntitiesTab from './tabs/entities-tab';
import QueriesTab from './tabs/queries-tab';

export type RightPanelTab = 'activity' | 'entities' | 'queries';

interface RightPanelProps {
  currentQueries: Array<QueryResult>;
  entitiesData: EntitiesData | null;
  expanded: boolean;
  isStreaming: boolean;
  onToggle: () => void;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
}

const TABS: Array<{ icon: typeof Activity; id: RightPanelTab; label: string }> = [
  { icon: Layers, id: 'entities', label: 'Entities' },
  { icon: Activity, id: 'activity', label: 'Activity' },
  { icon: Terminal, id: 'queries', label: 'Queries' },
];

export default function RightPanel({
  currentQueries,
  entitiesData,
  expanded,
  isStreaming,
  onToggle,
  pipelineSteps,
  queryStats,
}: RightPanelProps) {
  const [activeTab, setActiveTab] = useState<RightPanelTab>('activity');

  if (!expanded) {
    return (
      <div className="flex h-full w-12 shrink-0 flex-col items-center gap-3 border-l border-border bg-secondary py-3">
        <button
          aria-label="Expand panel"
          className="rounded p-1.5 text-muted-foreground hover:bg-background hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {TABS.map((tab) => (
          <button
            aria-label={tab.label}
            className="rounded p-1.5 text-muted-foreground hover:bg-background hover:text-foreground"
            key={tab.id}
            onClick={() => {
              setActiveTab(tab.id);
              onToggle();
            }}
            type="button"
          >
            <tab.icon className="h-4 w-4" />
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="flex h-full w-[340px] shrink-0 flex-col border-l border-border bg-secondary">
      <div className="flex shrink-0 items-center border-b border-border" role="tablist">
        <button
          aria-label="Collapse panel"
          className="flex items-center px-2 py-2.5 text-muted-foreground hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        {TABS.map((tab) => (
          <button
            aria-selected={activeTab === tab.id}
            className={cn(
              'px-3 py-2.5 text-xs font-medium transition-colors',
              activeTab === tab.id
                ? 'border-b-2 border-primary text-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            role="tab"
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {activeTab === 'activity' && (
          <ActivityTab
            isStreaming={isStreaming}
            pipelineSteps={pipelineSteps}
            queryStats={queryStats}
          />
        )}
        {activeTab === 'entities' && <EntitiesTab entitiesData={entitiesData} />}
        {activeTab === 'queries' && (
          <QueriesTab currentQueries={currentQueries} queryStats={queryStats} />
        )}
      </div>
    </div>
  );
}
