import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import type {
  AtlasLink,
  ChatMessage,
  EntitiesData,
  GraphqlSummary,
  PipelineStep,
  QueryAggregateStats,
  TradeOverrides,
  TurnSummary,
} from '@/types/chat';
import { API_BASE_URL } from '@/config';
import { classifyPipelineNode } from '@/utils/pipeline-type';
import { getSessionId } from '@/utils/session';

interface UseChatStreamOptions {
  onConversationChange?: () => void;
  onOverridesLoaded?: (o: TradeOverrides) => void;
}

interface UseChatStreamReturn {
  clearChat: () => void;
  entitiesData: EntitiesData | null;
  error: null | string;
  isRestoredThread: boolean;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
  sendMessage: (question: string, overrides?: TradeOverrides) => void;
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
    atlasLinks: [],
    content,
    docsConsulted: [],
    graphqlSummaries: [],
    id: `msg-${Date.now()}-${messageCounter}`,
    isStreaming,
    queryResults: [],
    role,
  };
}

function emptyEntities(): EntitiesData {
  return {
    countries: [],
    docsConsulted: [],
    graphqlClassification: null,
    graphqlEntities: null,
    lookupCodes: '',
    products: [],
    resolutionNotes: [],
    schemas: [],
  };
}

export function useChatStream(options?: UseChatStreamOptions): UseChatStreamReturn {
  const [messages, setMessages] = useState<Array<ChatMessage>>([]);
  const [pipelineSteps, setPipelineSteps] = useState<Array<PipelineStep>>([]);
  const [threadId, setThreadId] = useState<null | string>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<null | string>(null);
  const [entitiesData, setEntitiesData] = useState<EntitiesData | null>(null);
  const [queryStats, setQueryStats] = useState<QueryAggregateStats | null>(null);
  const [isRestoredThread, setIsRestoredThread] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);
  const initialQuerySent = useRef(false);
  const historyLoaded = useRef<string | null>(null);
  // Session-level cache: threadId → array of pipelineSteps (one per assistant turn)
  const pipelineStepsCache = useRef<Map<string, Array<Array<PipelineStep>>>>(new Map());
  const onConversationChangeRef = useRef(options?.onConversationChange);
  onConversationChangeRef.current = options?.onConversationChange;
  const onOverridesLoadedRef = useRef(options?.onOverridesLoaded);
  onOverridesLoadedRef.current = options?.onOverridesLoaded;

  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { threadId: urlThreadId } = useParams<{ threadId: string }>();

  const sendMessage = useCallback(
    (question: string, overrides?: TradeOverrides) => {
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
      setIsRestoredThread(false);
      setError(null);

      const body: Record<string, string> = { question: trimmed };
      if (threadId) {
        body.thread_id = threadId;
      }
      if (overrides?.schema) {
        body.override_schema = overrides.schema;
      }
      if (overrides?.direction) {
        body.override_direction = overrides.direction;
      }
      if (overrides?.mode) {
        body.override_mode = overrides.mode;
      }
      if (overrides?.systemMode) {
        body.mode = overrides.systemMode;
      }

      (async () => {
        // rAF batching: accumulate tokens in a local variable and flush
        // to React state at frame rate (~60fps) to prevent React 19's
        // automatic batching from merging all setState calls into one render.
        let contentAcc = '';
        let rafId: null | number = null;
        let streamThreadId: null | string = null;

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
          const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
            body: JSON.stringify(body),
            headers: {
              'Content-Type': 'application/json',
              'X-Session-Id': getSessionId(),
            },
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

              case 'atlas_links': {
                const links: Array<AtlasLink> = parsed.atlas_links ?? [];
                if (links.length > 0) {
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMsg.id
                        ? { ...m, atlasLinks: [...m.atlasLinks, ...links] }
                        : m,
                    ),
                  );
                }
                break;
              }

              case 'done':
                stopRaf();
                // Snapshot pipeline steps onto the assistant message before clearing
                setPipelineSteps((currentSteps) => {
                  if (currentSteps.length > 0) {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === assistantMsg.id
                          ? {
                              ...m,
                              content: contentAcc,
                              isStreaming: false,
                              pipelineSteps: currentSteps,
                            }
                          : m,
                      ),
                    );
                    // Cache steps for this thread so they survive thread switches
                    if (streamThreadId) {
                      const cached = pipelineStepsCache.current.get(streamThreadId) ?? [];
                      cached.push(currentSteps);
                      pipelineStepsCache.current.set(streamThreadId, cached);
                    }
                  } else {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === assistantMsg.id
                          ? { ...m, content: contentAcc, isStreaming: false }
                          : m,
                      ),
                    );
                  }
                  return []; // Clear global steps
                });
                setIsStreaming(false);
                if (parsed.total_queries != null || parsed.total_graphql_queries != null) {
                  setQueryStats({
                    totalExecutionTimeMs: parsed.total_execution_time_ms ?? 0,
                    totalGraphqlQueries: parsed.total_graphql_queries ?? 0,
                    totalGraphqlTimeMs: parsed.total_graphql_time_ms ?? 0,
                    totalQueries: parsed.total_queries ?? 0,
                    totalRows: parsed.total_rows ?? 0,
                    totalTimeMs: parsed.total_time_ms ?? 0,
                  });
                }
                onConversationChangeRef.current?.();
                break;

              case 'node_start':
                setPipelineSteps((prev) => [
                  ...prev,
                  {
                    label: parsed.label,
                    node: parsed.node,
                    pipelineType: classifyPipelineNode(parsed.node),
                    queryIndex: parsed.query_index ?? 0,
                    startedAt: Date.now(),
                    status: 'active' as const,
                  },
                ]);
                break;

              case 'pipeline_state':
                setPipelineSteps((prev) => {
                  // Find the LAST step with matching node that is still active
                  const targetIdx = prev.findLastIndex(
                    (s) => s.node === parsed.stage && s.status === 'active',
                  );
                  if (targetIdx === -1) {
                    return prev;
                  }
                  return prev.map((step, i) =>
                    i === targetIdx
                      ? {
                          ...step,
                          completedAt: Date.now(),
                          detail: parsed,
                          status: 'completed' as const,
                        }
                      : step,
                  );
                });

                // --- SQL pipeline stages ---
                if (parsed.stage === 'extract_products' && parsed.products) {
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    countries: (parsed.countries ?? []).map(
                      (c: { iso3_code: string; name: string }) => ({
                        iso3Code: c.iso3_code,
                        name: c.name,
                      }),
                    ),
                    products: parsed.products,
                    schemas: parsed.schemas ?? [],
                  }));
                } else if (parsed.stage === 'lookup_codes' && parsed.codes) {
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    lookupCodes: parsed.codes,
                  }));
                }

                // --- GraphQL pipeline stages ---
                if (parsed.stage === 'classify_query') {
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    graphqlClassification: {
                      apiTarget: '',
                      isRejected: parsed.is_rejected ?? false,
                      queryType: parsed.query_type ?? '',
                      rejectionReason: parsed.rejection_reason ?? '',
                    },
                  }));
                } else if (parsed.stage === 'extract_entities') {
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    graphqlEntities: parsed.entities ?? {},
                  }));
                } else if (parsed.stage === 'resolve_ids') {
                  const resolved = parsed.resolved_ids ?? {};
                  const notes: Array<string> = [];
                  for (const [key, val] of Object.entries(resolved)) {
                    if (val != null) {
                      notes.push(`${key}: ${String(val)}`);
                    }
                  }
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    resolutionNotes: notes,
                  }));
                } else if (parsed.stage === 'build_and_execute_graphql') {
                  // Update classification with api_target and build a graphql summary
                  setEntitiesData((prev) => {
                    const updated = { ...(prev ?? emptyEntities()) };
                    if (updated.graphqlClassification) {
                      updated.graphqlClassification = {
                        ...updated.graphqlClassification,
                        apiTarget: parsed.api_target ?? '',
                      };
                    }
                    return updated;
                  });
                  // Build GraphQL summary and attach to message
                  setMessages((prevMsgs) =>
                    prevMsgs.map((m) => {
                      if (m.id !== assistantMsg.id) {
                        return m;
                      }
                      const summary: GraphqlSummary = {
                        apiTarget: parsed.api_target ?? '',
                        classification: {
                          apiTarget: parsed.api_target ?? '',
                          isRejected: parsed.is_rejected ?? false,
                          queryType: parsed.query_type ?? '',
                          rejectionReason: parsed.rejection_reason ?? '',
                        },
                        entities: parsed.entities ?? {},
                        executionTimeMs: parsed.execution_time_ms ?? 0,
                        links: [],
                      };
                      return {
                        ...m,
                        graphqlSummaries: [...m.graphqlSummaries, summary],
                      };
                    }),
                  );
                } else if (parsed.stage === 'format_graphql_results') {
                  // Attach atlas_links to the last GraphQL summary on the message
                  const links: Array<AtlasLink> = parsed.atlas_links ?? [];
                  if (links.length > 0) {
                    setMessages((prevMsgs) =>
                      prevMsgs.map((m) => {
                        if (m.id !== assistantMsg.id || m.graphqlSummaries.length === 0) {
                          return m;
                        }
                        const last = m.graphqlSummaries.at(-1)!;
                        return {
                          ...m,
                          graphqlSummaries: [
                            ...m.graphqlSummaries.slice(0, -1),
                            { ...last, links },
                          ],
                        };
                      }),
                    );
                  }
                }

                // --- Docs pipeline stages ---
                if (parsed.stage === 'select_docs' && parsed.selected_files) {
                  setEntitiesData((prev) => ({
                    ...(prev ?? emptyEntities()),
                    docsConsulted: parsed.selected_files,
                  }));
                  setMessages((prevMsgs) =>
                    prevMsgs.map((m) =>
                      m.id === assistantMsg.id ? { ...m, docsConsulted: parsed.selected_files } : m,
                    ),
                  );
                }

                // --- SQL query tracking (existing) ---
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
                streamThreadId = id;
                setThreadId(id);
                // Mark as loaded so the history effect doesn't try to fetch
                // — messages for this thread are being streamed live.
                historyLoaded.current = id;
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
    setEntitiesData(null);
    setError(null);
    setIsRestoredThread(false);
    setIsStreaming(false);
    setMessages([]);
    setPipelineSteps([]);
    setQueryStats(null);
    setThreadId(null);
    navigate('/chat', { replace: true });
  }, [navigate]);

  // Reset historyLoaded when navigating away from a thread (e.g. clearChat → /chat).
  // This allows re-loading the same thread if the user navigates back to it.
  // Crucially, clearChat does NOT reset historyLoaded — the old value matches
  // urlThreadId during the intermediate render before navigate takes effect,
  // preventing an unwanted re-fetch of the thread being cleared.
  useEffect(() => {
    if (!urlThreadId) {
      historyLoaded.current = null;
    }
  }, [urlThreadId]);

  // Load thread history when navigating to /chat/:threadId.
  // Handles both initial page loads and direct thread-to-thread switches
  // (clicking a different conversation in the sidebar without clearing first).
  useEffect(() => {
    if (!urlThreadId || historyLoaded.current === urlThreadId) {
      return;
    }

    // Reset state from previous thread before loading new one
    setMessages([]);
    setPipelineSteps([]);
    setEntitiesData(null);
    setQueryStats(null);
    setError(null);

    historyLoaded.current = urlThreadId;
    setThreadId(urlThreadId);

    (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/threads/${urlThreadId}/messages`, {
          headers: { 'X-Session-Id': getSessionId() },
        });
        if (!response.ok) {
          return;
        }
        const data: unknown = await response.json();

        // Support both new shape {messages, overrides} and legacy array shape
        const messageList: Array<{ content: string; role: 'ai' | 'human' }> = Array.isArray(data)
          ? data
          : (data as { messages: Array<{ content: string; role: 'ai' | 'human' }> }).messages;

        const loaded = messageList.map((m) =>
          createMessage(m.role === 'human' ? 'user' : 'assistant', m.content),
        );

        // Hydrate right panel from turn_summaries if present
        const turnSummaries: Array<TurnSummary> =
          !Array.isArray(data) &&
          Array.isArray((data as { turn_summaries?: unknown }).turn_summaries)
            ? (data as { turn_summaries: Array<TurnSummary> }).turn_summaries
            : [];

        if (turnSummaries.length > 0) {
          // Pair summaries with assistant messages (1:1 by index)
          const assistantMessages = loaded.filter((m) => m.role === 'assistant');
          for (let i = 0; i < Math.min(assistantMessages.length, turnSummaries.length); i++) {
            const ts = turnSummaries[i];
            if (ts.queries.length > 0) {
              assistantMessages[i].queryResults = ts.queries.map((q) => ({
                columns: q.columns,
                executionTimeMs: q.execution_time_ms,
                rowCount: q.row_count,
                rows: q.rows,
                sql: q.sql,
              }));
            }
            // Hydrate atlas links from turn summary
            if (ts.atlas_links && ts.atlas_links.length > 0) {
              assistantMessages[i].atlasLinks = ts.atlas_links;
            }
            // Hydrate docs consulted from turn summary
            if (ts.docs_consulted && ts.docs_consulted.length > 0) {
              assistantMessages[i].docsConsulted = ts.docs_consulted;
            }
            // Hydrate graphql summaries from turn summary
            if (ts.graphql_summaries && ts.graphql_summaries.length > 0) {
              assistantMessages[i].graphqlSummaries = ts.graphql_summaries.map((gs) => ({
                apiTarget: gs.api_target,
                classification: {
                  apiTarget: gs.api_target,
                  isRejected: gs.classification.is_rejected,
                  queryType: gs.classification.query_type,
                  rejectionReason: gs.classification.rejection_reason,
                },
                entities: gs.entities,
                executionTimeMs: gs.execution_time_ms,
                links: gs.links,
              }));
            }
          }

          // Set entities from the last summary that has them
          const lastWithEntities = [...turnSummaries].reverse().find((ts) => ts.entities !== null);
          if (lastWithEntities?.entities) {
            setEntitiesData({
              countries: (lastWithEntities.entities.countries ?? []).map(
                (c: { iso3_code: string; name: string }) => ({
                  iso3Code: c.iso3_code,
                  name: c.name,
                }),
              ),
              docsConsulted: [],
              graphqlClassification: null,
              graphqlEntities: null,
              lookupCodes: '',
              products: lastWithEntities.entities.products,
              resolutionNotes: [],
              schemas: lastWithEntities.entities.schemas,
            });
          }

          // Set query stats from totals across all summaries
          const totalRows = turnSummaries.reduce((sum, ts) => sum + ts.total_rows, 0);
          const totalExecMs = turnSummaries.reduce(
            (sum, ts) => sum + ts.total_execution_time_ms,
            0,
          );
          const totalQueries = turnSummaries.reduce((sum, ts) => sum + ts.queries.length, 0);
          const totalGraphqlTimeMs = turnSummaries.reduce(
            (sum, ts) => sum + (ts.total_graphql_time_ms ?? 0),
            0,
          );
          if (totalQueries > 0 || totalGraphqlTimeMs > 0) {
            setQueryStats({
              totalExecutionTimeMs: totalExecMs,
              totalGraphqlQueries: 0,
              totalGraphqlTimeMs: totalGraphqlTimeMs,
              totalQueries,
              totalRows,
              totalTimeMs: 0,
            });
          }
        }

        // Restore cached pipeline steps from this session
        const cachedSteps = pipelineStepsCache.current.get(urlThreadId);
        if (cachedSteps && cachedSteps.length > 0) {
          const assistantMsgs = loaded.filter((m) => m.role === 'assistant');
          for (let i = 0; i < Math.min(assistantMsgs.length, cachedSteps.length); i++) {
            assistantMsgs[i].pipelineSteps = cachedSteps[i];
          }
        }

        setMessages(loaded);
        setIsRestoredThread(true);

        // Restore trade overrides from history if present
        if (!Array.isArray(data) && (data as { overrides?: unknown }).overrides) {
          const raw = (data as { overrides: Record<string, string | null> }).overrides;
          onOverridesLoadedRef.current?.({
            direction: (raw.override_direction as TradeOverrides['direction']) ?? null,
            mode: (raw.override_mode as TradeOverrides['mode']) ?? null,
            schema: (raw.override_schema as TradeOverrides['schema']) ?? null,
            systemMode: null,
          });
        }
      } catch {
        // Silently fail — user can still send new messages
      }
    })();
  }, [urlThreadId]);

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
    entitiesData,
    error,
    isRestoredThread,
    isStreaming,
    messages,
    pipelineSteps,
    queryStats,
    sendMessage,
    threadId,
  };
}
