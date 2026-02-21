import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatWorkspace from './chat-workspace';

const DEFAULT_PROPS = {
  entitiesData: null,
  error: null,
  isStreaming: false,
  messages: [],
  onClear: vi.fn(),
  onSend: vi.fn(),
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

  it('renders right panel with tab buttons', () => {
    renderWorkspace();
    expect(screen.getByRole('tab', { name: 'Activity' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Entities' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Queries' })).toBeInTheDocument();
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

  it('collapsing right panel shows icon strip', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: /collapse panel/i }));

    expect(screen.getByRole('button', { name: /expand panel/i })).toBeInTheDocument();
  });

  it('welcome message visible when no messages', () => {
    renderWorkspace();
    expect(screen.getByText(/ask me anything about trade data/i)).toBeInTheDocument();
  });
});
