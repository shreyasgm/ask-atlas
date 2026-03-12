import { useCallback } from 'react';
import ChatWorkspace from '@/components/workspace/chat-workspace';
import { useChatStream } from '@/hooks/use-chat-stream';
import { useConversations } from '@/hooks/use-conversations';
import { useTradeToggles } from '@/hooks/use-trade-toggles';

export default function ChatPage() {
  const {
    addOptimisticConversation,
    conversations,
    deleteConversation,
    hasMore,
    isLoading: conversationsLoading,
    loadMore,
    refresh,
    updateOptimisticTitle,
  } = useConversations();

  const { overrides, resetAll, setMode, setOverrides, setSchema, setSystemMode } =
    useTradeToggles();

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
    stopStreaming,
    threadId,
  } = useChatStream({
    onConversationChange: refresh,
    onOptimisticConversation: addOptimisticConversation,
    onOverridesLoaded: setOverrides,
    onUpdateTitle: updateOptimisticTitle,
  });

  const handleSend = useCallback(
    (question: string) => {
      sendRaw(question, overrides);
    },
    [overrides, sendRaw],
  );

  const handleClear = useCallback(() => {
    const newThreadId = crypto.randomUUID();
    addOptimisticConversation(newThreadId);
    clearChatStream(newThreadId);
    resetAll();
  }, [addOptimisticConversation, clearChatStream, resetAll]);

  return (
    <main className="h-screen bg-background text-foreground" id="main-content">
      <ChatWorkspace
        activeThreadId={threadId}
        conversations={conversations}
        conversationsHasMore={hasMore}
        conversationsLoading={conversationsLoading}
        entitiesData={entitiesData}
        error={error}
        isRestoredThread={isRestoredThread}
        isStreaming={isStreaming}
        messages={messages}
        onClear={handleClear}
        onDeleteConversation={deleteConversation}
        onLoadMoreConversations={loadMore}
        onModeChange={setMode}
        onSchemaChange={setSchema}
        onSend={handleSend}
        onStop={stopStreaming}
        onSystemModeChange={setSystemMode}
        overrides={overrides}
        pipelineSteps={pipelineSteps}
        queryStats={queryStats}
      />
    </main>
  );
}
