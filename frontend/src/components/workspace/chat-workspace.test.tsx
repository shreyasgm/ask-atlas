import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatWorkspace from './chat-workspace';

const DEFAULT_PROPS = {
  activeThreadId: null,
  conversations: [],
  conversationsLoading: false,
  entitiesData: null,
  error: null,
  isRestoredThread: false,
  isStreaming: false,
  messages: [],
  onClear: vi.fn(),
  onDeleteConversation: vi.fn(),
  onModeChange: vi.fn(),
  onSchemaChange: vi.fn(),
  onSend: vi.fn(),
  overrides: { direction: null, mode: null, schema: null },
  pipelineSteps: [],
  queryStats: null,
};

function renderWorkspace(props = {}) {
  return render(
    <MemoryRouter>
      <ChatWorkspace {...DEFAULT_PROPS} {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

describe('ChatWorkspace', () => {
  it('renders left sidebar with collapse button', () => {
    renderWorkspace();
    expect(screen.getByRole('button', { name: /collapse sidebar/i })).toBeInTheDocument();
  });

  it('does not render right panel tabs', () => {
    renderWorkspace();
    expect(screen.queryByRole('tab', { name: 'Activity' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Entities' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Queries' })).not.toBeInTheDocument();
  });

  it('renders center panel with chat input', () => {
    renderWorkspace();
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeInTheDocument();
  });

  it('collapsing left sidebar shows icon strip', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: /collapse sidebar/i }));

    // Should now show expand button and new chat icon
    expect(screen.getByRole('button', { name: /expand sidebar/i })).toBeInTheDocument();
  });

  it('welcome message visible when no messages', () => {
    renderWorkspace();
    expect(screen.getByText(/ask me anything about trade data/i)).toBeInTheDocument();
  });

  it('renders sidebar toggle button in center panel for mobile', () => {
    renderWorkspace();
    expect(screen.getByRole('button', { name: /toggle sidebar/i })).toBeInTheDocument();
  });
});
