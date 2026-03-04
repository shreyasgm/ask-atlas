import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useFeedback } from './use-feedback';

vi.mock('@/config', () => ({ API_BASE_URL: '' }));

vi.mock('@/utils/session', () => ({
  getSessionId: () => 'test-session-id',
}));

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('useFeedback', () => {
  it('starts with an empty map when threadId is null', () => {
    globalThis.fetch = vi.fn();
    const { result } = renderHook(() => useFeedback(null));
    expect(result.current.feedbackMap.size).toBe(0);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('loads existing feedback for a thread', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () =>
        Promise.resolve([
          { comment: null, id: 1, rating: 'up', turn_index: 0 },
          { comment: 'bad', id: 2, rating: 'down', turn_index: 1 },
        ]),
      ok: true,
    });

    const { result } = renderHook(() => useFeedback('thread-1'));

    await waitFor(() => {
      expect(result.current.feedbackMap.size).toBe(2);
    });

    expect(result.current.feedbackMap.get(0)).toEqual({
      comment: undefined,
      id: 1,
      rating: 'up',
    });
    expect(result.current.feedbackMap.get(1)).toEqual({
      comment: 'bad',
      id: 2,
      rating: 'down',
    });

    expect(globalThis.fetch).toHaveBeenCalledWith('/api/feedback?thread_id=thread-1', {
      headers: { 'X-Session-Id': 'test-session-id' },
    });
  });

  it('clears map when threadId changes to null', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: () => Promise.resolve([{ comment: null, id: 1, rating: 'up', turn_index: 0 }]),
      ok: true,
    });

    const { rerender, result } = renderHook(({ tid }) => useFeedback(tid), {
      initialProps: { tid: 'thread-1' as null | string },
    });

    await waitFor(() => {
      expect(result.current.feedbackMap.size).toBe(1);
    });

    rerender({ tid: null });

    expect(result.current.feedbackMap.size).toBe(0);
  });

  it('submitFeedback posts and updates map', async () => {
    // Initial load returns empty
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce({ json: () => Promise.resolve([]), ok: true })
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ comment: null, id: 10, rating: 'up', turn_index: 0 }),
        ok: true,
      });

    const { result } = renderHook(() => useFeedback('thread-1'));

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    });

    act(() => {
      result.current.submitFeedback(0, 'up');
    });

    // Optimistic update
    expect(result.current.feedbackMap.get(0)?.rating).toBe('up');

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });

    // Server response update
    await waitFor(() => {
      expect(result.current.feedbackMap.get(0)?.id).toBe(10);
    });
  });

  it('updateFeedback sends PUT and updates map', async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        json: () => Promise.resolve([{ comment: null, id: 5, rating: 'up', turn_index: 0 }]),
        ok: true,
      })
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ comment: 'changed', id: 5, rating: 'down', turn_index: 0 }),
        ok: true,
      });

    const { result } = renderHook(() => useFeedback('thread-1'));

    await waitFor(() => {
      expect(result.current.feedbackMap.size).toBe(1);
    });

    act(() => {
      result.current.updateFeedback(5, 0, 'down', 'changed');
    });

    // Optimistic
    expect(result.current.feedbackMap.get(0)?.rating).toBe('down');

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(2);
    });

    const putCall = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(putCall[0]).toBe('/api/feedback/5');
    expect(putCall[1].method).toBe('PUT');
  });
});
