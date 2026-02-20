/**
 * Page-level integration tests for ChatPage.
 *
 * These render the REAL ChatPage with the REAL useChatStream hook.
 * Only `fetch` is mocked (at the network boundary).
 * react-router is provided via MemoryRouter — no mock.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createControllableStream,
  makeAgentTalkEvent,
  makeDoneEvent,
  makeNodeStartEvent,
  makePipelineStateEvent,
  makeThreadIdEvent,
  THREAD_ID,
} from '@/test/sse-helpers';
import ChatPage from './chat';

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
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('ChatPage integration (real hook + real components)', () => {
  it('full flow: submit → stream → display → suggestions', async () => {
    const user = userEvent.setup();
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    renderChat();

    // Type and submit
    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'What are the top exports?');
    await user.click(screen.getByRole('button', { name: /send/i }));

    // User message should appear (also shows in top bar title, so use getAllByText)
    await waitFor(() => {
      expect(screen.getAllByText('What are the top exports?').length).toBeGreaterThanOrEqual(1);
    });

    // Stream response
    pushEvent(makeThreadIdEvent());
    pushEvent(makeAgentTalkEvent('Top exports include '));

    await waitFor(() => {
      expect(screen.getByText(/top exports include/i)).toBeInTheDocument();
    });

    pushEvent(makeAgentTalkEvent('soybeans and iron ore.'));

    await waitFor(() => {
      expect(screen.getByText(/soybeans and iron ore/i)).toBeInTheDocument();
    });

    // End the stream
    pushEvent(makeDoneEvent());
    close();

    // Suggestion pills appear after streaming ends
    await waitFor(() => {
      expect(screen.getByText('Break down by partner')).toBeInTheDocument();
    });

    // Input re-enabled
    expect(screen.getByPlaceholderText(/ask about trade data/i)).not.toBeDisabled();
  });

  it('error flow: 500 response shows error and re-enables input', async () => {
    const user = userEvent.setup();
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    });

    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'hello');
    await user.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/server error: 500/i)).toBeInTheDocument();
    });

    expect(screen.getByPlaceholderText(/ask about trade data/i)).not.toBeDisabled();
  });

  it('multi-turn: threadId reused in second request', async () => {
    const user = userEvent.setup();

    // First turn
    const stream1 = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream1.stream, ok: true });

    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'first question');
    await user.click(screen.getByRole('button', { name: /send/i }));

    stream1.pushEvent(makeThreadIdEvent());
    stream1.pushEvent(makeAgentTalkEvent('first answer'));
    stream1.pushEvent(makeDoneEvent());
    stream1.close();

    await waitFor(() => {
      expect(screen.getByText(/first answer/i)).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/ask about trade data/i)).not.toBeDisabled();
    });

    // Second turn — new stream
    const stream2 = createControllableStream();
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      body: stream2.stream,
      ok: true,
    });

    const input2 = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input2, 'second question');
    await user.click(screen.getByRole('button', { name: /send/i }));

    // Verify second fetch includes thread_id
    const secondCallBody = JSON.parse(
      (global.fetch as ReturnType<typeof vi.fn>).mock.calls[1][1].body as string,
    );
    expect(secondCallBody.thread_id).toBe(THREAD_ID);

    stream2.pushEvent(makeAgentTalkEvent('second answer'));
    stream2.pushEvent(makeDoneEvent());
    stream2.close();

    await waitFor(() => {
      expect(screen.getByText(/second answer/i)).toBeInTheDocument();
    });
  });

  it('pipeline stepper shows active then completed steps', async () => {
    const user = userEvent.setup();
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'query');
    await user.click(screen.getByRole('button', { name: /send/i }));

    pushEvent(makeThreadIdEvent());
    pushEvent(makeNodeStartEvent('generate_sql', 'Generating SQL query'));

    await waitFor(() => {
      expect(screen.getByText(/generating sql query/i)).toBeInTheDocument();
    });

    pushEvent(makePipelineStateEvent('generate_sql'));
    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/ask about trade data/i)).not.toBeDisabled();
    });
  });

  it('clear button resets messages', async () => {
    const user = userEvent.setup();
    const { close, pushEvent, stream } = createControllableStream();
    global.fetch = vi.fn().mockResolvedValue({ body: stream, ok: true });

    renderChat();

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'hello');
    await user.click(screen.getByRole('button', { name: /send/i }));

    pushEvent(makeThreadIdEvent());
    pushEvent(makeAgentTalkEvent('response text'));
    pushEvent(makeDoneEvent());
    close();

    await waitFor(() => {
      expect(screen.getByText(/response text/)).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/ask about trade data/i)).not.toBeDisabled();
    });

    // Click clear
    await user.click(screen.getByText('Clear'));

    // Messages gone, welcome message back
    expect(screen.queryByText(/response text/)).not.toBeInTheDocument();
    expect(screen.getByText(/ask me anything about trade data/i)).toBeInTheDocument();
  });
});
