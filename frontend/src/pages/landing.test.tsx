import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it } from 'vitest';
import { DATA_COVERAGE_CARDS, QUICK_START_TILES } from '@/constants/landing-data';
import LandingPage from './landing';

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location-display">{location.pathname + location.search}</div>;
}

function renderLanding() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route
          element={
            <>
              <LandingPage />
              <LocationDisplay />
            </>
          }
          path="/"
        />
        <Route element={<LocationDisplay />} path="*" />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Header', () => {
  it('renders the logo text "Ask Atlas"', () => {
    renderLanding();
    expect(screen.getByRole('link', { name: /ask atlas/i })).toBeInTheDocument();
  });

  it('renders nav links: GitHub and Atlas (no About)', () => {
    renderLanding();
    const header = screen.getByRole('banner');
    expect(within(header).queryByRole('link', { name: 'About' })).not.toBeInTheDocument();
    expect(within(header).getByRole('link', { name: 'GitHub' })).toBeInTheDocument();
    expect(within(header).getByRole('link', { name: 'Atlas' })).toBeInTheDocument();
  });

  it('renders "Start Chatting" CTA linking to /chat', () => {
    renderLanding();
    const cta = screen.getByRole('link', { name: /start chatting/i });
    expect(cta).toBeInTheDocument();
    expect(cta).toHaveAttribute('href', '/chat');
  });

  it('renders mobile hamburger menu button', () => {
    renderLanding();
    const header = screen.getByRole('banner');
    expect(within(header).getByRole('button', { name: /open menu/i })).toBeInTheDocument();
  });

  it('opens mobile menu dropdown when hamburger is clicked', async () => {
    const user = userEvent.setup();
    renderLanding();
    const header = screen.getByRole('banner');
    await user.click(within(header).getByRole('button', { name: /open menu/i }));
    // Dropdown adds extra links (desktop nav also has them), so use getAllBy
    const startLinks = screen.getAllByRole('link', { name: /start chatting/i });
    expect(startLinks.length).toBeGreaterThanOrEqual(2);
    const ghLinks = screen.getAllByRole('link', { name: 'GitHub' });
    expect(ghLinks.length).toBeGreaterThanOrEqual(2);
  });
});

describe('Hero Section', () => {
  it('renders heading and subtitle', () => {
    renderLanding();
    expect(screen.getByRole('heading', { name: /ask about global trade/i })).toBeInTheDocument();
    expect(screen.getByText(/ai-powered insights/i)).toBeInTheDocument();
  });

  it('renders search input with placeholder', () => {
    renderLanding();
    expect(screen.getByPlaceholderText(/top exports/i)).toBeInTheDocument();
  });

  it('does not render powered-by credit', () => {
    renderLanding();
    expect(
      screen.queryByText(/powered by growth lab at harvard university/i),
    ).not.toBeInTheDocument();
  });

  it('navigates to /chat?q=... on search submission', async () => {
    const user = userEvent.setup();
    renderLanding();
    const input = screen.getByPlaceholderText(/top exports/i);
    await user.type(input, 'coffee exports');
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByTestId('location-display')).toHaveTextContent('/chat?q=coffee+exports');
  });

  it('does NOT navigate on empty search submission', async () => {
    const user = userEvent.setup();
    renderLanding();
    await user.click(screen.getByRole('button', { name: 'Search' }));
    expect(screen.getByTestId('location-display')).toHaveTextContent('/');
  });
});

describe('Quick Start Section', () => {
  it('renders "QUICK START" section label', () => {
    renderLanding();
    expect(screen.getByText('QUICK START')).toBeInTheDocument();
  });

  it('renders "Explore Trade Data" heading', () => {
    renderLanding();
    expect(screen.getByText('Explore Trade Data')).toBeInTheDocument();
  });

  it('renders all 6 tile titles', () => {
    renderLanding();
    for (const tile of QUICK_START_TILES) {
      expect(screen.getByText(tile.title)).toBeInTheDocument();
    }
  });

  it('navigates to /chat?q=<query> when a tile is clicked', async () => {
    const user = userEvent.setup();
    renderLanding();
    const tile = QUICK_START_TILES[0];
    await user.click(screen.getByText(tile.title));
    const expected = '/chat?' + new URLSearchParams({ q: tile.query }).toString();
    expect(screen.getByTestId('location-display')).toHaveTextContent(expected);
  });
});

describe('Data Coverage Section', () => {
  it('renders "TRADE CLASSIFICATIONS" section label', () => {
    renderLanding();
    expect(screen.getByText('TRADE CLASSIFICATIONS')).toBeInTheDocument();
  });

  it('renders all 4 data coverage card titles', () => {
    renderLanding();
    for (const card of DATA_COVERAGE_CARDS) {
      expect(screen.getByText(card.title)).toBeInTheDocument();
    }
  });
});

describe('Footer', () => {
  it('renders simplified footer without credit text', () => {
    renderLanding();
    const footer = screen.getByRole('contentinfo');
    expect(screen.queryByText(/created by shreyas gadgin matha/i)).not.toBeInTheDocument();
    expect(within(footer).getByText('Ask Atlas')).toBeInTheDocument();
  });

  it('renders Atlas of Economic Complexity link', () => {
    renderLanding();
    expect(screen.getByRole('link', { name: /atlas of economic complexity/i })).toBeInTheDocument();
  });
});
