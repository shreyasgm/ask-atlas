import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, PipelineStep } from '@/types/chat';

// Mock the hook
const mockSendMessage = vi.fn();
const mockClearChat = vi.fn();

let mockHookReturn: {
  clearChat: typeof mockClearChat;
  error: null | string;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  pipelineSteps: Array<PipelineStep>;
  sendMessage: typeof mockSendMessage;
  threadId: null | string;
};

vi.mock('@/hooks/use-chat-stream', () => ({
  useChatStream: () => mockHookReturn,
}));

// Must import AFTER vi.mock
const { default: ChatPage } = await import('./chat');

function renderChat(path = '/chat') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ChatPage />} path="/chat" />
        <Route element={<ChatPage />} path="/chat/:threadId" />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // jsdom doesn't implement scrollIntoView
  Element.prototype.scrollIntoView = vi.fn();
  mockSendMessage.mockReset();
  mockClearChat.mockReset();
  mockHookReturn = {
    clearChat: mockClearChat,
    error: null,
    isStreaming: false,
    messages: [],
    pipelineSteps: [],
    sendMessage: mockSendMessage,
    threadId: null,
  };
});

describe('ChatPage - empty state', () => {
  it('renders welcome message when no messages', () => {
    renderChat();
    expect(screen.getByText(/ask me anything about trade data/i)).toBeInTheDocument();
  });

  it('renders input bar with correct placeholder', () => {
    renderChat();
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeInTheDocument();
  });

  it('renders send button with correct aria-label', () => {
    renderChat();
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
  });
});

describe('ChatPage - header', () => {
  it('header logo links to /', () => {
    renderChat();
    const logo = screen.getByRole('link', { name: /ask atlas/i });
    expect(logo).toHaveAttribute('href', '/');
  });

  it('header has "New Chat" button', () => {
    renderChat();
    expect(screen.getByRole('link', { name: /new chat/i })).toBeInTheDocument();
  });
});

describe('ChatPage - messages', () => {
  it('renders user message', () => {
    mockHookReturn.messages = [
      {
        content: 'What are the top exports of Brazil?',
        id: '1',
        isStreaming: false,
        role: 'user',
        toolCalls: [],
        toolOutputs: [],
      },
    ];
    renderChat();
    const allMatches = screen.getAllByText('What are the top exports of Brazil?');
    // User message bubble + top bar title
    expect(allMatches.length).toBeGreaterThanOrEqual(1);
  });

  it('renders assistant message with content', () => {
    mockHookReturn.messages = [
      {
        content: 'Top exports of Brazil include soybeans.',
        id: '2',
        isStreaming: false,
        role: 'assistant',
        toolCalls: [],
        toolOutputs: [],
      },
    ];
    renderChat();
    expect(screen.getByText(/soybeans/)).toBeInTheDocument();
  });

  it('renders tool output as preformatted text', () => {
    mockHookReturn.messages = [
      {
        content: 'Here are the results.',
        id: '2',
        isStreaming: false,
        role: 'assistant',
        toolCalls: [],
        toolOutputs: [{ content: 'product | value\nsoybeans | 100B', name: 'execute_sql' }],
      },
    ];
    renderChat();
    expect(screen.getByText(/product \| value/)).toBeInTheDocument();
  });

  it('renders SQL block that is collapsible', () => {
    mockHookReturn.messages = [
      {
        content: 'Results below.',
        id: '2',
        isStreaming: false,
        role: 'assistant',
        toolCalls: [{ content: 'SELECT * FROM hs92_trade', name: 'execute_sql' }],
        toolOutputs: [],
      },
    ];
    renderChat();
    expect(screen.getByText(/sql query/i)).toBeInTheDocument();
  });
});

describe('ChatPage - streaming', () => {
  it('disables input while streaming', () => {
    mockHookReturn.isStreaming = true;
    renderChat();
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeDisabled();
  });

  it('shows pipeline stepper during streaming', () => {
    mockHookReturn.isStreaming = true;
    mockHookReturn.pipelineSteps = [
      { label: 'Generating SQL query', node: 'generate_sql', status: 'active' },
    ];
    renderChat();
    expect(screen.getByText(/generating sql query/i)).toBeInTheDocument();
  });
});

describe('ChatPage - interactions', () => {
  it('calls sendMessage on form submit', async () => {
    const user = userEvent.setup();
    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'coffee exports');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(mockSendMessage).toHaveBeenCalledWith('coffee exports');
  });

  it('calls sendMessage on Enter key', async () => {
    const user = userEvent.setup();
    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'coffee exports{Enter}');

    expect(mockSendMessage).toHaveBeenCalledWith('coffee exports');
  });

  it('suggestion pills appear on completed assistant messages', () => {
    mockHookReturn.messages = [
      {
        content: 'Here are the results.',
        id: '2',
        isStreaming: false,
        role: 'assistant',
        toolCalls: [],
        toolOutputs: [],
      },
    ];
    renderChat();
    expect(screen.getByText('Break down by partner')).toBeInTheDocument();
    expect(screen.getByText('Show time series')).toBeInTheDocument();
    expect(screen.getByText('View complexity metrics')).toBeInTheDocument();
  });

  it('clicking suggestion pill calls sendMessage', async () => {
    const user = userEvent.setup();
    mockHookReturn.messages = [
      {
        content: 'Here are the results.',
        id: '2',
        isStreaming: false,
        role: 'assistant',
        toolCalls: [],
        toolOutputs: [],
      },
    ];
    renderChat();

    await user.click(screen.getByText('Break down by partner'));
    expect(mockSendMessage).toHaveBeenCalledWith('Break down by partner');
  });

  it('clear button calls clearChat', async () => {
    const user = userEvent.setup();
    mockHookReturn.messages = [
      {
        content: 'test',
        id: '1',
        isStreaming: false,
        role: 'user',
        toolCalls: [],
        toolOutputs: [],
      },
    ];
    renderChat();

    const clearButton = screen.getByRole('button', { name: /clear/i });
    await user.click(clearButton);
    expect(mockClearChat).toHaveBeenCalled();
  });
});

describe('ChatPage - routing', () => {
  it('renders at /chat route', () => {
    renderChat('/chat');
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeInTheDocument();
  });

  it('renders at /chat/:threadId route', () => {
    renderChat('/chat/some-thread-id');
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeInTheDocument();
  });
});
