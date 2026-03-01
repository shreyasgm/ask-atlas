import { ChevronLeft, Globe, Menu, MessageSquare, Plus, Search, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import type { ConversationSummary } from '@/types/chat';
import { cn } from '@/lib/utils';

interface LeftSidebarProps {
  activeThreadId: string | null;
  conversations: Array<ConversationSummary>;
  expanded: boolean;
  isLoading: boolean;
  onDeleteConversation: (threadId: string) => void;
  onNewChat: () => void;
  onSelectConversation: (threadId: string) => void;
  onToggle: () => void;
}

function formatRelativeTime(isoDate: string): string {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  if (Number.isNaN(then)) {
    return '';
  }
  const diffMs = now - then;
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHr / 24);

  if (diffMin < 1) {
    return 'just now';
  }
  if (diffMin < 60) {
    return `${diffMin}m ago`;
  }
  if (diffHr < 24) {
    return `${diffHr}h ago`;
  }
  if (diffDay < 7) {
    return `${diffDay}d ago`;
  }
  return new Date(isoDate).toLocaleDateString();
}

export default function LeftSidebar({
  activeThreadId,
  conversations,
  expanded,
  isLoading,
  onDeleteConversation,
  onNewChat,
  onSelectConversation,
  onToggle,
}: LeftSidebarProps) {
  const [searchQuery, setSearchQuery] = useState('');

  const filtered = useMemo(
    () =>
      searchQuery
        ? conversations.filter((c) =>
            (c.title ?? '').toLowerCase().includes(searchQuery.toLowerCase()),
          )
        : conversations,
    [conversations, searchQuery],
  );

  if (!expanded) {
    return (
      <div className="hidden h-full w-12 shrink-0 flex-col items-center gap-3 border-r border-border bg-background py-3 lg:flex">
        <button
          aria-label="Expand sidebar"
          className="rounded p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <Menu className="h-4 w-4" />
        </button>
        <button
          aria-label="New chat"
          className="rounded bg-primary p-1.5 text-primary-foreground transition-colors hover:bg-primary/90"
          onClick={onNewChat}
          type="button"
        >
          <Plus className="h-4 w-4" />
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
          className="rounded p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          onClick={onToggle}
          type="button"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>

      {/* New Chat button */}
      <div className="px-3 pb-2">
        <button
          className="flex w-full items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
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
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            aria-label="Search conversations"
            className="w-full bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search..."
            type="text"
            value={searchQuery}
          />
        </div>
      </div>

      {/* Conversations */}
      <div className="flex-1 overflow-y-auto px-3">
        <p className="mb-2 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
          History
        </p>

        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div
                className="h-10 animate-pulse rounded-md bg-muted"
                data-testid="conversation-skeleton"
                key={i}
              />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <p className="py-2 text-xs text-muted-foreground">
            {searchQuery ? 'No matches' : 'No conversations yet'}
          </p>
        ) : (
          <div className="space-y-0.5">
            {filtered.map((conversation) => (
              <ConversationItem
                active={conversation.threadId === activeThreadId}
                conversation={conversation}
                key={conversation.threadId}
                onDelete={onDeleteConversation}
                onSelect={onSelectConversation}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ConversationItem({
  active,
  conversation,
  onDelete,
  onSelect,
}: {
  active: boolean;
  conversation: ConversationSummary;
  onDelete: (threadId: string) => void;
  onSelect: (threadId: string) => void;
}) {
  return (
    <div
      className={cn(
        'group relative rounded-md transition-colors',
        active ? 'bg-secondary' : 'hover:bg-secondary/50',
      )}
      data-testid="conversation-item"
    >
      <Link
        className="flex items-start gap-2 rounded-md px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        onClick={() => onSelect(conversation.threadId)}
        to={`/chat/${conversation.threadId}`}
      >
        <MessageSquare
          className={cn(
            'mt-0.5 h-3.5 w-3.5 shrink-0',
            active ? 'text-blue-500' : 'text-muted-foreground',
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-foreground">
            {conversation.title ?? 'Untitled conversation'}
          </p>
          <p className="text-[10px] text-muted-foreground">
            {formatRelativeTime(conversation.updatedAt)}
          </p>
        </div>
      </Link>
      <button
        aria-label="Delete conversation"
        className="absolute top-1.5 right-1.5 hidden rounded p-0.5 text-muted-foreground group-hover:block hover:bg-destructive/10 hover:text-destructive focus-visible:block focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        onClick={(e) => {
          e.stopPropagation();
          if (window.confirm('Delete this conversation?')) {
            onDelete(conversation.threadId);
          }
        }}
        type="button"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}
