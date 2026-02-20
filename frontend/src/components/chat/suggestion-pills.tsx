import { SUGGESTION_PILLS } from '@/constants/chat-data';

interface SuggestionPillsProps {
  onSend: (text: string) => void;
}

export default function SuggestionPills({ onSend }: SuggestionPillsProps) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {SUGGESTION_PILLS.map((pill) => (
        <button
          className="rounded-full border border-primary px-3 py-1.5 text-xs text-primary transition-colors hover:bg-primary hover:text-primary-foreground"
          key={pill}
          onClick={() => onSend(pill)}
          type="button"
        >
          {pill}
        </button>
      ))}
    </div>
  );
}
