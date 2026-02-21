import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PipelineStep, QueryAggregateStats } from '@/types/chat';
import ActivityTab from './activity-tab';

const now = Date.now();

function makeStep(
  overrides: Partial<PipelineStep> & { label: string; node: string },
): PipelineStep {
  return {
    startedAt: now,
    status: 'active',
    ...overrides,
  };
}

const COMPLETED_STEPS: Array<PipelineStep> = [
  makeStep({
    completedAt: now + 120,
    label: 'Extracting products',
    node: 'extract_products',
    startedAt: now,
    status: 'completed',
  }),
  makeStep({
    completedAt: now + 350,
    label: 'Generating SQL query',
    node: 'generate_sql',
    startedAt: now + 120,
    status: 'completed',
  }),
  makeStep({
    completedAt: now + 1500,
    label: 'Executing SQL',
    node: 'execute_sql',
    startedAt: now + 350,
    status: 'completed',
  }),
];

const STATS: QueryAggregateStats = {
  totalExecutionTimeMs: 150,
  totalQueries: 3,
  totalRows: 42,
  totalTimeMs: 2100,
};

describe('ActivityTab', () => {
  it('shows empty state when no steps or stats', () => {
    render(<ActivityTab isStreaming={false} pipelineSteps={[]} queryStats={null} />);
    expect(screen.getByText(/no activity yet/i)).toBeInTheDocument();
  });

  it('renders timeline entries for each pipeline step', () => {
    render(<ActivityTab isStreaming={false} pipelineSteps={COMPLETED_STEPS} queryStats={null} />);
    expect(screen.getByText('Extracting products')).toBeInTheDocument();
    expect(screen.getByText('Generating SQL query')).toBeInTheDocument();
    expect(screen.getByText('Executing SQL')).toBeInTheDocument();
  });

  it('displays timing for completed steps', () => {
    render(<ActivityTab isStreaming={false} pipelineSteps={COMPLETED_STEPS} queryStats={null} />);
    // 120ms, 230ms, 1150ms → 1.1s or 1.2s (floating point)
    expect(screen.getByText('120ms')).toBeInTheDocument();
    expect(screen.getByText('230ms')).toBeInTheDocument();
    // 1150ms formats to 1.1s or 1.2s depending on floating point rounding
    expect(screen.getByText(/1\.[12]s/)).toBeInTheDocument();
  });

  it('shows "In progress" badge while streaming', () => {
    render(
      <ActivityTab
        isStreaming={true}
        pipelineSteps={[makeStep({ label: 'Generating SQL', node: 'generate_sql' })]}
        queryStats={null}
      />,
    );
    expect(screen.getByText('In progress')).toBeInTheDocument();
  });

  it('shows "Complete" badge when done with stats', () => {
    render(<ActivityTab isStreaming={false} pipelineSteps={COMPLETED_STEPS} queryStats={STATS} />);
    expect(screen.getByText('Complete')).toBeInTheDocument();
  });

  it('renders "Response delivered" with aggregate stats', () => {
    render(<ActivityTab isStreaming={false} pipelineSteps={COMPLETED_STEPS} queryStats={STATS} />);
    expect(screen.getByText('Response delivered')).toBeInTheDocument();
    expect(screen.getByText(/3 queries/)).toBeInTheDocument();
    expect(screen.getByText(/42 rows/)).toBeInTheDocument();
    expect(screen.getByText(/2\.1s total/)).toBeInTheDocument();
  });
});
