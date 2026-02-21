import { AlertCircle } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ChatMessage, PipelineStep } from '@/types/chat';
import AssistantMessage from './assistant-message';
import PipelineStepper from './pipeline-stepper';
import UserMessage from './user-message';
import WelcomeMessage from './welcome-message';

interface MessageListProps {
  error?: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onSend: (text: string) => void;
  pipelineSteps: Array<PipelineStep>;
}

export default function MessageList({
  error,
  isStreaming,
  messages,
  onSend,
  pipelineSteps,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const isNearBottom =
      container.scrollTop + container.clientHeight >= container.scrollHeight - 100;
    if (isNearBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, pipelineSteps]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6" ref={containerRef}>
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <WelcomeMessage />
        {messages.map((msg, i) => {
          if (msg.role === 'user') {
            return <UserMessage content={msg.content} key={msg.id} />;
          }
          return (
            <AssistantMessage
              isLast={i === messages.length - 1}
              key={msg.id}
              message={msg}
              onSend={onSend}
            />
          );
        })}
        {isStreaming && pipelineSteps.length > 0 && (
          <div className="ml-4">
            <PipelineStepper steps={pipelineSteps} />
          </div>
        )}
        {error && (
          <div
            className="flex items-start gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-3"
            role="alert"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <p className="text-sm text-destructive">{error}</p>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
