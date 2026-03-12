import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConversationSummary } from '@/types/chat';
import { API_BASE_URL } from '@/config';
import { getSessionId } from '@/utils/session';

const SENTENCE_END = /[.!?]/;
const MAX_TITLE_LENGTH = 50;

/** Derive a short title from the user's question (mirrors backend derive_title). */
export function deriveTitle(message: string, maxLength = MAX_TITLE_LENGTH): string {
  if (!message || !message.trim()) {
    return message;
  }
  const match = SENTENCE_END.exec(message);
  const title = match ? message.slice(0, match.index + 1) : message;
  if (title.length <= maxLength) {
    return title;
  }
  const truncated = title.slice(0, maxLength - 3);
  const lastSpace = truncated.lastIndexOf(' ');
  return (lastSpace > 0 ? truncated.slice(0, lastSpace) : truncated).trimEnd() + '...';
}

interface BackendConversation {
  created_at: string;
  thread_id: string;
  title: string | null;
  updated_at: string;
}

interface BackendResponse {
  conversations: Array<BackendConversation>;
  has_more: boolean;
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
  addOptimisticConversation: (threadId: string, questionText?: string) => void;
  conversations: Array<ConversationSummary>;
  deleteConversation: (threadId: string) => Promise<void>;
  hasMore: boolean;
  isLoading: boolean;
  loadMore: () => void;
  refresh: () => void;
}

const DEBOUNCE_MS = 500;

export function useConversations(): UseConversationsReturn {
  const [conversations, setConversations] = useState<Array<ConversationSummary>>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const offsetRef = useRef(0);

  // AbortController to cancel in-flight fetches
  const abortRef = useRef<AbortController | null>(null);
  // Debounce timer for refresh()
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchConversations = useCallback(async (offset = 0, append = false) => {
    // Abort previous in-flight request
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    if (!append) {
      setIsLoading(true);
    }
    try {
      const response = await fetch(`${API_BASE_URL}/api/threads?limit=50&offset=${offset}`, {
        headers: { 'X-Session-Id': getSessionId() },
        signal: controller.signal,
      });
      if (!response.ok) {
        if (!append) {
          setConversations([]);
          setHasMore(false);
        }
        return;
      }
      const data: BackendResponse = await response.json();
      const mapped = data.conversations.map(toSummary);
      if (append) {
        setConversations((prev) => [...prev, ...mapped]);
      } else {
        setConversations(mapped);
      }
      setHasMore(data.has_more);
      offsetRef.current = offset + mapped.length;
    } catch (error: unknown) {
      // Ignore AbortError — it means we cancelled intentionally
      const isAbort =
        typeof error === 'object' &&
        error !== null &&
        'name' in error &&
        (error as { name: string }).name === 'AbortError';
      if (!isAbort && !append) {
        setConversations([]);
        setHasMore(false);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsLoading(false);
      }
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
        const response = await fetch(`${API_BASE_URL}/api/threads/${threadId}`, {
          headers: { 'X-Session-Id': getSessionId() },
          method: 'DELETE',
        });
        if (!response.ok) {
          await fetchConversations();
        }
      } catch {
        await fetchConversations();
      }
    },
    [fetchConversations],
  );

  // Debounced refresh — collapses rapid calls into one fetch
  const refresh = useCallback(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      fetchConversations();
    }, DEBOUNCE_MS);
  }, [fetchConversations]);

  // Load next page of conversations
  const loadMore = useCallback(() => {
    if (hasMore) {
      fetchConversations(offsetRef.current, true);
    }
  }, [fetchConversations, hasMore]);

  // Optimistic insert — add a conversation at the top without fetching.
  // When questionText is provided, derives a title client-side so the
  // sidebar shows a meaningful label immediately.
  const addOptimisticConversation = useCallback((threadId: string, questionText?: string) => {
    setConversations((prev) => {
      if (prev.some((c) => c.threadId === threadId)) {
        return prev;
      }
      const now = new Date().toISOString();
      const title = questionText ? deriveTitle(questionText) : null;
      return [{ createdAt: now, threadId, title, updatedAt: now }, ...prev];
    });
  }, []);

  return {
    addOptimisticConversation,
    conversations,
    deleteConversation,
    hasMore,
    isLoading,
    loadMore,
    refresh,
  };
}
