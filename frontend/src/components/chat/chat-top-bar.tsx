import { Globe, Menu } from 'lucide-react';

interface ChatTopBarProps {
  onClear: () => void;
  onToggleSidebar?: () => void;
  title: string;
}

export default function ChatTopBar({ onClear, onToggleSidebar, title }: ChatTopBarProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2">
      {/* Mobile: hamburger + centered logo */}
      <button
        aria-label="Toggle sidebar"
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:text-foreground lg:hidden"
        onClick={onToggleSidebar}
        type="button"
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex items-center gap-2 lg:hidden">
        <Globe className="h-5 w-5 text-primary" />
        <span className="text-sm font-bold text-foreground">Ask Atlas</span>
      </div>
      {/* Spacer to balance hamburger on mobile */}
      <div className="w-9 lg:hidden" />

      {/* Desktop: title + clear button */}
      <h2 className="hidden truncate text-sm font-medium lg:block">{title}</h2>
      <button
        className="hidden shrink-0 text-sm text-muted-foreground hover:text-foreground lg:block"
        onClick={onClear}
        type="button"
      >
        Clear
      </button>
    </div>
  );
}
