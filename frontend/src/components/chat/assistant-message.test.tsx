import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '@/types/chat';
import AssistantMessage from './assistant-message';

function makeMessage(overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    content: '',
    id: 'msg-1',
    isStreaming: false,
    queryResults: [],
    role: 'assistant',
    ...overrides,
  };
}

describe('AssistantMessage', () => {
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
