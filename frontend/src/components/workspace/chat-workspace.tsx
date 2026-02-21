import { useEffect, useState } from 'react';
import type { ChatMessage, EntitiesData, PipelineStep, QueryAggregateStats } from '@/types/chat';
import CenterPanel from './center-panel';
import LeftSidebar from './left-sidebar';
import RightPanel from './right-panel';

interface ChatWorkspaceProps {
  entitiesData: EntitiesData | null;
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onClear: () => void;
  onSend: (text: string) => void;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
}

function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return true;
    }
    return window.matchMedia('(min-width: 1024px)').matches;
  });

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return;
    }
    const mql = window.matchMedia('(min-width: 1024px)');
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return isDesktop;
}

export default function ChatWorkspace({
  entitiesData,
  error,
  isStreaming,
  messages,
  onClear,
  onSend,
  pipelineSteps,
  queryStats,
}: ChatWorkspaceProps) {
  const isDesktop = useIsDesktop();
  const [sidebarExpanded, setSidebarExpanded] = useState(isDesktop);
  const [rightPanelExpanded, setRightPanelExpanded] = useState(isDesktop);

  const lastAssistant = messages.findLast((m) => m.role === 'assistant');
  const currentQueries = lastAssistant?.queryResults ?? [];

  return (
    <div className="relative flex h-full overflow-hidden">
      {/* Left sidebar — overlays on mobile */}
      <div className={!isDesktop && sidebarExpanded ? 'absolute inset-y-0 left-0 z-20' : undefined}>
        <LeftSidebar
          expanded={sidebarExpanded}
          onNewChat={onClear}
          onToggle={() => setSidebarExpanded((prev) => !prev)}
        />
      </div>

      <CenterPanel
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onClear={onClear}
        onSend={onSend}
        pipelineSteps={pipelineSteps}
      />

      {/* Right panel — overlays on mobile */}
      <div
        className={!isDesktop && rightPanelExpanded ? 'absolute inset-y-0 right-0 z-20' : undefined}
      >
        <RightPanel
          currentQueries={currentQueries}
          entitiesData={entitiesData}
          expanded={rightPanelExpanded}
          isStreaming={isStreaming}
          onToggle={() => setRightPanelExpanded((prev) => !prev)}
          pipelineSteps={pipelineSteps}
          queryStats={queryStats}
        />
      </div>
    </div>
  );
}
