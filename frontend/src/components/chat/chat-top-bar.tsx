import { Globe, Menu, Plus } from 'lucide-react';
import ThemeToggle from '@/components/ui/theme-toggle';

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
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground lg:hidden"
        onClick={onToggleSidebar}
        type="button"
      >
        <Menu className="h-5 w-5" />
      </button>
      <div className="flex items-center gap-2 lg:hidden">
        <Globe className="h-5 w-5 text-primary" />
        <span className="text-sm font-bold text-foreground">Ask Atlas</span>
      </div>
      {/* Theme toggle + new chat on mobile */}
      <div className="flex items-center gap-1 lg:hidden">
        <ThemeToggle />
        <button
          aria-label="New chat"
          className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground"
          onClick={onClear}
          type="button"
        >
          <Plus className="h-5 w-5" />
        </button>
      </div>

      {/* Desktop: title + actions */}
      <h2 className="hidden truncate text-sm font-medium lg:block">{title}</h2>
      <div className="hidden items-center gap-1 lg:flex">
        <ThemeToggle />
        <button
          className="shrink-0 rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
          onClick={onClear}
          type="button"
        >
          Clear
        </button>
      </div>
    </div>
  );
}
