import { useCallback, useEffect, useReducer } from 'react';
import type { FeedbackState } from '@/types/chat';
import { API_BASE_URL } from '@/config';
import { getSessionId } from '@/utils/session';

interface BackendFeedback {
  comment: string | null;
  id: number;
  rating: 'down' | 'up';
  turn_index: number;
}

export interface UseFeedbackReturn {
  feedbackMap: Map<number, FeedbackState>;
  submitFeedback: (turnIndex: number, rating: 'down' | 'up', comment?: string) => void;
  updateFeedback: (
    feedbackId: number,
    turnIndex: number,
    rating: 'down' | 'up',
    comment?: string,
  ) => void;
}

type FeedbackAction =
  | { entries: Array<BackendFeedback>; type: 'loaded' }
  | { type: 'reset' }
  | { entry: FeedbackState; turnIndex: number; type: 'set' }
  | { turnIndex: number; type: 'remove' };

function feedbackReducer(
  state: Map<number, FeedbackState>,
  action: FeedbackAction,
): Map<number, FeedbackState> {
  switch (action.type) {
    case 'loaded': {
      const map = new Map<number, FeedbackState>();
      for (const entry of action.entries) {
        map.set(entry.turn_index, {
          comment: entry.comment ?? undefined,
          id: entry.id,
          rating: entry.rating,
        });
      }
      return map;
    }
    case 'remove': {
      const next = new Map(state);
      next.delete(action.turnIndex);
      return next;
    }
    case 'reset': {
      return new Map();
    }
    case 'set': {
      const next = new Map(state);
      next.set(action.turnIndex, action.entry);
      return next;
    }
  }
}

export function useFeedback(threadId: null | string): UseFeedbackReturn {
  const [feedbackMap, dispatch] = useReducer(feedbackReducer, new Map<number, FeedbackState>());

  useEffect(() => {
    dispatch({ type: 'reset' });

    if (!threadId) {
      return;
    }

    let cancelled = false;

    async function load() {
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/feedback?thread_id=${encodeURIComponent(threadId!)}`,
          { headers: { 'X-Session-Id': getSessionId() } },
        );
        if (!response.ok || cancelled) {
          return;
        }
        const data: Array<BackendFeedback> = await response.json();
        if (!cancelled) {
          dispatch({ entries: data, type: 'loaded' });
        }
      } catch {
        // Silently ignore — feedback is non-critical
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [threadId]);

  const submitFeedback = useCallback(
    (turnIndex: number, rating: 'down' | 'up', comment?: string) => {
      if (!threadId) {
        return;
      }

      // Optimistic update
      dispatch({ entry: { comment, id: -1, rating }, turnIndex, type: 'set' });

      (async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/feedback`, {
            body: JSON.stringify({
              comment: comment ?? null,
              rating,
              thread_id: threadId,
              turn_index: turnIndex,
            }),
            headers: {
              'Content-Type': 'application/json',
              'X-Session-Id': getSessionId(),
            },
            method: 'POST',
          });
          if (response.ok) {
            const data: BackendFeedback = await response.json();
            dispatch({
              entry: { comment: data.comment ?? undefined, id: data.id, rating: data.rating },
              turnIndex,
              type: 'set',
            });
          }
        } catch {
          // Revert on failure
          dispatch({ turnIndex, type: 'remove' });
        }
      })();
    },
    [threadId],
  );

  const updateFeedback = useCallback(
    (feedbackId: number, turnIndex: number, rating: 'down' | 'up', comment?: string) => {
      if (!threadId) {
        return;
      }

      // Optimistic update
      dispatch({ entry: { comment, id: feedbackId, rating }, turnIndex, type: 'set' });

      (async () => {
        try {
          const response = await fetch(`${API_BASE_URL}/api/feedback/${feedbackId}`, {
            body: JSON.stringify({ comment: comment ?? null, rating }),
            headers: {
              'Content-Type': 'application/json',
              'X-Session-Id': getSessionId(),
            },
            method: 'PUT',
          });
          if (response.ok) {
            const data: BackendFeedback = await response.json();
            dispatch({
              entry: { comment: data.comment ?? undefined, id: data.id, rating: data.rating },
              turnIndex,
              type: 'set',
            });
          }
        } catch {
          // Silently ignore — feedback is non-critical
        }
      })();
    },
    [threadId],
  );

  return { feedbackMap, submitFeedback, updateFeedback };
}
