import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { EntitiesData, QueryAggregateStats } from '@/types/chat';
import QueryContextCard from './query-context-card';

const ENTITIES: EntitiesData = {
  countries: [{ iso3Code: 'IND', name: 'India' }],
  lookupCodes: 'name_to_code',
  products: [
    { codes: ['0901'], name: 'Coffee', schema: 'HS92' },
    { codes: ['0902'], name: 'Tea', schema: 'HS92' },
  ],
  schemas: ['HS92'],
};

const STATS: QueryAggregateStats = {
  totalExecutionTimeMs: 1500,
  totalQueries: 3,
  totalRows: 42,
  totalTimeMs: 2100,
};

describe('QueryContextCard', () => {
  it('renders nothing when entitiesData is null', () => {
    const { container } = render(<QueryContextCard entitiesData={null} queryStats={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders collapsed state with country ISO3 code in green pill', () => {
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={null} />);
    expect(screen.getByText('Country:')).toBeInTheDocument();
    expect(screen.getByText('IND')).toBeInTheDocument();
  });

  it('renders collapsed state with schema label and product code pills', () => {
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={null} />);
    expect(screen.getByText('HS92:')).toBeInTheDocument();
    expect(screen.getByText('0901')).toBeInTheDocument();
    expect(screen.getByText('0902')).toBeInTheDocument();
  });

  it('shows "Name (ISO3)" format in expanded country pill', async () => {
    const user = userEvent.setup();
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={STATS} />);

    await user.click(screen.getByRole('button', { name: /expand query context/i }));

    expect(screen.getByText('India (IND)')).toBeInTheDocument();
  });

  it('expands on click to show full details with product name+code pills', async () => {
    const user = userEvent.setup();
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={STATS} />);

    await user.click(screen.getByRole('button', { name: /expand query context/i }));

    expect(screen.getByText('Query Context')).toBeInTheDocument();
    expect(screen.getByText('Country:')).toBeInTheDocument();
    expect(screen.getByText(/Schema: HS92/)).toBeInTheDocument();
    expect(screen.getByText('Products:')).toBeInTheDocument();
    // Product pills show "code name" format
    expect(screen.getByText('0901 Coffee')).toBeInTheDocument();
    expect(screen.getByText('0902 Tea')).toBeInTheDocument();
  });

  it('shows query stats line when queryStats provided', async () => {
    const user = userEvent.setup();
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={STATS} />);

    await user.click(screen.getByRole('button', { name: /expand query context/i }));

    expect(screen.getByText(/3 queries/)).toBeInTheDocument();
    expect(screen.getByText(/42 rows/)).toBeInTheDocument();
  });

  it('handles empty products array gracefully', () => {
    const emptyEntities: EntitiesData = {
      countries: [],
      lookupCodes: 'name_to_code',
      products: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={emptyEntities} queryStats={null} />);
    expect(screen.getByText('HS92:')).toBeInTheDocument();
  });

  it('shows dash when no countries provided', () => {
    const noCountryEntities: EntitiesData = {
      countries: [],
      lookupCodes: 'name_to_code',
      products: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={noCountryEntities} queryStats={null} />);
    // Should show a dash placeholder when no countries
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('shows multiple country codes in collapsed view', () => {
    const multiCountry: EntitiesData = {
      countries: [
        { iso3Code: 'IND', name: 'India' },
        { iso3Code: 'USA', name: 'United States' },
      ],
      lookupCodes: '',
      products: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={multiCountry} queryStats={null} />);
    expect(screen.getByText('IND')).toBeInTheDocument();
    expect(screen.getByText('USA')).toBeInTheDocument();
  });
});
