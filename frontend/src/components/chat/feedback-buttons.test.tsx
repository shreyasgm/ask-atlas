import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import FeedbackButtons from './feedback-buttons';

describe('FeedbackButtons', () => {
  it('renders both thumb buttons', () => {
    render(<FeedbackButtons onSubmit={vi.fn()} onUpdate={vi.fn()} />);
    expect(screen.getByLabelText('Thumbs up')).toBeDefined();
    expect(screen.getByLabelText('Thumbs down')).toBeDefined();
  });

  it('calls onSubmit with up when thumbs up is clicked', () => {
    const onSubmit = vi.fn();
    render(<FeedbackButtons onSubmit={onSubmit} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('Thumbs up'));
    expect(onSubmit).toHaveBeenCalledWith('up');
  });

  it('shows comment input when thumbs down is clicked', () => {
    render(<FeedbackButtons onSubmit={vi.fn()} onUpdate={vi.fn()} />);
    fireEvent.click(screen.getByLabelText('Thumbs down'));
    expect(screen.getByPlaceholderText('What was wrong or expected? (optional)')).toBeDefined();
  });

  it('submits downvote with comment on Send click', () => {
    const onSubmit = vi.fn();
    render(<FeedbackButtons onSubmit={onSubmit} onUpdate={vi.fn()} />);

    // First click opens input
    fireEvent.click(screen.getByLabelText('Thumbs down'));

    const input = screen.getByPlaceholderText('What was wrong or expected? (optional)');
    fireEvent.change(input, { target: { value: 'wrong data' } });
    fireEvent.click(screen.getByText('Send'));

    expect(onSubmit).toHaveBeenCalledWith('down', 'wrong data');
  });

  it('calls onUpdate when changing vote from up to down', () => {
    const onUpdate = vi.fn();
    render(
      <FeedbackButtons feedback={{ id: 1, rating: 'up' }} onSubmit={vi.fn()} onUpdate={onUpdate} />,
    );

    // Click thumbs down to open comment input
    fireEvent.click(screen.getByLabelText('Thumbs down'));
    // Click Send to submit
    fireEvent.click(screen.getByText('Send'));

    expect(onUpdate).toHaveBeenCalledWith(1, 'down', undefined);
  });

  it('calls onUpdate when changing from down to up', () => {
    const onUpdate = vi.fn();
    render(
      <FeedbackButtons
        feedback={{ id: 1, rating: 'down' }}
        onSubmit={vi.fn()}
        onUpdate={onUpdate}
      />,
    );

    fireEvent.click(screen.getByLabelText('Thumbs up'));
    expect(onUpdate).toHaveBeenCalledWith(1, 'up');
  });
});
