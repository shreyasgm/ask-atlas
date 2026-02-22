import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { TradeOverrides } from '@/types/chat';
import TradeTogglesBar from './trade-toggles-bar';

const NULL_OVERRIDES: TradeOverrides = { direction: null, mode: null, schema: null };

function renderBar(
  overrides: TradeOverrides = NULL_OVERRIDES,
  handlers: {
    onDirectionChange?: (v: null | string) => void;
    onModeChange?: (v: null | string) => void;
    onSchemaChange?: (v: null | string) => void;
  } = {},
) {
  return render(
    <TradeTogglesBar
      onDirectionChange={handlers.onDirectionChange ?? vi.fn()}
      onModeChange={handlers.onModeChange ?? vi.fn()}
      onSchemaChange={handlers.onSchemaChange ?? vi.fn()}
      overrides={overrides}
    />,
  );
}

describe('TradeTogglesBar', () => {
  it('has toolbar role', () => {
    renderBar();
    expect(screen.getByRole('toolbar')).toBeInTheDocument();
  });

  it('has accessible label', () => {
    renderBar();
    expect(screen.getByRole('toolbar', { name: /trade query constraints/i })).toBeInTheDocument();
  });

  it('renders MODE label', () => {
    renderBar();
    expect(screen.getByText('MODE')).toBeInTheDocument();
  });

  it('renders all toggle buttons including Auto options', () => {
    renderBar();
    // 3 Auto buttons + Goods, Services, Exports, Imports, HS92, HS12, SITC = 10 buttons
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(10);
  });

  it('renders specific option buttons', () => {
    renderBar();
    expect(screen.getByRole('button', { name: 'Goods' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Services' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Exports' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Imports' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'HS92' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'HS12' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SITC' })).toBeInTheDocument();
  });

  it('Auto buttons are pressed by default when all overrides null', () => {
    renderBar();
    const autoButtons = screen.getAllByRole('button', { name: 'Auto' });
    expect(autoButtons).toHaveLength(3);
    for (const btn of autoButtons) {
      expect(btn).toHaveAttribute('aria-pressed', 'true');
    }
  });

  it('specific option shows aria-pressed when active', () => {
    renderBar({ direction: 'exports', mode: null, schema: null });
    expect(screen.getByRole('button', { name: 'Exports' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Imports' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('clicking specific option calls handler with value', async () => {
    const user = userEvent.setup();
    const onDirectionChange = vi.fn();
    renderBar(NULL_OVERRIDES, { onDirectionChange });

    await user.click(screen.getByRole('button', { name: 'Exports' }));
    expect(onDirectionChange).toHaveBeenCalledWith('exports');
  });

  it('clicking Auto calls handler with null', async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    renderBar({ direction: null, mode: 'goods', schema: null }, { onModeChange });

    // There are 3 Auto buttons — find the one in the mode group
    // The first Auto is in the mode group (first group)
    const autoButtons = screen.getAllByRole('button', { name: 'Auto' });
    await user.click(autoButtons[0]);
    expect(onModeChange).toHaveBeenCalledWith(null);
  });

  it('clicking schema option calls onSchemaChange', async () => {
    const user = userEvent.setup();
    const onSchemaChange = vi.fn();
    renderBar(NULL_OVERRIDES, { onSchemaChange });

    await user.click(screen.getByRole('button', { name: 'HS12' }));
    expect(onSchemaChange).toHaveBeenCalledWith('hs12');
  });

  it('renders vertical dividers between groups', () => {
    const { container } = renderBar();
    const dividers = container.querySelectorAll('[data-testid="toggle-divider"]');
    expect(dividers).toHaveLength(2);
  });

  it('mode Auto not pressed when mode is selected', () => {
    renderBar({ direction: null, mode: 'goods', schema: null });
    const autoButtons = screen.getAllByRole('button', { name: 'Auto' });
    // First Auto is mode group
    expect(autoButtons[0]).toHaveAttribute('aria-pressed', 'false');
  });

  it('classification schema shows correct active style', () => {
    renderBar({ direction: null, mode: null, schema: 'sitc' });
    expect(screen.getByRole('button', { name: 'SITC' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'HS92' })).toHaveAttribute('aria-pressed', 'false');
  });
});
