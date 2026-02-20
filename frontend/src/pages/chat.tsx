import ChatHeader from '@/components/chat/chat-header';
import ChatInput from '@/components/chat/chat-input';
import ChatTopBar from '@/components/chat/chat-top-bar';
import MessageList from '@/components/chat/message-list';
import { useChatStream } from '@/hooks/use-chat-stream';

export default function ChatPage() {
  const { clearChat, error, isStreaming, messages, pipelineSteps, sendMessage } = useChatStream();

  const firstUserMessage = messages.find((m) => m.role === 'user');
  const chatTitle = firstUserMessage ? firstUserMessage.content.slice(0, 60) : '';

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <ChatHeader />
      {messages.length > 0 && <ChatTopBar onClear={clearChat} title={chatTitle} />}
      <MessageList
        isStreaming={isStreaming}
        messages={messages}
        onSend={sendMessage}
        pipelineSteps={pipelineSteps}
      />
      {error && (
        <div className="px-4 pb-2">
          <p className="text-center text-sm text-destructive">{error}</p>
        </div>
      )}
      <div className="mx-auto w-full max-w-2xl px-4 pb-4">
        <ChatInput disabled={isStreaming} onSend={sendMessage} />
      </div>
    </div>
  );
}
