import type {
  ChatMessage,
  ClassificationSchema,
  EntitiesData,
  PipelineStep,
  QueryAggregateStats,
  TradeMode,
  TradeOverrides,
} from '@/types/chat';
import ChatInput from '@/components/chat/chat-input';
import ChatTopBar from '@/components/chat/chat-top-bar';
import MessageList from '@/components/chat/message-list';
import TradeTogglesBar from '@/components/chat/trade-toggles-bar';

interface CenterPanelProps {
  entitiesData: EntitiesData | null;
  error: null | string;
  isRestoredThread: boolean;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onClear: () => void;
  onModeChange: (v: TradeMode | null) => void;
  onSchemaChange: (v: ClassificationSchema | null) => void;
  onSend: (text: string) => void;
  onToggleSidebar: () => void;
  overrides: TradeOverrides;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
}

export default function CenterPanel({
  entitiesData,
  error,
  isRestoredThread,
  isStreaming,
  messages,
  onClear,
  onModeChange,
  onSchemaChange,
  onSend,
  onToggleSidebar,
  overrides,
  pipelineSteps,
  queryStats,
}: CenterPanelProps) {
  const firstUserMessage = messages.find((m) => m.role === 'user');
  const chatTitle = firstUserMessage ? firstUserMessage.content.slice(0, 60) : '';

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <ChatTopBar onClear={onClear} onToggleSidebar={onToggleSidebar} title={chatTitle} />
      <TradeTogglesBar
        onModeChange={onModeChange}
        onSchemaChange={onSchemaChange}
        overrides={overrides}
      />
      <MessageList
        entitiesData={entitiesData}
        error={error}
        isRestoredThread={isRestoredThread}
        isStreaming={isStreaming}
        messages={messages}
        pipelineSteps={pipelineSteps}
        queryStats={queryStats}
      />
      <div className="border-t border-border">
        <div className="mx-auto w-full max-w-2xl px-4 py-4">
          <ChatInput disabled={isStreaming} onSend={onSend} />
        </div>
      </div>
    </div>
  );
}
