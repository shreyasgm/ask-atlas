import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import RightPanel from './right-panel';

const DEFAULT_PROPS = {
  currentQueries: [],
  entitiesData: null,
  expanded: true,
  isRestoredThread: false,
  isStreaming: false,
  onToggle: vi.fn(),
  pipelineSteps: [],
  queryStats: null,
};

describe('RightPanel', () => {
  it('renders three tab buttons when expanded', () => {
    render(<RightPanel {...DEFAULT_PROPS} />);
    expect(screen.getByRole('tab', { name: 'Entities' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Activity' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Queries' })).toBeInTheDocument();
  });

  it('defaults to Activity tab', () => {
    render(<RightPanel {...DEFAULT_PROPS} />);
    const activityTab = screen.getByRole('tab', { name: 'Activity' });
    expect(activityTab).toHaveAttribute('aria-selected', 'true');
    // Activity tab empty state should be visible
    expect(screen.getByText(/no activity yet/i)).toBeInTheDocument();
  });

  it('switches to Entities tab on click', async () => {
    const user = userEvent.setup();
    render(<RightPanel {...DEFAULT_PROPS} />);

    await user.click(screen.getByRole('tab', { name: 'Entities' }));

    expect(screen.getByRole('tab', { name: 'Entities' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/no entities resolved yet/i)).toBeInTheDocument();
  });

  it('switches to Queries tab on click', async () => {
    const user = userEvent.setup();
    render(<RightPanel {...DEFAULT_PROPS} />);

    await user.click(screen.getByRole('tab', { name: 'Queries' }));

    expect(screen.getByRole('tab', { name: 'Queries' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/no queries executed yet/i)).toBeInTheDocument();
  });

  it('renders collapse button when expanded', () => {
    render(<RightPanel {...DEFAULT_PROPS} />);
    expect(screen.getByRole('button', { name: /collapse panel/i })).toBeInTheDocument();
  });

  it('calls onToggle when collapse button clicked', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<RightPanel {...DEFAULT_PROPS} onToggle={onToggle} />);

    await user.click(screen.getByRole('button', { name: /collapse panel/i }));
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it('renders collapsed icon strip when not expanded', () => {
    render(<RightPanel {...DEFAULT_PROPS} expanded={false} />);
    expect(screen.getByRole('button', { name: /expand panel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Entities' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Activity' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Queries' })).toBeInTheDocument();
  });

  it('clicking collapsed icon button calls onToggle', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(<RightPanel {...DEFAULT_PROPS} expanded={false} onToggle={onToggle} />);

    await user.click(screen.getByRole('button', { name: 'Entities' }));
    expect(onToggle).toHaveBeenCalledOnce();
  });
});
