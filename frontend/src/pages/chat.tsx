import ChatWorkspace from '@/components/workspace/chat-workspace';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useConversations } from '@/hooks/use-conversations';

export default function ChatPage() {
  const {
    conversations,
    deleteConversation,
    isLoading: conversationsLoading,
    refresh,
  } = useConversations();

  const {
    clearChat,
    entitiesData,
    error,
    isStreaming,
    messages,
    pipelineSteps,
    queryStats,
    sendMessage,
    threadId,
  } = useChatStream({ onConversationChange: refresh });

  return (
    <div className="h-screen bg-background text-foreground">
      <ChatWorkspace
        activeThreadId={threadId}
        conversations={conversations}
        conversationsLoading={conversationsLoading}
        entitiesData={entitiesData}
        error={error}
        isStreaming={isStreaming}
        messages={messages}
        onClear={clearChat}
        onDeleteConversation={deleteConversation}
        onSend={sendMessage}
        pipelineSteps={pipelineSteps}
        queryStats={queryStats}
      />
    </div>
  );
}
