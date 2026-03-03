import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import ChatInput from './chat-input';

const defaultProps = {
  disabled: false,
  isStreaming: false,
  onSend: vi.fn(),
  onStop: vi.fn(),
};

describe('ChatInput', () => {
  it('renders send button when not streaming', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /stop generating/i })).not.toBeInTheDocument();
  });

  it('renders stop button when streaming', () => {
    render(<ChatInput {...defaultProps} isStreaming />);
    expect(screen.getByRole('button', { name: /stop generating/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument();
  });

  it('calls onStop when stop button is clicked', async () => {
    const onStop = vi.fn();
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} isStreaming onStop={onStop} />);
    await user.click(screen.getByRole('button', { name: /stop generating/i }));
    expect(onStop).toHaveBeenCalledOnce();
  });

  it('disables input when disabled and not streaming', () => {
    render(<ChatInput {...defaultProps} disabled />);
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeDisabled();
  });

  it('disables input when streaming', () => {
    render(<ChatInput {...defaultProps} isStreaming />);
    expect(screen.getByPlaceholderText(/generating response/i)).toBeDisabled();
  });

  it('shows default placeholder when not streaming', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByPlaceholderText(/ask about trade data/i)).toBeInTheDocument();
  });

  it('shows generating placeholder when streaming', () => {
    render(<ChatInput {...defaultProps} isStreaming />);
    expect(screen.getByPlaceholderText(/generating response/i)).toBeInTheDocument();
  });

  it('calls onSend and clears input on submit', async () => {
    const onSend = vi.fn();
    const user = userEvent.setup();
    render(<ChatInput {...defaultProps} onSend={onSend} />);

    const input = screen.getByPlaceholderText(/ask about trade data/i);
    await user.type(input, 'coffee exports');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(onSend).toHaveBeenCalledWith('coffee exports');
  });

  it('send button is disabled when input is empty', () => {
    render(<ChatInput {...defaultProps} />);
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
  });
});
