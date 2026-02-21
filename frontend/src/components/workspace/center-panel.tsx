import type { ChatMessage, PipelineStep } from '@/types/chat';
import ChatInput from '@/components/chat/chat-input';
import ChatTopBar from '@/components/chat/chat-top-bar';
import MessageList from '@/components/chat/message-list';

interface CenterPanelProps {
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onClear: () => void;
  onSend: (text: string) => void;
  pipelineSteps: Array<PipelineStep>;
}

export default function CenterPanel({
  error,
  isStreaming,
  messages,
  onClear,
  onSend,
  pipelineSteps,
}: CenterPanelProps) {
  const firstUserMessage = messages.find((m) => m.role === 'user');
  const chatTitle = firstUserMessage ? firstUserMessage.content.slice(0, 60) : '';

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {messages.length > 0 && <ChatTopBar onClear={onClear} title={chatTitle} />}
      <MessageList
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onSend={onSend}
        pipelineSteps={pipelineSteps}
      />
      <div className="mx-auto w-full max-w-2xl px-4 pb-4">
        <ChatInput disabled={isStreaming} onSend={onSend} />
      </div>
    </div>
  );
}
