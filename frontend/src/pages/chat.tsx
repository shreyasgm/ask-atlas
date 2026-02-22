import { useCallback } from 'react';
import ChatWorkspace from '@/components/workspace/chat-workspace';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useConversations } from '@/hooks/use-conversations';
import { useTradeToggles } from '@/hooks/use-trade-toggles';

export default function ChatPage() {
  const {
    conversations,
    deleteConversation,
    isLoading: conversationsLoading,
    refresh,
  } = useConversations();

  const { overrides, resetAll, setMode, setOverrides, setSchema } = useTradeToggles();

  const {
    clearChat: clearChatStream,
    entitiesData,
    error,
    isRestoredThread,
    isStreaming,
    messages,
    pipelineSteps,
    queryStats,
    sendMessage: sendRaw,
    threadId,
  } = useChatStream({ onConversationChange: refresh, onOverridesLoaded: setOverrides });

  const handleSend = useCallback(
    (question: string) => {
      sendRaw(question, overrides);
    },
    [overrides, sendRaw],
  );

  const handleClear = useCallback(() => {
    clearChatStream();
    resetAll();
  }, [clearChatStream, resetAll]);

  return (
    <div className="h-screen bg-background text-foreground">
      <ChatWorkspace
        activeThreadId={threadId}
        conversations={conversations}
        conversationsLoading={conversationsLoading}
        entitiesData={entitiesData}
        error={error}
        isRestoredThread={isRestoredThread}
        isStreaming={isStreaming}
        messages={messages}
        onClear={handleClear}
        onDeleteConversation={deleteConversation}
        onModeChange={setMode}
        onSchemaChange={setSchema}
        onSend={handleSend}
        overrides={overrides}
        pipelineSteps={pipelineSteps}
        queryStats={queryStats}
      />
    </div>
  );
}
