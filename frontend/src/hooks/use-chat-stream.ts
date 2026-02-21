import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { ChatMessage, PipelineStep } from '@/types/chat';

interface UseChatStreamReturn {
  clearChat: () => void;
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  pipelineSteps: Array<PipelineStep>;
  sendMessage: (question: string) => void;
  threadId: null | string;
}

/** Parse an SSE stream from a ReadableStream<Uint8Array>. */
async function* parseSSE(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<{ data: string; event: string }> {
  const decoder = new TextDecoder();
  const reader = body.getReader();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true }).replaceAll('\r\n', '\n');

      const parts = buffer.split('\n\n');
      // Keep last part as potential incomplete chunk
      buffer = parts.pop() ?? '';

      for (const part of parts) {
        const trimmed = part.trim();
        if (!trimmed) {
          continue;
        }

        let event = 'message';
        let data = '';

        for (const line of trimmed.split('\n')) {
          if (line.startsWith('event: ')) {
            event = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            data = line.slice(6);
          }
        }

        if (data) {
          yield { data, event };
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

let messageCounter = 0;

function createMessage(
  role: 'assistant' | 'user',
  content: string,
  isStreaming = false,
): ChatMessage {
  messageCounter++;
  return {
    content,
    id: `msg-${Date.now()}-${messageCounter}`,
    isStreaming,
    queryResults: [],
    role,
  };
}

export function useChatStream(): UseChatStreamReturn {
  const [messages, setMessages] = useState<Array<ChatMessage>>([]);
  const [pipelineSteps, setPipelineSteps] = useState<Array<PipelineStep>>([]);
  const [threadId, setThreadId] = useState<null | string>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<null | string>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const initialQuerySent = useRef(false);

  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const sendMessage = useCallback(
    (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || isStreaming) {
        return;
      }

      // Abort any in-flight request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;

      // Timeout after 30s if no response (e.g. backend down)
      let timedOut = false;
      const timeoutId = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 30_000);

      const userMsg = createMessage('user', trimmed);
      const assistantMsg = createMessage('assistant', '', true);

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setPipelineSteps([]);
      setIsStreaming(true);
      setError(null);

      const body: Record<string, string> = { question: trimmed };
      if (threadId) {
        body.thread_id = threadId;
      }

      (async () => {
        // rAF batching: accumulate tokens in a local variable and flush
        // to React state at frame rate (~60fps) to prevent React 19's
        // automatic batching from merging all setState calls into one render.
        let contentAcc = '';
        let rafId: null | number = null;

        function flushContent() {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMsg.id ? { ...m, content: contentAcc } : m)),
          );
          rafId = requestAnimationFrame(flushContent);
        }

        function stopRaf() {
          if (rafId !== null) {
            cancelAnimationFrame(rafId);
            rafId = null;
          }
        }

        try {
          const response = await fetch('/api/chat/stream', {
            body: JSON.stringify(body),
            headers: { 'Content-Type': 'application/json' },
            method: 'POST',
            signal: controller.signal,
          });
          clearTimeout(timeoutId);

          if (!response.ok) {
            setError(`Server error: ${response.status} ${response.statusText}`);
            setIsStreaming(false);
            setMessages((prev) => prev.filter((m) => m.id !== assistantMsg.id));
            return;
          }

          if (!response.body) {
            setError('No response body');
            setIsStreaming(false);
            return;
          }

          // Start rAF flush loop
          rafId = requestAnimationFrame(flushContent);

          for await (const { data, event } of parseSSE(response.body)) {
            if (controller.signal.aborted) {
              break;
            }

            const parsed = JSON.parse(data);

            switch (event) {
              case 'agent_talk':
                contentAcc += parsed.content ?? '';
                break;

              case 'done':
                stopRaf();
                setIsStreaming(false);
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMsg.id
                      ? { ...m, content: contentAcc, isStreaming: false }
                      : m,
                  ),
                );
                break;

              case 'node_start':
                setPipelineSteps((prev) => [
                  ...prev,
                  {
                    label: parsed.label,
                    node: parsed.node,
                    status: 'active' as const,
                  },
                ]);
                break;

              case 'pipeline_state':
                setPipelineSteps((prev) =>
                  prev.map((step) =>
                    step.node === parsed.stage ? { ...step, status: 'completed' as const } : step,
                  ),
                );

                if (parsed.stage === 'generate_sql' && parsed.sql) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id
                        ? {
                            ...m,
                            queryResults: [
                              ...m.queryResults,
                              {
                                columns: [],
                                executionTimeMs: 0,
                                rowCount: 0,
                                rows: [],
                                sql: parsed.sql,
                              },
                            ],
                          }
                        : m,
                    ),
                  );
                } else if (parsed.stage === 'execute_sql' && parsed.columns) {
                  setMessages((prev) =>
                    prev.map((m) => {
                      if (m.id !== assistantMsg.id || m.queryResults.length === 0) {
                        return m;
                      }
                      const last = m.queryResults.at(-1)!;
                      return {
                        ...m,
                        queryResults: [
                          ...m.queryResults.slice(0, -1),
                          {
                            ...last,
                            columns: parsed.columns ?? [],
                            executionTimeMs: parsed.execution_time_ms ?? 0,
                            rowCount: parsed.row_count ?? 0,
                            rows: parsed.rows ?? [],
                          },
                        ],
                      };
                    }),
                  );
                }
                break;

              case 'thread_id': {
                const id = parsed.thread_id;
                setThreadId(id);
                navigate(`/chat/${id}`, { replace: true });
                break;
              }

              default:
                break;
            }
          }

          // Stream ended without a done event — final flush
          stopRaf();
        } catch (error: unknown) {
          stopRaf();
          clearTimeout(timeoutId);

          const isAbortError =
            typeof error === 'object' &&
            error !== null &&
            'name' in error &&
            (error as { name: string }).name === 'AbortError';

          if (isAbortError) {
            if (timedOut) {
              setError('Request timed out. The server may be unavailable.');
            }
            // Always reset streaming state on abort (including StrictMode cleanup)
            setIsStreaming(false);
            setMessages((prev) =>
              prev.map((m) => (m.id === assistantMsg.id ? { ...m, isStreaming: false } : m)),
            );
            return;
          }

          const message =
            typeof error === 'object' && error !== null && 'message' in error
              ? String((error as { message: unknown }).message)
              : 'Unknown error';
          setError(message);
          setIsStreaming(false);
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMsg.id ? { ...m, isStreaming: false } : m)),
          );
        }
      })();
    },
    [isStreaming, navigate, threadId],
  );

  const clearChat = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    setError(null);
    setIsStreaming(false);
    setMessages([]);
    setPipelineSteps([]);
    setThreadId(null);
  }, []);

  // Auto-submit from ?q= param on mount.
  // Uses setTimeout(0) to defer past React StrictMode's synchronous
  // cleanup-remount cycle, which would otherwise abort the in-flight fetch.
  useEffect(() => {
    const q = searchParams.get('q');
    if (!q || initialQuerySent.current) {
      return;
    }

    const timerId = setTimeout(() => {
      if (!initialQuerySent.current) {
        initialQuerySent.current = true;
        sendMessage(q);
      }
    }, 0);

    return () => {
      clearTimeout(timerId);
    };
  }, [searchParams, sendMessage]);

  // Abort on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  return {
    clearChat,
    error,
    isStreaming,
    messages,
    pipelineSteps,
    sendMessage,
    threadId,
  };
}
