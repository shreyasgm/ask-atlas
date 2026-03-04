import { AlertCircle } from 'lucide-react';
import { memo, useMemo } from 'react';
import type { ChatMessage, EntitiesData, PipelineStep, QueryAggregateStats } from '@/types/chat';
import { useFeedback } from '@/hooks/use-feedback';
import AssistantMessage from './assistant-message';
import FeedbackButtons from './feedback-buttons';
import PipelineStepper from './pipeline-stepper';
import QueryContextCard from './query-context-card';
import UserMessage from './user-message';
import WelcomeMessage from './welcome-message';

interface MessageListProps {
  entitiesData?: EntitiesData | null;
  error?: null | string;
  isRestoredThread?: boolean;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  pipelineSteps: Array<PipelineStep>;
  queryStats?: QueryAggregateStats | null;
  threadId?: null | string;
}

export default memo(function MessageList({
  entitiesData,
  error,
  isRestoredThread,
  isStreaming,
  messages,
  pipelineSteps,
  queryStats,
  threadId,
}: MessageListProps) {
  const lastAssistantIndex = messages.findLastIndex((m) => m.role === 'assistant');
  const { feedbackMap, submitFeedback, updateFeedback } = useFeedback(threadId ?? null);

  const turnIndexMap = useMemo(() => {
    const map = new Map<string, number>();
    let idx = 0;
    for (const msg of messages) {
      if (msg.role === 'assistant') {
        map.set(msg.id, idx);
        idx++;
      }
    }
    return map;
  }, [messages]);

  return (
    <div aria-live="polite" className="flex-1 overflow-y-auto px-4 py-4 sm:py-6" role="log">
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <WelcomeMessage />
        {messages.map((msg, index) => {
          if (msg.role === 'user') {
            return <UserMessage content={msg.content} key={msg.id} />;
          }
          return (
            <div className="flex flex-col gap-3" key={msg.id}>
              {index === lastAssistantIndex && entitiesData && (
                <QueryContextCard entitiesData={entitiesData} queryStats={queryStats ?? null} />
              )}
              {msg.pipelineSteps && msg.pipelineSteps.length > 0 && (
                <PipelineStepper steps={msg.pipelineSteps} />
              )}
              {isStreaming && index === lastAssistantIndex && pipelineSteps.length > 0 && (
                <PipelineStepper steps={pipelineSteps} />
              )}
              <AssistantMessage
                message={msg}
                pipelineStarted={
                  isStreaming && index === lastAssistantIndex && pipelineSteps.length > 0
                }
              />
              {!msg.isStreaming && !msg.interrupted && turnIndexMap.has(msg.id) && (
                <FeedbackButtons
                  feedback={feedbackMap.get(turnIndexMap.get(msg.id)!)}
                  onSubmit={(rating, comment) =>
                    submitFeedback(turnIndexMap.get(msg.id)!, rating, comment)
                  }
                  onUpdate={(id, rating, comment) =>
                    updateFeedback(id, turnIndexMap.get(msg.id)!, rating, comment)
                  }
                />
              )}
            </div>
          );
        })}
        {error && (
          <div
            className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3"
            role="alert"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <p className="line-clamp-5 text-sm text-destructive">{error}</p>
          </div>
        )}
      </div>
    </div>
  );
});
