import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EntitiesData, QueryAggregateStats } from '@/types/chat';
import { useChatStream } from './use-chat-stream';

vi.mock('@/utils/session', () => ({
  getSessionId: () => 'test-session-id',
}));

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

// Matches real backend event ordering: node_start → tool_call → tool_output →
// pipeline_state (completed) → agent_talk (response text) → done
const STANDARD_EVENTS: Array<{ data: string; event: string }> = [
  { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
  {
    data: JSON.stringify({ label: 'Generating SQL query', node: 'generate_sql', query_index: 1 }),
    event: 'node_start',
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
      thread_id: THREAD_ID,
      total_execution_time_ms: 100,
      total_queries: 1,
      total_rows: 10,
      total_time_ms: 500,
    }),
    event: 'done',
  },
];

// Mock useNavigate and useParams
const mockNavigate = vi.fn();
let mockParams: Record<string, string> = {};

vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
  useParams: () => mockParams,
  useSearchParams: () => [new URLSearchParams()],
}));

beforeEach(() => {
  mockNavigate.mockReset();
  mockParams = {};
});

describe('useChatStream', () => {
  it('has correct initial state', () => {
    global.fetch = vi.fn();
    const { result } = renderHook(() => useChatStream());

    expect(result.current.entitiesData).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.pipelineSteps).toEqual([]);
    expect(result.current.queryStats).toBeNull();
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
    // No agent_talk — steps persist until stream ends so waitFor can observe them
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
    // No agent_talk — steps persist until stream ends so waitFor can observe completed status
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
        data: JSON.stringify({ stage: 'generate_sql' }),
        event: 'pipeline_state',
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
      const step = result.current.pipelineSteps.find((s) => s.node === 'generate_sql');
      expect(step?.status).toBe('completed');
    });
  });

  it('clears pipeline steps when first agent_talk arrives', async () => {
    // Events: node_start → pipeline_state (completed) → agent_talk → done
    const events = [
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({ label: 'Generating SQL', node: 'generate_sql', query_index: 1 }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({ stage: 'generate_sql' }),
        event: 'pipeline_state',
      },
      {
        data: JSON.stringify({
          content: 'Here are results.',
          message_type: 'agent_talk',
          source: 'agent',
        }),
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
    ];
    global.fetch = mockFetchWithEvents(events);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    // Pipeline steps should have been cleared when agent_talk arrived
    expect(result.current.pipelineSteps).toEqual([]);
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

    expect(result.current.entitiesData).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.messages).toEqual([]);
    expect(result.current.pipelineSteps).toEqual([]);
    expect(result.current.queryStats).toBeNull();
    expect(result.current.threadId).toBeNull();
  });

  it('populates entitiesData from extract_products pipeline_state', async () => {
    const events = [
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({ label: 'Extracting products', node: 'extract_products' }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({
          products: [{ codes: ['8541', '8542'], name: 'Semiconductors', schema: 'hs92' }],
          schemas: ['hs92'],
          stage: 'extract_products',
        }),
        event: 'pipeline_state',
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
    ];
    global.fetch = mockFetchWithEvents(events);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.entitiesData).not.toBeNull();
      const data = result.current.entitiesData as EntitiesData;
      expect(data.products).toHaveLength(1);
      expect(data.products[0].name).toBe('Semiconductors');
      expect(data.products[0].codes).toEqual(['8541', '8542']);
      expect(data.schemas).toEqual(['hs92']);
    });
  });

  it('updates entitiesData.lookupCodes from lookup_codes pipeline_state', async () => {
    const events = [
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({ label: 'Extracting products', node: 'extract_products' }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({
          products: [{ codes: ['8541'], name: 'Semiconductors', schema: 'hs92' }],
          schemas: ['hs92'],
          stage: 'extract_products',
        }),
        event: 'pipeline_state',
      },
      {
        data: JSON.stringify({ label: 'Looking up codes', node: 'lookup_codes' }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({
          lookup_codes: '8541,8542',
          stage: 'lookup_codes',
        }),
        event: 'pipeline_state',
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
    ];
    global.fetch = mockFetchWithEvents(events);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.entitiesData?.lookupCodes).toBe('8541,8542');
    });
  });

  it('populates queryStats from done event', async () => {
    const events = [
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({
          content: 'result',
          message_type: 'agent_talk',
          source: 'agent',
        }),
        event: 'agent_talk',
      },
      {
        data: JSON.stringify({
          thread_id: THREAD_ID,
          total_execution_time_ms: 150,
          total_queries: 3,
          total_rows: 42,
          total_time_ms: 2100,
        }),
        event: 'done',
      },
    ];
    global.fetch = mockFetchWithEvents(events);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.queryStats).not.toBeNull();
      const stats = result.current.queryStats as QueryAggregateStats;
      expect(stats.totalQueries).toBe(3);
      expect(stats.totalRows).toBe(42);
      expect(stats.totalExecutionTimeMs).toBe(150);
      expect(stats.totalTimeMs).toBe(2100);
    });
  });

  it('sets startedAt on node_start and completedAt+detail on pipeline_state', async () => {
    const events = [
      { data: JSON.stringify({ thread_id: THREAD_ID }), event: 'thread_id' },
      {
        data: JSON.stringify({ label: 'Generating SQL', node: 'generate_sql', query_index: 1 }),
        event: 'node_start',
      },
      {
        data: JSON.stringify({ sql: 'SELECT 1', stage: 'generate_sql' }),
        event: 'pipeline_state',
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
    ];
    global.fetch = mockFetchWithEvents(events);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      const step = result.current.pipelineSteps.find((s) => s.node === 'generate_sql');
      expect(step).toBeDefined();
      expect(step?.startedAt).toEqual(expect.any(Number));
      expect(step?.completedAt).toEqual(expect.any(Number));
      expect(step?.detail).toEqual(expect.objectContaining({ sql: 'SELECT 1' }));
    });
  });

  it('sends X-Session-Id header with chat stream requests', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const { result } = renderHook(() => useChatStream());

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/chat/stream',
      expect.objectContaining({
        headers: expect.objectContaining({
          'X-Session-Id': 'test-session-id',
        }),
      }),
    );
  });

  it('calls onConversationChange after done event', async () => {
    global.fetch = mockFetchWithEvents(STANDARD_EVENTS);

    const onConversationChange = vi.fn();
    const { result } = renderHook(() => useChatStream({ onConversationChange }));

    act(() => {
      result.current.sendMessage('hello');
    });

    await waitFor(() => {
      expect(result.current.isStreaming).toBe(false);
    });

    expect(onConversationChange).toHaveBeenCalled();
  });

  it('loads thread history when URL has threadId and no messages', async () => {
    mockParams = { threadId: 'existing-thread' };

    global.fetch = vi.fn().mockResolvedValue({
      json: () =>
        Promise.resolve([
          { content: 'What are top exports?', role: 'human' },
          { content: 'The top exports include soybeans.', role: 'ai' },
        ]),
      ok: true,
    });

    const { result } = renderHook(() => useChatStream());

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(2);
    });

    expect(result.current.messages[0].role).toBe('user');
    expect(result.current.messages[0].content).toBe('What are top exports?');
    expect(result.current.messages[1].role).toBe('assistant');
    expect(result.current.messages[1].content).toBe('The top exports include soybeans.');
    expect(result.current.threadId).toBe('existing-thread');

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/threads/existing-thread/messages',
      expect.objectContaining({
        headers: { 'X-Session-Id': 'test-session-id' },
      }),
    );
  });

  it('clearChat navigates to /chat', async () => {
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

    expect(mockNavigate).toHaveBeenCalledWith('/chat', { replace: true });
  });
});
