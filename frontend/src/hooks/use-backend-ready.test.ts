import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

async function importHook() {
  const { useBackendReady } = await import('./use-backend-ready');
  return useBackendReady;
}

describe('useBackendReady', () => {
  let fetchResolve: (res: Response) => void;
  let fetchReject: (err: Error) => void;

  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((resolve, reject) => {
            fetchResolve = resolve;
            fetchReject = reject;
          }),
      ),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns false while the health check is still pending', async () => {
    const useBackendReady = await importHook();
    const { result } = renderHook(() => useBackendReady());
    expect(result.current).toBe(false);
  });

  it('returns true after a successful health check', async () => {
    const useBackendReady = await importHook();
    const { result } = renderHook(() => useBackendReady());

    fetchResolve(new Response(null, { status: 200 }));
    // Allow the microtask (.then) to fire and React to re-render
    await vi.waitFor(() => {
      expect(result.current).toBe(true);
    });
  });

  it('stays false when the health check returns a non-ok status', async () => {
    const useBackendReady = await importHook();
    const { result } = renderHook(() => useBackendReady());

    fetchResolve(new Response(null, { status: 503 }));
    // Give the .then handler time to run
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current).toBe(false);
  });

  it('stays false on a network error', async () => {
    const useBackendReady = await importHook();
    const { result } = renderHook(() => useBackendReady());

    fetchReject(new TypeError('Failed to fetch'));
    // Give the .catch handler time to run
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current).toBe(false);
  });
});
