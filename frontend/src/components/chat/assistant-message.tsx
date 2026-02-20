import type { ChatMessage } from '@/types/chat';
import SqlBlock from './sql-block';
import SuggestionPills from './suggestion-pills';

interface AssistantMessageProps {
  isLast: boolean;
  message: ChatMessage;
  onSend: (text: string) => void;
}

export default function AssistantMessage({ isLast, message, onSend }: AssistantMessageProps) {
  return (
    <div className="flex flex-col gap-2">
      {message.content && (
        <div className="flex items-start gap-2">
          <div className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-green-500" />
          <p className="text-sm font-medium">{message.content}</p>
        </div>
      )}

      {message.toolCalls.map((tc, i) => (
        <div className="ml-4" key={`tc-${i}`}>
          <SqlBlock sql={tc.content} />
        </div>
      ))}

      {message.toolOutputs.map((to, i) => (
        <div className="ml-4" key={`to-${i}`}>
          <pre className="overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs">
            {to.content}
          </pre>
        </div>
      ))}

      {(message.toolCalls.length > 0 || message.toolOutputs.length > 0) && (
        <p className="ml-4 font-mono text-xs text-muted-foreground">
          Source: Atlas of Economic Complexity
        </p>
      )}

      {isLast && !message.isStreaming && (
        <div className="ml-4">
          <SuggestionPills onSend={onSend} />
        </div>
      )}
    </div>
  );
}
