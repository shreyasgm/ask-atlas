import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '@/types/chat';
import AssistantMessage from './assistant-message';

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    atlasLinks: [],
    content: '',
    docsConsulted: [],
    graphqlSummaries: [],
    id: 'msg-1',
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
});
