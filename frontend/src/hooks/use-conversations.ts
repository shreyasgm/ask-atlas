import { useCallback, useEffect, useState } from 'react';
import type { ConversationSummary } from '@/types/chat';
import { getSessionId } from '@/utils/session';

interface BackendConversation {
  created_at: string;
  thread_id: string;
  title: string | null;
  updated_at: string;
}

function toSummary(c: BackendConversation): ConversationSummary {
  return {
    createdAt: c.created_at,
    threadId: c.thread_id,
    title: c.title,
    updatedAt: c.updated_at,
  };
}

export interface UseConversationsReturn {
  conversations: Array<ConversationSummary>;
  deleteConversation: (threadId: string) => Promise<void>;
  isLoading: boolean;
  refresh: () => void;
}

export function useConversations(): UseConversationsReturn {
  const [conversations, setConversations] = useState<Array<ConversationSummary>>([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchConversations = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await fetch('/api/threads', {
        headers: { 'X-Session-Id': getSessionId() },
      });
      if (!response.ok) {
        setConversations([]);
        return;
      }
      const data: Array<BackendConversation> = await response.json();
      setConversations(data.map(toSummary));
    } catch {
      setConversations([]);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const deleteConversation = useCallback(
    async (threadId: string) => {
      // Optimistic removal
      setConversations((prev) => prev.filter((c) => c.threadId !== threadId));

      try {
        const response = await fetch(`/api/threads/${threadId}`, {
          headers: { 'X-Session-Id': getSessionId() },
          method: 'DELETE',
        });
        if (!response.ok) {
          // Re-fetch to restore state on failure
          await fetchConversations();
        }
      } catch {
        await fetchConversations();
      }
    },
    [fetchConversations],
  );

  const refresh = useCallback(() => {
    fetchConversations();
  }, [fetchConversations]);

  return { conversations, deleteConversation, isLoading, refresh };
}
