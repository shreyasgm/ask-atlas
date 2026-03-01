import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  ChatMessage,
  EntitiesData,
  PipelineStep,
  QueryAggregateStats,
  TradeOverrides,
} from '@/types/chat';

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    atlasLinks: [],
    content: '',
    docsConsulted: [],
    graphqlSummaries: [],
    id: 'test',
    isStreaming: false,
    queryResults: [],
    role: 'user',
    ...overrides,
  };
}

// Mock the hooks
const mockSendMessage = vi.fn();
const mockClearChat = vi.fn();
const mockDeleteConversation = vi.fn();
const mockRefresh = vi.fn();
const mockResetAll = vi.fn();
const mockSetMode = vi.fn();
const mockSetOverrides = vi.fn();
const mockSetSchema = vi.fn();
const mockSetSystemMode = vi.fn();

let mockHookReturn: {
  clearChat: typeof mockClearChat;
  entitiesData: EntitiesData | null;
  error: null | string;
  isRestoredThread: boolean;
  isStreaming: boolean;
  messages: Array<ChatMessage>;
  pipelineSteps: Array<PipelineStep>;
  queryStats: QueryAggregateStats | null;
  sendMessage: typeof mockSendMessage;
  threadId: null | string;
};

let mockToggleReturn: {
  overrides: TradeOverrides;
  resetAll: typeof mockResetAll;
  setMode: typeof mockSetMode;
  setOverrides: typeof mockSetOverrides;
  setSchema: typeof mockSetSchema;
  setSystemMode: typeof mockSetSystemMode;
};

vi.mock('@/hooks/use-chat-stream', () => ({
  useChatStream: () => mockHookReturn,
}));

vi.mock('@/hooks/use-trade-toggles', () => ({
  useTradeToggles: () => mockToggleReturn,
}));

vi.mock('@/hooks/use-conversations', () => ({
  useConversations: () => ({
    conversations: [],
    deleteConversation: mockDeleteConversation,
    isLoading: false,
    refresh: mockRefresh,
  }),
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
  mockDeleteConversation.mockReset();
  mockRefresh.mockReset();
  mockResetAll.mockReset();
  mockSetMode.mockReset();
  mockSetOverrides.mockReset();
  mockSetSchema.mockReset();
  mockSetSystemMode.mockReset();
  mockHookReturn = {
    clearChat: mockClearChat,
    entitiesData: null,
    error: null,
    isRestoredThread: false,
    isStreaming: false,
    messages: [],
    pipelineSteps: [],
    queryStats: null,
    sendMessage: mockSendMessage,
    threadId: null,
  };
  mockToggleReturn = {
    overrides: { direction: null, mode: null, schema: null, systemMode: null },
    resetAll: mockResetAll,
    setMode: mockSetMode,
    setOverrides: mockSetOverrides,
    setSchema: mockSetSchema,
    setSystemMode: mockSetSystemMode,
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

describe('ChatPage - sidebar branding', () => {
  it('sidebar logo links to /', () => {
    renderChat();
    const logo = screen.getByRole('link', { name: /ask atlas/i });
    expect(logo).toHaveAttribute('href', '/');
  });

  it('sidebar has "New Chat" button', () => {
    renderChat();
    const buttons = screen.getAllByRole('button', { name: /new chat/i });
    expect(buttons.length).toBeGreaterThanOrEqual(1);
  });
});

describe('ChatPage - messages', () => {
  it('renders user message', () => {
    mockHookReturn.messages = [
      msg({ content: 'What are the top exports of Brazil?', id: '1', role: 'user' }),
    ];
    renderChat();
    const allMatches = screen.getAllByText('What are the top exports of Brazil?');
    // User message bubble + top bar title
    expect(allMatches.length).toBeGreaterThanOrEqual(1);
  });

  it('renders assistant message with content', () => {
    mockHookReturn.messages = [
      msg({ content: 'Top exports of Brazil include soybeans.', id: '2', role: 'assistant' }),
    ];
    renderChat();
    expect(screen.getByText(/soybeans/)).toBeInTheDocument();
  });

  it('renders markdown in assistant message', () => {
    mockHookReturn.messages = [
      msg({ content: 'Results include **soybeans** and *iron ore*.', id: '2', role: 'assistant' }),
    ];
    renderChat();
    const strong = screen.getByText('soybeans');
    expect(strong.tagName).toBe('STRONG');
  });

  it('renders SQL block from queryResults', () => {
    mockHookReturn.messages = [
      msg({
        content: 'Results below.',
        id: '2',
        queryResults: [
          {
            columns: ['product', 'value'],
            executionTimeMs: 42,
            rowCount: 2,
            rows: [
              ['soybeans', 100],
              ['iron ore', 80],
            ],
            sql: 'SELECT * FROM hs92_trade',
          },
        ],
        role: 'assistant',
      }),
    ];
    renderChat();
    expect(screen.getByText(/sql query/i)).toBeInTheDocument();
  });

  it('renders query result table with data after expanding SQL block', async () => {
    const user = userEvent.setup();
    mockHookReturn.messages = [
      msg({
        content: 'Results below.',
        id: '2',
        queryResults: [
          {
            columns: ['product', 'value'],
            executionTimeMs: 42,
            rowCount: 2,
            rows: [
              ['soybeans', 100],
              ['iron ore', 80],
            ],
            sql: 'SELECT * FROM hs92_trade',
          },
        ],
        role: 'assistant',
      }),
    ];
    renderChat();

    // Table hidden until SQL block expanded
    expect(screen.queryByText('soybeans')).not.toBeInTheDocument();
    await user.click(screen.getByText('SQL Query'));

    expect(screen.getByText('product')).toBeInTheDocument();
    expect(screen.getByText('soybeans')).toBeInTheDocument();
    expect(screen.getByText('2 rows in 42ms')).toBeInTheDocument();
  });

  it('renders source attribution when queryResults present', () => {
    mockHookReturn.messages = [
      msg({
        content: 'Results below.',
        id: '2',
        queryResults: [{ columns: [], executionTimeMs: 0, rowCount: 0, rows: [], sql: 'SELECT 1' }],
        role: 'assistant',
      }),
    ];
    renderChat();
    expect(screen.getByText(/source: atlas of economic complexity/i)).toBeInTheDocument();
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
    mockHookReturn.messages = [
      msg({ content: '', id: 'a1', isStreaming: true, role: 'assistant' }),
    ];
    mockHookReturn.pipelineSteps = [
      {
        label: 'Generating SQL query',
        node: 'generate_sql',
        pipelineType: 'sql',
        startedAt: Date.now(),
        status: 'active',
      },
    ];
    renderChat();
    const matches = screen.getAllByText(/generating sql query/i);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });
});

describe('ChatPage - interactions', () => {
  it('calls sendMessage with overrides on form submit', async () => {
    const user = userEvent.setup();
    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'coffee exports');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(mockSendMessage).toHaveBeenCalledWith('coffee exports', {
      direction: null,
      mode: null,
      schema: null,
      systemMode: null,
    });
  });

  it('calls sendMessage on Enter key', async () => {
    const user = userEvent.setup();
    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'coffee exports{Enter}');

    expect(mockSendMessage).toHaveBeenCalledWith('coffee exports', {
      direction: null,
      mode: null,
      schema: null,
      systemMode: null,
    });
  });

  it('clear button calls clearChat', async () => {
    const user = userEvent.setup();
    mockHookReturn.messages = [msg({ content: 'test', id: '1', role: 'user' })];
    renderChat();

    const clearButton = screen.getByRole('button', { name: /clear/i });
    await user.click(clearButton);
    expect(mockClearChat).toHaveBeenCalled();
  });
});

describe('ChatPage - trade toggles', () => {
  it('renders trade toggles bar', () => {
    renderChat();
    expect(screen.getByRole('toolbar', { name: /trade query constraints/i })).toBeInTheDocument();
  });

  it('clearChat resets toggles', async () => {
    const user = userEvent.setup();
    mockHookReturn.messages = [msg({ content: 'test', id: '1', role: 'user' })];
    renderChat();

    const clearButton = screen.getByRole('button', { name: /clear/i });
    await user.click(clearButton);
    expect(mockClearChat).toHaveBeenCalled();
    expect(mockResetAll).toHaveBeenCalled();
  });
});

describe('ChatPage - error display', () => {
  it('renders error with alert role inside message area', () => {
    mockHookReturn.error = 'Server error: 500 Internal Server Error';
    renderChat();
    const alert = screen.getByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(alert).toHaveTextContent('Server error: 500 Internal Server Error');
  });

  it('does not render error when error is null', () => {
    mockHookReturn.error = null;
    renderChat();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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
