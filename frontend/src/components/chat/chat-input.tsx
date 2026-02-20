import type { FormEvent, KeyboardEvent } from 'react';
import { ArrowUp } from 'lucide-react';
import { useRef, useState } from 'react';

interface ChatInputProps {
  disabled: boolean;
  onSend: (text: string) => void;
}

export default function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
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
        className="flex items-center gap-2 rounded-2xl border border-border px-4 py-1"
        onSubmit={handleSubmit}
      >
        <input
          className="h-11 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about trade data..."
          ref={inputRef}
          type="text"
          value={value}
        />
        <button
          aria-label="Send"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-50"
          disabled={disabled || !value.trim()}
          type="submit"
        >
          <ArrowUp className="h-4 w-4" />
        </button>
      </form>
      <p className="mt-1.5 text-center text-[10px] text-muted-foreground">
        Responses may contain inaccuracies. Verify independently.
      </p>
    </div>
  );
}
