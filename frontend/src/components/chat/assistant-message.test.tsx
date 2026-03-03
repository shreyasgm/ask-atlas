import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/types/chat';
import AssistantMessage from './assistant-message';

vi.mock('@/hooks/use-backend-ready', () => ({
  useBackendReady: vi.fn(() => false),
}));

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    atlasLinks: [],
    content: '',
    docsConsulted: [],
    graphqlSummaries: [],
    id: 'msg-1',
    interrupted: false,
    isStreaming: false,
    queryResults: [],
    role: 'assistant',
    ...overrides,
  };
}

describe('AssistantMessage', () => {
  it('renders "Ask-Atlas Assistant" label', () => {
    const msg = makeMessage({ content: 'Hello world' });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByText('Ask-Atlas Assistant')).toBeInTheDocument();
  });

  it('renders GFM markdown table as HTML table', () => {
    const msg = makeMessage({
      content: '| Product | Value |\n|---|---|\n| Coffee | 100 |',
    });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Product')).toBeInTheDocument();
    expect(screen.getByText('Coffee')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });

  it('renders basic markdown (bold, lists) correctly', () => {
    const msg = makeMessage({
      content: '**bold text** and a list:\n\n- item one\n- item two',
    });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByText('bold text')).toBeInTheDocument();
    expect(screen.getByText('item one')).toBeInTheDocument();
  });

  it('renders query results and source text flush left (no ml-4 indent)', () => {
    const msg = makeMessage({
      content: 'Some answer',
      queryResults: [
        {
          columns: ['country'],
          executionTimeMs: 10,
          rowCount: 1,
          rows: [['Brazil']],
          sql: 'SELECT country FROM trade',
        },
      ],
    });
    render(<AssistantMessage message={msg} />);

    // The SQL Query wrapper div should NOT have ml-4 class
    const sqlQueryButton = screen.getByText('SQL Query');
    const sqlWrapper = sqlQueryButton.closest('[class]')?.parentElement;
    expect(sqlWrapper?.className).not.toMatch(/\bml-4\b/);

    // The "Source:" text should NOT have ml-4 class
    const sourceText = screen.getByText(/Source: Atlas/);
    expect(sourceText.className).not.toMatch(/\bml-4\b/);
  });

  it('renders inline LaTeX math via $...$ delimiters', () => {
    const msg = makeMessage({
      content: 'The formula is $E = mc^2$ in physics.',
    });
    const { container } = render(<AssistantMessage message={msg} />);
    // rehype-katex wraps math in <span class="katex">
    const katexSpan = container.querySelector('.katex');
    expect(katexSpan).toBeInTheDocument();
  });

  it('renders display LaTeX math via $$...$$ delimiters', () => {
    const msg = makeMessage({
      content: 'Below is a formula:\n\n$$\\sum_{i=1}^{n} x_i$$',
    });
    const { container } = render(<AssistantMessage message={msg} />);
    // Display math renders katex inside a block-level container
    const katexElements = container.querySelectorAll('.katex');
    expect(katexElements.length).toBeGreaterThan(0);
  });

  it('shows loading indicator when streaming with no content and pipeline not started', () => {
    const msg = makeMessage({ isStreaming: true });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByText('Processing your question...')).toBeInTheDocument();
    expect(screen.getByText('Ask-Atlas Assistant')).toBeInTheDocument();
  });

  it('hides loading indicator when pipeline has started', () => {
    const msg = makeMessage({ isStreaming: true });
    render(<AssistantMessage message={msg} pipelineStarted />);
    expect(screen.queryByText('Processing your question...')).not.toBeInTheDocument();
  });

  it('hides loading indicator once content arrives', () => {
    const msg = makeMessage({ content: 'Here are the results', isStreaming: true });
    render(<AssistantMessage message={msg} />);
    expect(screen.queryByText('Processing your question...')).not.toBeInTheDocument();
    expect(screen.getByText('Here are the results')).toBeInTheDocument();
  });

  it('shows "Response was stopped" when message is interrupted', () => {
    const msg = makeMessage({ content: 'Partial content here', interrupted: true });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByText('Response was stopped')).toBeInTheDocument();
    expect(screen.getByText('Partial content here')).toBeInTheDocument();
  });

  it('shows "Response was stopped" when interrupted with no content', () => {
    const msg = makeMessage({ interrupted: true });
    render(<AssistantMessage message={msg} />);
    expect(screen.getByText('Response was stopped')).toBeInTheDocument();
  });

  it('does not show "Response was stopped" on normal messages', () => {
    const msg = makeMessage({ content: 'Normal response' });
    render(<AssistantMessage message={msg} />);
    expect(screen.queryByText('Response was stopped')).not.toBeInTheDocument();
  });

  it('hides query result table until SQL collapsible is expanded', async () => {
    const user = userEvent.setup();
    const msg = makeMessage({
      queryResults: [
        {
          columns: ['country', 'value'],
          executionTimeMs: 42,
          rowCount: 1,
          rows: [['Brazil', 100]],
          sql: 'SELECT country, value FROM trade',
        },
      ],
    });
    render(<AssistantMessage message={msg} />);

    // Table should not be visible before expanding
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByText('Brazil')).not.toBeInTheDocument();

    // Click the SQL Query trigger to expand
    await user.click(screen.getByText('SQL Query'));

    // Now table and row count should be visible
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Brazil')).toBeInTheDocument();
    expect(screen.getByText('1 rows in 42ms')).toBeInTheDocument();
  });

  it('shows "Processing..." instead of cold-start hint when backend is warm', async () => {
    const { useBackendReady } = await import('@/hooks/use-backend-ready');
    vi.mocked(useBackendReady).mockReturnValue(true);

    const msg = makeMessage({ isStreaming: true });
    render(<AssistantMessage message={msg} />);

    // Fast-forward past the 4-second cold-start timer
    vi.useFakeTimers();
    vi.advanceTimersByTime(5000);
    vi.useRealTimers();

    // Even after the timer fires, we should still see "Processing..." because backend is warm
    expect(screen.getByText('Processing your question...')).toBeInTheDocument();
    expect(screen.queryByText(/Starting up the backend/)).not.toBeInTheDocument();

    vi.mocked(useBackendReady).mockReturnValue(false);
  });

  it('shows cold-start hint when backend is cold and timer fires', async () => {
    const { useBackendReady } = await import('@/hooks/use-backend-ready');
    vi.mocked(useBackendReady).mockReturnValue(false);

    vi.useFakeTimers();
    const msg = makeMessage({ isStreaming: true });
    render(<AssistantMessage message={msg} />);

    // Initially shows "Processing..."
    expect(screen.getByText('Processing your question...')).toBeInTheDocument();

    // After 4 seconds, should show cold-start hint
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(screen.getByText(/Starting up the backend/)).toBeInTheDocument();
    vi.useRealTimers();
  });
});
