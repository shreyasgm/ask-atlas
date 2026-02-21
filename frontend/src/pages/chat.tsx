import ChatWorkspace from '@/components/workspace/chat-workspace';
import { useChatStream } from '@/hooks/use-chat-stream';

export default function ChatPage() {
  const {
    clearChat,
    entitiesData,
    error,
    isStreaming,
    messages,
    pipelineSteps,
    queryStats,
    sendMessage,
  } = useChatStream();

  return (
    <div className="h-screen bg-background text-foreground">
      <ChatWorkspace
        entitiesData={entitiesData}
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onClear={clearChat}
        onSend={sendMessage}
        pipelineSteps={pipelineSteps}
        queryStats={queryStats}
      />
    </div>
  );
}
