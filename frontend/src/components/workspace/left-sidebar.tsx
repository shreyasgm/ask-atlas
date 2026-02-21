import { ChevronLeft, Globe, Menu, MessageSquare, Plus, Search } from 'lucide-react';
import { Link } from 'react-router';

interface LeftSidebarProps {
  expanded: boolean;
  onNewChat: () => void;
  onToggle: () => void;
}

export default function LeftSidebar({ expanded, onNewChat, onToggle }: LeftSidebarProps) {
  if (!expanded) {
    return (
      <div className="flex h-full w-12 shrink-0 flex-col items-center gap-3 border-r border-border bg-background py-3">
        <button
          aria-label="Expand sidebar"
          className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <Menu className="h-4 w-4" />
        </button>
        <button
          aria-label="New chat"
          className="rounded bg-primary p-1.5 text-primary-foreground hover:bg-primary/90"
          onClick={onNewChat}
          type="button"
        >
          <Plus className="h-4 w-4" />
        </button>
        <button
          aria-label="Current chat"
          className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground"
          type="button"
        >
          <MessageSquare className="h-4 w-4" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full w-[220px] shrink-0 flex-col border-r border-border bg-background">
      {/* Header: Logo + collapse */}
      <div className="flex items-center justify-between px-4 py-3">
        <Link className="flex items-center gap-2" to="/">
          <Globe className="h-5 w-5 text-primary" />
          <span className="text-sm font-bold">Ask Atlas</span>
        </Link>
        <button
          aria-label="Collapse sidebar"
          className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>

      {/* New Chat button */}
      <div className="px-3 pb-2">
        <button
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          onClick={onNewChat}
          type="button"
        >
          <Plus className="h-4 w-4" />
          New Chat
        </button>
      </div>

      {/* Search */}
      <div className="px-3 pb-3">
        <div className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5">
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">Search...</span>
        </div>
      </div>

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto px-3">
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Conversations
        </p>
        <p className="py-2 text-xs text-muted-foreground">No conversations yet</p>
      </div>

      {/* Saved Queries */}
      <div className="border-t border-border px-3 py-3">
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Saved Queries
        </p>
        <p className="text-xs text-muted-foreground">No saved queries</p>
      </div>
    </div>
  );
}
