import { Menu, MessageSquare, Plus } from 'lucide-react';

interface LeftSidebarProps {
  expanded: boolean;
  onNewChat: () => void;
  onToggle: () => void;
}

export default function LeftSidebar({ expanded, onNewChat, onToggle }: LeftSidebarProps) {
  if (!expanded) {
    return (
      <div className="flex w-12 shrink-0 flex-col items-center gap-3 border-r border-border bg-background py-3">
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
    <div className="flex w-[220px] shrink-0 flex-col border-r border-border bg-background">
      <div className="flex items-center justify-between px-3 py-3">
        <span className="text-sm font-semibold">Chats</span>
        <button
          aria-label="Collapse sidebar"
          className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <Menu className="h-4 w-4" />
        </button>
      </div>

      <div className="px-3 pb-3">
        <button
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          onClick={onNewChat}
          type="button"
        >
          <Plus className="h-4 w-4" />
          New Chat
        </button>
      </div>

      <div className="px-3">
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Conversations
        </p>
        <p className="text-xs text-muted-foreground">No conversations yet</p>
      </div>

      <div className="mt-auto px-3 pb-3">
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          Saved Queries
        </p>
        <p className="text-xs text-muted-foreground">No saved queries</p>
      </div>
    </div>
  );
}
