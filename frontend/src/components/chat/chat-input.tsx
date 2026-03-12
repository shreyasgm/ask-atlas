import type { FormEvent, KeyboardEvent } from 'react';
import { ArrowUp, Square } from 'lucide-react';
import { useRef, useState } from 'react';

interface ChatInputProps {
  disabled: boolean;
  isStreaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export default function ChatInput({ disabled, isStreaming, onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (isStreaming) {
      onStop();
      return;
    }
    const trimmed = value.trim();
    if (!trimmed || disabled) {
      return;
    }
    onSend(trimmed);
    setValue('');
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  }

  return (
    <div className="w-full">
      <form
        className="flex items-center gap-2 rounded-2xl border border-border px-4 py-1 transition-colors focus-within:border-ring"
        onSubmit={handleSubmit}
      >
        <input
          aria-label="Ask about trade data"
          className="h-11 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground sm:text-sm"
          disabled={disabled || isStreaming}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isStreaming ? 'Generating response...' : 'Ask about trade data...'}
          ref={inputRef}
          type="text"
          value={value}
        />
        {isStreaming ? (
          <button
            aria-label="Stop generating"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-destructive text-destructive-foreground transition-opacity hover:bg-destructive/90"
            onClick={onStop}
            type="button"
          >
            <Square className="h-3.5 w-3.5" />
          </button>
        ) : (
          <button
            aria-label="Send"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-opacity hover:bg-primary/90 disabled:opacity-50"
            disabled={disabled || !value.trim()}
            type="submit"
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        )}
      </form>
      <p className="mt-1.5 text-center text-xs text-muted-foreground">
        Responses may contain inaccuracies. Verify independently.
      </p>
    </div>
  );
}
