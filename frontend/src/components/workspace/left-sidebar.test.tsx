import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ConversationSummary } from '@/types/chat';
import LeftSidebar from './left-sidebar';

const CONVERSATIONS: Array<ConversationSummary> = [
  {
    createdAt: '2025-02-20T10:00:00Z',
    threadId: 'thread-1',
    title: 'Top exports of Brazil',
    updatedAt: new Date(Date.now() - 2 * 60 * 1000).toISOString(), // 2 min ago
  },
  {
    createdAt: '2025-02-19T09:00:00Z',
    threadId: 'thread-2',
    title: 'Coffee trade patterns',
    updatedAt: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(), // 1 day ago
  },
];

const DEFAULT_PROPS = {
  activeThreadId: null as string | null,
  conversations: CONVERSATIONS,
  expanded: true,
  isLoading: false,
  onDeleteConversation: vi.fn(),
  onNewChat: vi.fn(),
  onSelectConversation: vi.fn(),
  onToggle: vi.fn(),
};

function renderSidebar(props = {}) {
  return render(
    <MemoryRouter>
      <LeftSidebar {...DEFAULT_PROPS} {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('LeftSidebar - expanded', () => {
  it('renders conversation items with titles', () => {
    renderSidebar();
    expect(screen.getByText('Top exports of Brazil')).toBeInTheDocument();
    expect(screen.getByText('Coffee trade patterns')).toBeInTheDocument();
  });

  it('renders section header', () => {
    renderSidebar();
    expect(screen.getByText('Recent conversations')).toBeInTheDocument();
  });

  it('active conversation is highlighted', () => {
    renderSidebar({ activeThreadId: 'thread-1' });
    const activeItem = screen
      .getByText('Top exports of Brazil')
      .closest('[data-testid="conversation-item"]');
    expect(activeItem).toHaveClass('bg-secondary');
  });

  it('search input filters conversations by title', async () => {
    const user = userEvent.setup();
    renderSidebar();

    const searchInput = screen.getByPlaceholderText('Search...');
    await user.type(searchInput, 'coffee');

    expect(screen.queryByText('Top exports of Brazil')).not.toBeInTheDocument();
    expect(screen.getByText('Coffee trade patterns')).toBeInTheDocument();
  });

  it('New Chat button calls onNewChat', async () => {
    const user = userEvent.setup();
    const onNewChat = vi.fn();
    renderSidebar({ onNewChat });

    const buttons = screen.getAllByRole('button', { name: /new chat/i });
    await user.click(buttons[0]);
    expect(onNewChat).toHaveBeenCalled();
  });

  it('delete button calls onDeleteConversation after confirm', async () => {
    const user = userEvent.setup();
    const onDeleteConversation = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderSidebar({ onDeleteConversation });

    const item = screen
      .getByText('Top exports of Brazil')
      .closest('[data-testid="conversation-item"]') as HTMLElement;
    const deleteBtn = within(item).getByRole('button', { name: /delete/i });
    await user.click(deleteBtn);

    expect(window.confirm).toHaveBeenCalled();
    expect(onDeleteConversation).toHaveBeenCalledWith('thread-1');
  });

  it('delete button does not call onDeleteConversation when confirm is cancelled', async () => {
    const user = userEvent.setup();
    const onDeleteConversation = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderSidebar({ onDeleteConversation });

    const item = screen
      .getByText('Top exports of Brazil')
      .closest('[data-testid="conversation-item"]') as HTMLElement;
    const deleteBtn = within(item).getByRole('button', { name: /delete/i });
    await user.click(deleteBtn);

    expect(onDeleteConversation).not.toHaveBeenCalled();
  });

  it('conversation items link to /chat/{threadId}', () => {
    renderSidebar();
    const link = screen.getByText('Top exports of Brazil').closest('a');
    expect(link).toHaveAttribute('href', '/chat/thread-1');
  });

  it('clicking a conversation calls onSelectConversation', async () => {
    const user = userEvent.setup();
    const onSelectConversation = vi.fn();
    renderSidebar({ onSelectConversation });

    await user.click(screen.getByText('Top exports of Brazil'));
    expect(onSelectConversation).toHaveBeenCalledWith('thread-1');
  });
});

describe('LeftSidebar - loading state', () => {
  it('shows skeleton placeholders when loading', () => {
    renderSidebar({ conversations: [], isLoading: true });
    const skeletons = screen.getAllByTestId('conversation-skeleton');
    expect(skeletons.length).toBe(3);
  });
});

describe('LeftSidebar - empty state', () => {
  it('shows "No conversations yet" when empty and not loading', () => {
    renderSidebar({ conversations: [], isLoading: false });
    expect(screen.getByText('No conversations yet')).toBeInTheDocument();
  });
});

describe('LeftSidebar - conversations with null title', () => {
  it('shows fallback text for null title', () => {
    renderSidebar({
      conversations: [
        {
          createdAt: '2025-02-20T10:00:00Z',
          threadId: 'thread-3',
          title: null,
          updatedAt: '2025-02-20T10:00:00Z',
        },
      ],
    });
    expect(screen.getByText('Untitled conversation')).toBeInTheDocument();
  });
});

describe('LeftSidebar - collapsed', () => {
  it('does not render conversation items when collapsed', () => {
    renderSidebar({ expanded: false });
    expect(screen.queryByText('Top exports of Brazil')).not.toBeInTheDocument();
  });

  it('shows expand and new chat buttons', () => {
    renderSidebar({ expanded: false });
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /new chat/i })).toBeInTheDocument();
  });
});
