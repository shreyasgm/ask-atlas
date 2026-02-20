interface ChatTopBarProps {
  onClear: () => void;
  title: string;
}

export default function ChatTopBar({ onClear, title }: ChatTopBarProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2">
      <h2 className="truncate text-sm font-medium">{title}</h2>
      <button
        className="shrink-0 text-sm text-muted-foreground hover:text-foreground"
        onClick={onClear}
        type="button"
      >
        Clear
      </button>
    </div>
  );
}
