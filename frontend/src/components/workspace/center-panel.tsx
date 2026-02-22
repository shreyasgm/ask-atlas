import type {
  ChatMessage,
  ClassificationSchema,
  PipelineStep,
  TradeMode,
  TradeOverrides,
} from '@/types/chat';
import ChatInput from '@/components/chat/chat-input';
import ChatTopBar from '@/components/chat/chat-top-bar';
import MessageList from '@/components/chat/message-list';
import TradeTogglesBar from '@/components/chat/trade-toggles-bar';

interface CenterPanelProps {
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onClear: () => void;
  onModeChange: (v: TradeMode | null) => void;
  onSchemaChange: (v: ClassificationSchema | null) => void;
  onSend: (text: string) => void;
  overrides: TradeOverrides;
  pipelineSteps: Array<PipelineStep>;
}

export default function CenterPanel({
  error,
  isStreaming,
  messages,
  onClear,
  onModeChange,
  onSchemaChange,
  onSend,
  overrides,
  pipelineSteps,
}: CenterPanelProps) {
  const firstUserMessage = messages.find((m) => m.role === 'user');
  const chatTitle = firstUserMessage ? firstUserMessage.content.slice(0, 60) : '';

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {messages.length > 0 && <ChatTopBar onClear={onClear} title={chatTitle} />}
      <TradeTogglesBar
        onModeChange={onModeChange}
        onSchemaChange={onSchemaChange}
        overrides={overrides}
      />
      <MessageList
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onSend={onSend}
        pipelineSteps={pipelineSteps}
      />
      <div className="border-t border-border">
        <div className="mx-auto w-full max-w-2xl px-4 py-4">
          <ChatInput disabled={isStreaming} onSend={onSend} />
        </div>
      </div>
    </div>
  );
}
