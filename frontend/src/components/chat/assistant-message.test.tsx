import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
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
    render(<AssistantMessage isLast={false} message={msg} onSend={vi.fn()} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('Product')).toBeInTheDocument();
    expect(screen.getByText('Coffee')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
  });

  it('renders basic markdown (bold, lists) correctly', () => {
    const msg = makeMessage({
      content: '**bold text** and a list:\n\n- item one\n- item two',
    });
    render(<AssistantMessage isLast={false} message={msg} onSend={vi.fn()} />);
    expect(screen.getByText('bold text')).toBeInTheDocument();
    expect(screen.getByText('item one')).toBeInTheDocument();
  });
});
