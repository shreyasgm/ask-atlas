import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary } from '@/types/chat';
import { useConversations } from './use-conversations';

vi.mock('@/utils/session', () => ({
  getSessionId: () => 'test-session-id',
}));

const CONVERSATIONS_RESPONSE = [
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
});

describe('useConversations', () => {
  it('starts with empty conversations and loading true', () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve([]),
      ok: true,
    });

    const { result } = renderHook(() => useConversations());
    expect(result.current.conversations).toEqual([]);
    expect(result.current.isLoading).toBe(true);
  });

  it('fetches conversations on mount with X-Session-Id header', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve(CONVERSATIONS_RESPONSE),
      ok: true,
    });

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(globalThis.fetch).toHaveBeenCalledWith('/api/threads', {
      headers: { 'X-Session-Id': 'test-session-id' },
    });
    expect(result.current.conversations).toEqual(EXPECTED_CONVERSATIONS);
  });

  it('handles fetch failure gracefully with empty list', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
    });

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
      .mockResolvedValueOnce({
        json: () => Promise.resolve(CONVERSATIONS_RESPONSE),
        ok: true,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 204,
      });

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

  it('refresh re-fetches the conversation list', async () => {
    const updatedResponse = [CONVERSATIONS_RESPONSE[0]];

    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve(CONVERSATIONS_RESPONSE),
        ok: true,
      })
      .mockResolvedValueOnce({
        json: () => Promise.resolve(updatedResponse),
        ok: true,
      });

    const { result } = renderHook(() => useConversations());

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(2);
    });

    act(() => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.conversations).toHaveLength(1);
    });
  });

  it('re-fetches on delete failure to restore state', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve(CONVERSATIONS_RESPONSE),
        ok: true,
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
      })
      // Re-fetch after failure
      .mockResolvedValueOnce({
        json: () => Promise.resolve(CONVERSATIONS_RESPONSE),
        ok: true,
      });

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
});
