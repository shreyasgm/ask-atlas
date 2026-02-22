import { useCallback, useEffect, useState } from 'react';
import type {
  ChatMessage,
  ClassificationSchema,
  ConversationSummary,
  EntitiesData,
  PipelineStep,
  QueryAggregateStats,
  TradeDirection,
  TradeMode,
  TradeOverrides,
} from '@/types/chat';
import CenterPanel from './center-panel';
import LeftSidebar from './left-sidebar';
import RightPanel from './right-panel';

interface ChatWorkspaceProps {
  activeThreadId: null | string;
  conversations: Array<ConversationSummary>;
  conversationsLoading: boolean;
  entitiesData: EntitiesData | null;
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onClear: () => void;
  onDeleteConversation: (threadId: string) => void;
  onDirectionChange: (v: TradeDirection | null) => void;
  onModeChange: (v: TradeMode | null) => void;
  onSchemaChange: (v: ClassificationSchema | null) => void;
  onSend: (text: string) => void;
  overrides: TradeOverrides;
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
  activeThreadId,
  conversations,
  conversationsLoading,
  entitiesData,
  error,
  isStreaming,
  messages,
  onClear,
  onDeleteConversation,
  onDirectionChange,
  onModeChange,
  onSchemaChange,
  onSend,
  overrides,
  pipelineSteps,
  queryStats,
}: ChatWorkspaceProps) {
  const isDesktop = useIsDesktop();
  const [sidebarExpanded, setSidebarExpanded] = useState(isDesktop);
  const [rightPanelExpanded, setRightPanelExpanded] = useState(isDesktop);
  const lastAssistant = messages.findLast((m) => m.role === 'assistant');
  const currentQueries = lastAssistant?.queryResults ?? [];

  const handleSelectConversation = useCallback(
    (threadId: string) => {
      // Navigate happens via the Link in the sidebar; this callback
      // lets us collapse the sidebar on mobile after selection.
      if (!isDesktop) {
        setSidebarExpanded(false);
      }
    },
    [isDesktop],
  );

  const handleNewChat = useCallback(() => {
    onClear();
  }, [onClear]);

  return (
    <div className="relative flex h-full overflow-hidden">
      {/* Left sidebar — overlays on mobile */}
      <div className={!isDesktop && sidebarExpanded ? 'absolute inset-y-0 left-0 z-20' : undefined}>
        <LeftSidebar
          activeThreadId={activeThreadId}
          conversations={conversations}
          expanded={sidebarExpanded}
          isLoading={conversationsLoading}
          onDeleteConversation={onDeleteConversation}
          onNewChat={handleNewChat}
          onSelectConversation={handleSelectConversation}
          onToggle={() => setSidebarExpanded((prev) => !prev)}
        />
      </div>

      <CenterPanel
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onClear={onClear}
        onDirectionChange={onDirectionChange}
        onModeChange={onModeChange}
        onSchemaChange={onSchemaChange}
        onSend={onSend}
        overrides={overrides}
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
