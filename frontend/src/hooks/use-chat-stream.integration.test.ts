import { act, renderHook, waitFor } from '@testing-library/react';
/**
 * Integration tests for useChatStream with ASYNC SSE streams.
 *
 * Unlike the unit tests (use-chat-stream.test.ts) which deliver all events
 * synchronously via pull(), these tests use controllable / delayed streams
 * that cross real microtask boundaries — exposing React 19 auto-batching
 * and React Compiler memoisation issues.
 */
import { createElement, type ReactNode, StrictMode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createControllableStream,
  makeAgentTalkEvent,
  makeAsyncSSEStream,
  makeDoneEvent,
  makeNodeStartEvent,
  makePipelineStateEvent,
  makeThreadIdEvent,
  THREAD_ID,
} from '@/test/sse-helpers';
import { useChatStream } from './use-chat-stream';

// Mock session to avoid localStorage issues
vi.mock('@/utils/session', () => ({
  getSessionId: () => 'test-session-id',
}));

// -- react-router mock (only navigate + searchParams, no real routing) --
const mockNavigate = vi.fn();
let mockSearchParams = new URLSearchParams();

vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({}),
  useSearchParams: () => [mockSearchParams],
}));

beforeEach(() => {
  mockNavigate.mockReset();
  mockSearchParams = new URLSearchParams();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useChatStream integration (async SSE)', () => {
  it('streaming text renders incrementally', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    // Push thread_id + first talk chunk
    pushEvent(makeThreadIdEvent());
    pushEvent(makeAgentTalkEvent('Hello '));

    // Intermediate content should appear BEFORE the second chunk
    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('Hello ');
    });

    // Push second talk chunk
    pushEvent(makeAgentTalkEvent('world'));

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('Hello world');
    });

    // End stream
    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('thread_id event navigates mid-stream', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    pushEvent(makeThreadIdEvent());

    await waitFor(() => {
      expect(result.current.threadId).toBe(THREAD_ID);
    });

    // Navigate should fire before the stream ends
    expect(mockNavigate).toHaveBeenCalledWith(`/chat/${THREAD_ID}`, { replace: true });
    // Still streaming
    expect(result.current.isStreaming).toBe(true);

    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('pipeline steps appear while streaming', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    pushEvent(makeThreadIdEvent());
    pushEvent(makeNodeStartEvent('generate_sql', 'Generating SQL query'));

    await waitFor(() => {
      expect(result.current.pipelineSteps).toHaveLength(1);
      expect(result.current.pipelineSteps[0].node).toBe('generate_sql');
      expect(result.current.pipelineSteps[0].status).toBe('active');
    });

    // isStreaming must still be true
    expect(result.current.isStreaming).toBe(true);

    pushEvent(makePipelineStateEvent('generate_sql'));

    await waitFor(() => {
      expect(result.current.pipelineSteps[0].status).toBe('completed');
    });

    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('done event sets isStreaming false and marks message complete', async () => {
    const events = [makeThreadIdEvent(), makeAgentTalkEvent('response'), makeDoneEvent()];
    global.fetch = vi.fn().mockResolvedValue({
      body: makeAsyncSSEStream(events),
      ok: true,
    });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.isStreaming).toBe(false);
      expect(assistant?.content).toBe('response');
    });
  });

  it('non-200 response sets error', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.error).toBe('Server error: 500 Internal Server Error');
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('timeout after 30s shows error', async () => {
    vi.useFakeTimers();

    // Fetch that never resolves but rejects on abort (like a real hanging server)
    global.fetch = vi.fn().mockImplementation(
      (_url: string, options?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          options?.signal?.addEventListener('abort', () => {
            reject(new DOMException('The operation was aborted.', 'AbortError'));
          });
        }),
    );

    const { result } = renderHook(() => useChatStream());

    await act(async () => {
      result.current.sendMessage('hello');
    });

    // Fire the 30s timeout (advanceTimersByTimeAsync processes microtasks)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(result.current.error).toBe('Request timed out. The server may be unavailable.');
    expect(result.current.isStreaming).toBe(false);
  });

  it('clearChat during streaming resets all state', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    pushEvent(makeThreadIdEvent());
    pushEvent(makeAgentTalkEvent('partial'));

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('partial');
    });

    // Abort mid-stream
    act(() => {
      result.current.clearChat();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.pipelineSteps).toEqual([]);
    expect(result.current.threadId).toBeNull();

    close();
  });

  it('handles SSE event split across chunk boundaries', async () => {
    const { close, pushRaw, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    // Send thread_id normally
    pushRaw(`event: thread_id\ndata: ${JSON.stringify({ thread_id: THREAD_ID })}\n\n`);

    await waitFor(() => {
      expect(result.current.threadId).toBe(THREAD_ID);
    });

    // Split an agent_talk event across two raw chunks
    pushRaw('event: agent');
    pushRaw(
      `_talk\ndata: ${JSON.stringify({ content: 'split-test', message_type: 'agent_talk', source: 'agent' })}\n\n`,
    );

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('split-test');
    });

    pushRaw(
      `event: done\ndata: ${JSON.stringify({ thread_id: THREAD_ID, total_execution_time_ms: 0, total_queries: 0, total_rows: 0, total_time_ms: 0 })}\n\n`,
    );
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('parses CRLF line endings from sse_starlette', async () => {
    const { close, pushRaw, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    // sse_starlette sends \r\n line endings and \r\n\r\n event boundaries
    pushRaw(`event: thread_id\r\ndata: ${JSON.stringify({ thread_id: THREAD_ID })}\r\n\r\n`);

    await waitFor(() => {
      expect(result.current.threadId).toBe(THREAD_ID);
    });

    pushRaw(
      `event: agent_talk\r\ndata: ${JSON.stringify({ content: 'crlf-works', message_type: 'agent_talk', source: 'agent' })}\r\n\r\n`,
    );

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('crlf-works');
    });

    pushRaw(
      `event: done\r\ndata: ${JSON.stringify({ thread_id: THREAD_ID, total_execution_time_ms: 0, total_queries: 0, total_rows: 0, total_time_ms: 0 })}\r\n\r\n`,
    );
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('pipeline_state generate_sql populates queryResults with SQL', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    pushEvent(makeThreadIdEvent());
    pushEvent(makeNodeStartEvent('generate_sql', 'Generating SQL query'));
    pushEvent(
      makePipelineStateEvent('generate_sql', {
        sql: 'SELECT * FROM hs92_trade LIMIT 10',
      }),
    );

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.queryResults).toHaveLength(1);
      expect(assistant?.queryResults[0].sql).toBe('SELECT * FROM hs92_trade LIMIT 10');
      expect(assistant?.queryResults[0].rowCount).toBe(0);
    });

    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('pipeline_state execute_sql updates last queryResult with data', async () => {
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    pushEvent(makeThreadIdEvent());
    pushEvent(
      makePipelineStateEvent('generate_sql', {
        sql: 'SELECT product FROM trade',
      }),
    );
    pushEvent(
      makePipelineStateEvent('execute_sql', {
        columns: ['product', 'value'],
        execution_time_ms: 42,
        row_count: 2,
        rows: [
          ['soybeans', 100],
          ['iron ore', 80],
        ],
      }),
    );

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.queryResults).toHaveLength(1);
      expect(assistant?.queryResults[0].columns).toEqual(['product', 'value']);
      expect(assistant?.queryResults[0].rowCount).toBe(2);
      expect(assistant?.queryResults[0].executionTimeMs).toBe(42);
      expect(assistant?.queryResults[0].rows).toEqual([
        ['soybeans', 100],
        ['iron ore', 80],
      ]);
    });

    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('accumulates content from multiple agent_talk events in a single chunk', async () => {
    const { close, pushRaw, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    // Deliver thread_id first
    pushRaw(`event: thread_id\ndata: ${JSON.stringify({ thread_id: THREAD_ID })}\n\n`);

    await waitFor(() => {
      expect(result.current.threadId).toBe(THREAD_ID);
    });

    // Deliver TWO agent_talk events in one raw chunk (simulates batched arrival)
    pushRaw(
      `event: agent_talk\ndata: ${JSON.stringify({ content: 'Hello ', message_type: 'agent_talk', source: 'agent' })}\n\n` +
        `event: agent_talk\ndata: ${JSON.stringify({ content: 'world', message_type: 'agent_talk', source: 'agent' })}\n\n`,
    );

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('Hello world');
    });

    pushRaw(
      `event: done\ndata: ${JSON.stringify({ thread_id: THREAD_ID, total_execution_time_ms: 0, total_queries: 0, total_rows: 0, total_time_ms: 0 })}\n\n`,
    );
    close();

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('auto-submit from ?q= works with StrictMode double-mount', async () => {
    const events = [makeThreadIdEvent(), makeAgentTalkEvent('auto-response'), makeDoneEvent()];
    global.fetch = vi.fn().mockResolvedValue({
      body: makeAsyncSSEStream(events),
      ok: true,
    });

    mockSearchParams = new URLSearchParams('q=Hello');

    const { result } = renderHook(() => useChatStream(), {
      wrapper: ({ children }: { children: ReactNode }) => createElement(StrictMode, null, children),
    });

    await waitFor(() => {
      expect(result.current.messages.length).toBeGreaterThanOrEqual(2);
      const user = result.current.messages.find((m) => m.role === 'user');
      expect(user?.content).toBe('Hello');
    });

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('auto-response');
      expect(result.current.isStreaming).toBe(false);
    });
  });
});
