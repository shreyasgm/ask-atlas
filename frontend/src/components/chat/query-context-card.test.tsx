import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { EntitiesData, QueryAggregateStats } from '@/types/chat';
import QueryContextCard from './query-context-card';

const ENTITIES: EntitiesData = {
  countries: [{ iso3Code: 'IND', name: 'India' }],
  docsConsulted: [],
  graphqlClassification: null,
  graphqlEntities: null,
  lookupCodes: 'name_to_code',
  products: [
    { codes: ['0901'], name: 'Coffee', schema: 'HS92' },
    { codes: ['0902'], name: 'Tea', schema: 'HS92' },
  ],
  resolutionNotes: [],
  schemas: ['HS92'],
};

const STATS: QueryAggregateStats = {
  totalExecutionTimeMs: 1500,
  totalGraphqlQueries: 0,
  totalGraphqlTimeMs: 0,
  totalQueries: 3,
  totalRows: 42,
  totalTimeMs: 2100,
};

describe('QueryContextCard', () => {
  it('renders nothing when entitiesData is null', () => {
    const { container } = render(<QueryContextCard entitiesData={null} queryStats={null} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows country names and product names with codes in collapsed state', () => {
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={null} />);
    expect(screen.getByText('India')).toBeInTheDocument();
    expect(screen.getByText('Coffee (0901)')).toBeInTheDocument();
    expect(screen.getByText('Tea (0902)')).toBeInTheDocument();
  });

  it('uses the actual schema name in the collapsed products label', () => {
    const hs12Entities: EntitiesData = {
      countries: [],
      docsConsulted: [],
      graphqlClassification: null,
      graphqlEntities: null,
      lookupCodes: '',
      products: [{ codes: ['8541'], name: 'Semiconductors', schema: 'HS12' }],
      resolutionNotes: [],
      schemas: ['HS12'],
    };
    render(<QueryContextCard entitiesData={hs12Entities} queryStats={null} />);
    expect(screen.getByText(/Products \(HS12\)/)).toBeInTheDocument();
  });

  it('hides country row when no countries are present', () => {
    const noCountry: EntitiesData = {
      countries: [],
      docsConsulted: [],
      graphqlClassification: null,
      graphqlEntities: null,
      lookupCodes: 'name_to_code',
      products: [{ codes: ['0901'], name: 'Coffee', schema: 'HS92' }],
      resolutionNotes: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={noCountry} queryStats={null} />);
    expect(screen.queryByText('India')).not.toBeInTheDocument();
    // Products should still render
    expect(screen.getByText('Coffee (0901)')).toBeInTheDocument();
  });

  it('hides products row when no products are present', () => {
    const noProducts: EntitiesData = {
      countries: [{ iso3Code: 'IND', name: 'India' }],
      docsConsulted: [],
      graphqlClassification: null,
      graphqlEntities: null,
      lookupCodes: '',
      products: [],
      resolutionNotes: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={noProducts} queryStats={null} />);
    expect(screen.queryByText(/Products/)).not.toBeInTheDocument();
    // Country should still render
    expect(screen.getByText('India')).toBeInTheDocument();
  });

  it('expands on click to show full product names, country names, and stats', async () => {
    const user = userEvent.setup();
    render(<QueryContextCard entitiesData={ENTITIES} queryStats={STATS} />);

    await user.click(screen.getByRole('button', { name: /expand query context/i }));

    // Country shows name
    expect(screen.getByText('India')).toBeInTheDocument();
    // Products show name (code)
    expect(screen.getByText('Coffee (0901)')).toBeInTheDocument();
    expect(screen.getByText('Tea (0902)')).toBeInTheDocument();
    // Stats rendered
    expect(screen.getByText(/3 SQL queries/)).toBeInTheDocument();
    expect(screen.getByText(/42 rows/)).toBeInTheDocument();
  });

  it('hides country and products sections in expanded state when empty', async () => {
    const user = userEvent.setup();
    const schemaOnly: EntitiesData = {
      countries: [],
      docsConsulted: [],
      graphqlClassification: null,
      graphqlEntities: null,
      lookupCodes: '',
      products: [],
      resolutionNotes: [],
      schemas: ['HS92'],
    };
    render(<QueryContextCard entitiesData={schemaOnly} queryStats={STATS} />);

    await user.click(screen.getByRole('button', { name: /expand query context/i }));

    expect(screen.getByText('Query Context')).toBeInTheDocument();
    expect(screen.getByText(/Schema: HS92/)).toBeInTheDocument();
    // Neither countries nor products rendered
    expect(screen.queryByText(/India/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Products:/)).not.toBeInTheDocument();
  });
});
