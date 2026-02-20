/**
 * Shared SSE stream helpers for integration tests.
 *
 * createControllableStream — push events one at a time to test incremental
 * state updates (exposes React batching / compiler issues).
 *
 * makeAsyncSSEStream — delivers all events with real async delays.
 */

type QueueItem = { type: 'chunk'; value: Uint8Array } | { type: 'close' };

export interface ControllableStream {
  close: () => void;
  pushEvent: (event: { data: string; event: string }) => void;
  pushRaw: (text: string) => void;
  stream: ReadableStream<Uint8Array>;
}

export function createControllableStream(): ControllableStream {
  const encoder = new TextEncoder();
  const queue: Array<QueueItem> = [];
  let waitingPull: (() => void) | null = null;

  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      while (queue.length === 0) {
        await new Promise<void>((resolve) => {
          waitingPull = resolve;
        });
      }
      const item = queue.shift()!;
      if (item.type === 'close') {
        controller.close();
      } else {
        controller.enqueue(item.value);
      }
    },
  });

  function notify() {
    if (waitingPull) {
      const fn = waitingPull;
      waitingPull = null;
      fn();
    }
  }

  return {
    close() {
      queue.push({ type: 'close' });
      notify();
    },
    pushEvent(event: { data: string; event: string }) {
      const chunk = `event: ${event.event}\ndata: ${event.data}\n\n`;
      queue.push({ type: 'chunk', value: encoder.encode(chunk) });
      notify();
    },
    pushRaw(text: string) {
      queue.push({ type: 'chunk', value: encoder.encode(text) });
      notify();
    },
    stream,
  };
}

export function makeAsyncSSEStream(
  events: Array<{ data: string; event: string }>,
  delayMs = 10,
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  return new ReadableStream({
    async pull(controller) {
      if (index < events.length) {
        await new Promise((r) => setTimeout(r, delayMs));
        const chunk = `event: ${events[index].event}\ndata: ${events[index].data}\n\n`;
        controller.enqueue(encoder.encode(chunk));
        index++;
      } else {
        controller.close();
      }
    },
  });
}

// ---- Standard event factory helpers ----

export const THREAD_ID = 'test-thread-abc-123';

export function makeThreadIdEvent(threadId = THREAD_ID) {
  return { data: JSON.stringify({ thread_id: threadId }), event: 'thread_id' };
}

export function makeAgentTalkEvent(content: string) {
  return {
    data: JSON.stringify({ content, message_type: 'agent_talk', source: 'agent' }),
    event: 'agent_talk',
  };
}

export function makeNodeStartEvent(node: string, label: string) {
  return {
    data: JSON.stringify({ label, node, query_index: 1 }),
    event: 'node_start',
  };
}

export function makePipelineStateEvent(stage: string) {
  return { data: JSON.stringify({ stage }), event: 'pipeline_state' };
}

export function makeDoneEvent(threadId = THREAD_ID) {
  return {
    data: JSON.stringify({
      thread_id: threadId,
      total_execution_time_ms: 100,
      total_queries: 1,
      total_rows: 10,
      total_time_ms: 500,
    }),
    event: 'done',
  };
}

export const STANDARD_EVENTS = [
  makeThreadIdEvent(),
  makeNodeStartEvent('generate_sql', 'Generating SQL query'),
  makeAgentTalkEvent('Hello '),
  makeAgentTalkEvent('world'),
  makePipelineStateEvent('generate_sql'),
  makeDoneEvent(),
];
