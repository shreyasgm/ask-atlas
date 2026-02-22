import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { TradeOverrides } from '@/types/chat';
import TradeTogglesBar from './trade-toggles-bar';

const NULL_OVERRIDES: TradeOverrides = { direction: null, mode: null, schema: null };

function renderBar(
  overrides: TradeOverrides = NULL_OVERRIDES,
  handlers: {
    onModeChange?: (v: null | string) => void;
    onSchemaChange?: (v: null | string) => void;
  } = {},
) {
  return render(
    <TradeTogglesBar
      onModeChange={handlers.onModeChange ?? vi.fn()}
      onSchemaChange={handlers.onSchemaChange ?? vi.fn()}
      overrides={overrides}
    />,
  );
}

describe('TradeTogglesBar', () => {
  it('renders mode and classification groups by default', () => {
    renderBar();
    // 2 Auto buttons + Goods, Services, HS92, HS12, SITC = 7 buttons
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(7);
  });

  it('Auto buttons are pressed by default when all overrides null', () => {
    renderBar();
    const autoButtons = screen.getAllByRole('button', { name: 'Auto' });
    expect(autoButtons).toHaveLength(2);
    for (const btn of autoButtons) {
      expect(btn).toHaveAttribute('aria-pressed', 'true');
    }
  });

  it('clicking Auto calls handler with null', async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    renderBar({ direction: null, mode: 'goods', schema: null }, { onModeChange });

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

  it('reflects active schema via aria-pressed', () => {
    renderBar({ direction: null, mode: null, schema: 'sitc' });
    expect(screen.getByRole('button', { name: 'SITC' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'HS92' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('hides classification toggle when mode is services', () => {
    const { container } = renderBar({ direction: null, mode: 'services', schema: null });
    // Only Auto, Goods, Services = 3 buttons
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'HS92' })).not.toBeInTheDocument();
    // No dividers
    const dividers = container.querySelectorAll('[data-testid="toggle-divider"]');
    expect(dividers).toHaveLength(0);
  });
});
