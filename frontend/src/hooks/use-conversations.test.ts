import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary } from '@/types/chat';
import { deriveTitle, useConversations } from './use-conversations';

vi.mock('@/config', () => ({ API_BASE_URL: '' }));

vi.mock('@/utils/session', () => ({
  getSessionId: () => 'test-session-id',
}));

const CONVERSATIONS_BACKEND = [
  {
    created_at: '2025-02-20T10:00:00Z',
    thread_id: 'thread-1',
    title: 'Top exports of Brazil',
    updated_at: '2025-02-20T11:00:00Z',
  },
  {
    created_at: '2025-02-19T09:00:00Z',
    thread_id: 'thread-2',
    title: null,
    updated_at: '2025-02-19T09:30:00Z',
  },
];

function paginatedResponse(conversations = CONVERSATIONS_BACKEND, hasMore = false) {
  return { conversations, has_more: hasMore };
}

function mockFetchOk(data: unknown) {
  return { json: () => Promise.resolve(data), ok: true };
}

const EXPECTED_CONVERSATIONS: Array<ConversationSummary> = [
  {
    createdAt: '2025-02-20T10:00:00Z',
    threadId: 'thread-1',
    title: 'Top exports of Brazil',
    updatedAt: '2025-02-20T11:00:00Z',
  },
  {
    createdAt: '2025-02-19T09:00:00Z',
    threadId: 'thread-2',
    title: null,
    updatedAt: '2025-02-19T09:30:00Z',
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useConversations', () => {
  it('starts with empty conversations and loading true', () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchOk(paginatedResponse([])));

    const { result } = renderHook(() => useConversations());
    expect(result.current.conversations).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('fetches conversations on mount with X-Session-Id header', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchOk(paginatedResponse()));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/threads?limit=50&offset=0',
      expect.objectContaining({
        headers: { 'X-Session-Id': 'test-session-id' },
      }),
    );
    expect(result.current.conversations).toEqual(EXPECTED_CONVERSATIONS);
    expect(result.current.hasMore).toBe(false);
  });

  it('handles fetch failure gracefully with empty list', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500 });

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.conversations).toEqual([]);
  });

  it('handles network error gracefully', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.conversations).toEqual([]);
  });

  it('deleteConversation calls DELETE and removes from local state', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(mockFetchOk(paginatedResponse()))
      .mockResolvedValueOnce({ ok: true, status: 204 });

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    await act(async () => {
      await result.current.deleteConversation('thread-1');
    });

    expect(globalThis.fetch).toHaveBeenCalledWith('/api/threads/thread-1', {
      headers: { 'X-Session-Id': 'test-session-id' },
      method: 'DELETE',
    });
    expect(result.current.conversations).toHaveLength(1);
    expect(result.current.conversations[0].threadId).toBe('thread-2');
  });

  it('refresh re-fetches the conversation list after debounce', async () => {
    const updatedResponse = paginatedResponse([CONVERSATIONS_BACKEND[0]]);

    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(mockFetchOk(paginatedResponse()))
      .mockResolvedValueOnce(mockFetchOk(updatedResponse));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    // Trigger debounced refresh — it will fire after ~500ms real time
    act(() => {
      result.current.refresh();
    });

    // Wait for the debounced fetch to complete
    await waitFor(
      () => {
        expect(result.current.conversations).toHaveLength(1);
      },
      { timeout: 2000 },
    );
  });

  it('re-fetches on delete failure to restore state', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce(mockFetchOk(paginatedResponse()))
      .mockResolvedValueOnce({ ok: false, status: 500 })
      // Re-fetch after failure
      .mockResolvedValueOnce(mockFetchOk(paginatedResponse()));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    await act(async () => {
      await result.current.deleteConversation('thread-1');
    });

    // Should re-fetch to restore original state
    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });
  });

  it('addOptimisticConversation inserts with derived title', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchOk(paginatedResponse()));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    act(() => {
      result.current.addOptimisticConversation(
        'thread-new',
        'Top exports of Brazil. What about Argentina?',
      );
    });

    expect(result.current.conversations).toHaveLength(3);
    expect(result.current.conversations[0].threadId).toBe('thread-new');
    expect(result.current.conversations[0].title).toBe('Top exports of Brazil.');
  });

  it('addOptimisticConversation with no question text uses null title', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchOk(paginatedResponse()));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    act(() => {
      result.current.addOptimisticConversation('thread-new');
    });

    expect(result.current.conversations).toHaveLength(3);
    expect(result.current.conversations[0].title).toBeNull();
  });

  it('addOptimisticConversation does not add duplicates', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchOk(paginatedResponse()));

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    act(() => {
      result.current.addOptimisticConversation('thread-1');
    });

    expect(result.current.conversations).toHaveLength(2);
  });
});

describe('deriveTitle', () => {
  it('returns short message as-is', () => {
    expect(deriveTitle('Hello world')).toBe('Hello world');
  });

  it('extracts first sentence ending with period', () => {
    expect(deriveTitle('Top exports of Brazil. What about Argentina?')).toBe(
      'Top exports of Brazil.',
    );
  });

  it('extracts first sentence ending with question mark', () => {
    expect(deriveTitle('What are exports? Tell me more.')).toBe('What are exports?');
  });

  it('extracts first sentence ending with exclamation', () => {
    expect(deriveTitle('Show me data! Now please.')).toBe('Show me data!');
  });

  it('truncates long messages on word boundary', () => {
    const long = 'What are the top twenty exported products from Brazil in 2020';
    const result = deriveTitle(long, 30);
    expect(result.length).toBeLessThanOrEqual(30);
    expect(result).toBe('What are the top twenty...');
  });

  it('returns empty string for empty input', () => {
    expect(deriveTitle('')).toBe('');
  });

  it('returns whitespace for whitespace-only input', () => {
    expect(deriveTitle('   ').trim()).toBe('');
  });
});
