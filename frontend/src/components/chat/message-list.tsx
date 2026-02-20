import { useEffect, useRef } from 'react';
import type { ChatMessage, PipelineStep } from '@/types/chat';
import AssistantMessage from './assistant-message';
import PipelineStepper from './pipeline-stepper';
import UserMessage from './user-message';
import WelcomeMessage from './welcome-message';

interface MessageListProps {
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  onSend: (text: string) => void;
  pipelineSteps: Array<PipelineStep>;
}

export default function MessageList({
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
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
