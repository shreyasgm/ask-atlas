import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { EntitiesData } from '@/types/chat';
import EntitiesTab from './entities-tab';

const ENTITIES: EntitiesData = {
  lookupCodes: '8541,8542',
  products: [
    { codes: ['8541', '8542'], name: 'Semiconductors', schema: 'hs92' },
    { codes: ['2709'], name: 'Crude petroleum', schema: 'hs92' },
  ],
  schemas: ['hs92'],
};

describe('EntitiesTab', () => {
  it('shows empty state when no entities data', () => {
    render(<EntitiesTab entitiesData={null} isRestoredThread={false} />);
    expect(screen.getByText(/no entities resolved yet/i)).toBeInTheDocument();
  });

  it('shows restored thread message instead of default empty state', () => {
    render(<EntitiesTab entitiesData={null} isRestoredThread={true} />);
    expect(screen.queryByText(/no entities resolved yet/i)).not.toBeInTheDocument();
    expect(screen.getByText(/only available for the current session/i)).toBeInTheDocument();
  });

  it('renders product names and code chips', () => {
    render(<EntitiesTab entitiesData={ENTITIES} isRestoredThread={false} />);
    expect(screen.getByText('Semiconductors')).toBeInTheDocument();
    expect(screen.getByText('Crude petroleum')).toBeInTheDocument();
    expect(screen.getByText('8541')).toBeInTheDocument();
    expect(screen.getByText('8542')).toBeInTheDocument();
    expect(screen.getByText('2709')).toBeInTheDocument();
  });

  it('renders unique product count', () => {
    render(<EntitiesTab entitiesData={ENTITIES} isRestoredThread={false} />);
    expect(screen.getByText('2 unique products')).toBeInTheDocument();
  });

  it('renders schema badges', () => {
    render(<EntitiesTab entitiesData={ENTITIES} isRestoredThread={false} />);
    expect(screen.getByText('hs92')).toBeInTheDocument();
  });

  it('renders resolution method', () => {
    render(<EntitiesTab entitiesData={ENTITIES} isRestoredThread={false} />);
    expect(screen.getByText(/auto-resolved/i)).toBeInTheDocument();
  });

  it('shows country and partner placeholders', () => {
    render(<EntitiesTab entitiesData={ENTITIES} isRestoredThread={false} />);
    const placeholders = screen.getAllByText('Not available yet');
    expect(placeholders).toHaveLength(2);
  });

  it('deduplicates products with same name', () => {
    const duped: EntitiesData = {
      lookupCodes: '',
      products: [
        { codes: ['8541'], name: 'Semiconductors', schema: 'hs92' },
        { codes: ['8542'], name: 'Semiconductors', schema: 'hs92' },
      ],
      schemas: ['hs92'],
    };
    render(<EntitiesTab entitiesData={duped} isRestoredThread={false} />);
    expect(screen.getByText('1 unique product')).toBeInTheDocument();
    expect(screen.getByText('8541')).toBeInTheDocument();
    expect(screen.getByText('8542')).toBeInTheDocument();
  });
});
