import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { QueryAggregateStats, QueryResult } from '@/types/chat';
import QueriesTab from './queries-tab';

const QUERIES: Array<QueryResult> = [
  {
    columns: ['product', 'value'],
    executionTimeMs: 42,
    rowCount: 15,
    rows: [['soybeans', 100]],
    sql: 'SELECT product, value FROM hs92_trade LIMIT 15',
  },
  {
    columns: ['partner'],
    executionTimeMs: 1200,
    rowCount: 5,
    rows: [['USA']],
    sql: "SELECT partner FROM trade WHERE country = 'BRA'",
  },
];

const STATS: QueryAggregateStats = {
  totalExecutionTimeMs: 150,
  totalQueries: 2,
  totalRows: 20,
  totalTimeMs: 3000,
};

describe('QueriesTab', () => {
  it('shows empty state when no queries', () => {
    render(<QueriesTab currentQueries={[]} queryStats={null} />);
    expect(screen.getByText(/no queries executed yet/i)).toBeInTheDocument();
  });

  it('renders query count badge', () => {
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('renders query cards with index labels', () => {
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);
    expect(screen.getByText('Q1')).toBeInTheDocument();
    expect(screen.getByText('Q2')).toBeInTheDocument();
  });

  it('shows execution time on each card', () => {
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);
    expect(screen.getByText('42ms')).toBeInTheDocument();
    expect(screen.getByText('1.2s')).toBeInTheDocument();
  });

  it('shows SQL when card is expanded', async () => {
    const user = userEvent.setup();
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);

    // SQL not visible initially
    expect(screen.queryByText(/SELECT product/)).not.toBeInTheDocument();

    // Click first card to expand
    await user.click(screen.getByText('Q1'));

    expect(screen.getByText(/SELECT product, value FROM hs92_trade/)).toBeInTheDocument();
    expect(screen.getByText(/15 rows returned/)).toBeInTheDocument();
  });

  it('Copy SQL button is present when card expanded', async () => {
    const user = userEvent.setup();
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);

    // Expand first card
    await user.click(screen.getByText('Q1'));

    // Copy button should be visible with correct aria-label
    const copyButton = screen.getByRole('button', { name: /copy sql/i });
    expect(copyButton).toBeInTheDocument();
  });

  it('renders aggregate stats footer', () => {
    render(<QueriesTab currentQueries={QUERIES} queryStats={STATS} />);
    expect(screen.getByText('Total rows')).toBeInTheDocument();
    expect(screen.getByText('20')).toBeInTheDocument();
    expect(screen.getByText('Total time')).toBeInTheDocument();
    expect(screen.getByText('3.0s')).toBeInTheDocument();
    expect(screen.getByText('Execution time')).toBeInTheDocument();
    expect(screen.getByText('150ms')).toBeInTheDocument();
  });

  it('hides aggregate footer when no stats', () => {
    render(<QueriesTab currentQueries={QUERIES} queryStats={null} />);
    expect(screen.queryByText('Total rows')).not.toBeInTheDocument();
  });
});
