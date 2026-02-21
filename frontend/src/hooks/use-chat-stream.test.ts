import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useChatStream } from './use-chat-stream';

/** Encode SSE events into a ReadableStream that fetch() can return. */
function makeSSEStream(events: Array<{ data: string; event: string }>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const chunks = events.map((e) => `event: ${e.event}\ndata: ${e.data}\n\n`);
  let index = 0;
  return new ReadableStream({
    pull(controller) {
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index++;
      } else {
        controller.close();
      }
    },
  });
}

function mockFetchWithEvents(events: Array<{ data: string; event: string }>) {
  return vi.fn().mockResolvedValue({
    body: makeSSEStream(events),
    ok: true,
  });
}

const THREAD_ID = 'abc-123';

const STANDARD_EVENTS: Array<{ data: string; event: string }> = [
  { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
  {
    data: JSON.stringify({ label: 'Generating SQL query', node: 'generate_sql', query_index: 1 }),
    event: 'node_start',
  },
  {
    data: JSON.stringify({
      content: 'Hello ',
      message_type: 'agent_talk',
      source: 'agent',
    }),
    event: 'agent_talk',
  },
  {
    data: JSON.stringify({
      content: 'world',
      message_type: 'agent_talk',
      source: 'agent',
    }),
    event: 'agent_talk',
  },
  {
    data: JSON.stringify({
      content: 'SELECT * FROM trade',
      message_type: 'tool_call',
      name: 'execute_sql',
      source: 'tool',
    }),
    event: 'tool_call',
  },
  {
    data: JSON.stringify({
      content: 'rows: 10',
      message_type: 'tool_output',
      name: 'execute_sql',
      source: 'tool',
    }),
    event: 'tool_output',
  },
  {
    data: JSON.stringify({ stage: 'generate_sql' }),
    event: 'pipeline_state',
  },
  {
    data: JSON.stringify({
      thread_id: THREAD_ID,
      total_execution_time_ms: 100,
      total_queries: 1,
      total_rows: 10,
      total_time_ms: 500,
    }),
    event: 'done',
  },
];

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => ({}),
  useSearchParams: () => [new URLSearchParams()],
}));

describe('useChatStream', () => {
  it('has correct initial state', () => {
    global.fetch = vi.fn();
    const { result } = renderHook(() => useChatStream());

    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.pipelineSteps).toEqual([]);
    expect(result.current.threadId).toBeNull();
  });

  it('adds user message and empty assistant message on sendMessage', async () => {
    global.fetch = mockFetchWithEvents([
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({
          thread_id: THREAD_ID,
          total_execution_time_ms: 0,
          total_queries: 0,
          total_rows: 0,
          total_time_ms: 0,
        }),
        event: 'done',
      },
    ]);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('What are the top exports?');
    });

    await waitFor(() => {
      expect(result.current.messages.length).toBeGreaterThanOrEqual(2);
      expect(result.current.messages[0].role).toBe('user');
      expect(result.current.messages[0].content).toBe('What are the top exports?');
      expect(result.current.messages[1].role).toBe('assistant');
    });
  });

  it('stores threadId from thread_id event', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.threadId).toBe(THREAD_ID);
    });
  });

  it('accumulates agent_talk chunks into assistant content', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.content).toBe('Hello world');
    });
  });

  it('tracks node_start as active pipeline step', async () => {
    global.fetch = mockFetchWithEvents([
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({
          label: 'Generating SQL query',
          node: 'generate_sql',
          query_index: 1,
        }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({ content: 'hi', message_type: 'agent_talk', source: 'agent' }),
        event: 'agent_talk',
      },
      {
        data: JSON.stringify({
          thread_id: THREAD_ID,
          total_execution_time_ms: 0,
          total_queries: 0,
          total_rows: 0,
          total_time_ms: 0,
        }),
        event: 'done',
      },
    ]);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.pipelineSteps.length).toBeGreaterThanOrEqual(1);
      const step = result.current.pipelineSteps.find((s) => s.node === 'generate_sql');
      expect(step).toBeDefined();
      expect(step?.label).toBe('Generating SQL query');
    });
  });

  it('marks pipeline step completed on pipeline_state', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      const step = result.current.pipelineSteps.find((s) => s.node === 'generate_sql');
      expect(step?.status).toBe('completed');
    });
  });

  it('sets isStreaming to false on done event', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
      const assistant = result.current.messages.find((m) => m.role === 'assistant');
      expect(assistant?.isStreaming).toBe(false);
    });
  });

  it('sets error on fetch failure', async () => {
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
      expect(result.current.error).toBeTruthy();
      expect(result.current.isStreaming).toBe(false);
    });
  });

  it('resets all state on clearChat', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.messages.length).toBeGreaterThan(0);
    });

    act(() => {
      result.current.clearChat();
    });

    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.pipelineSteps).toEqual([]);
    expect(result.current.threadId).toBeNull();
  });
});
