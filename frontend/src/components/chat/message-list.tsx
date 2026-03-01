import { AlertCircle } from 'lucide-react';
import type { ChatMessage, EntitiesData, PipelineStep, QueryAggregateStats } from '@/types/chat';
import AssistantMessage from './assistant-message';
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
}

export default function MessageList({
  entitiesData,
  error,
  isRestoredThread,
  isStreaming,
  messages,
  pipelineSteps,
  queryStats,
}: MessageListProps) {
  const lastAssistantIndex = messages.findLastIndex((m) => m.role === 'assistant');

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4 sm:py-6">
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
              <AssistantMessage
                message={msg}
                pipelineStarted={
                  isStreaming && index === lastAssistantIndex && pipelineSteps.length > 0
                }
              />
            </div>
          );
        })}
        {isStreaming && pipelineSteps.length > 0 && <PipelineStepper steps={pipelineSteps} />}
        {error && (
          <div
            className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3"
            role="alert"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}
        <div />
      </div>
    </div>
  );
}
