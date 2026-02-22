import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useTradeToggles } from './use-trade-toggles';

describe('useTradeToggles', () => {
  it('initial state is all null (auto-detect)', () => {
    const { result } = renderHook(() => useTradeToggles());
    expect(result.current.overrides).toEqual({ direction: null, mode: null, schema: null });
  });

  it('setDirection sets direction', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setDirection('exports'));
    expect(result.current.overrides.direction).toBe('exports');
  });

  it('setDirection(null) returns to auto', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setDirection('exports'));
    act(() => result.current.setDirection(null));
    expect(result.current.overrides.direction).toBeNull();
  });

  it('setMode sets mode', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setMode('goods'));
    expect(result.current.overrides.mode).toBe('goods');
  });

  it('setSchema sets schema', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setSchema('hs12'));
    expect(result.current.overrides.schema).toBe('hs12');
  });

  it('resetAll returns all to null', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => {
      result.current.setDirection('imports');
      result.current.setMode('services');
      result.current.setSchema('sitc');
    });
    act(() => result.current.resetAll());
    expect(result.current.overrides).toEqual({ direction: null, mode: null, schema: null });
  });

  it('setOverrides bulk-sets all three', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setOverrides({ direction: 'exports', mode: 'goods', schema: 'hs92' }));
    expect(result.current.overrides).toEqual({
      direction: 'exports',
      mode: 'goods',
      schema: 'hs92',
    });
  });

  it('setOverrides with partial nulls works', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setOverrides({ direction: 'exports', mode: null, schema: null }));
    expect(result.current.overrides).toEqual({ direction: 'exports', mode: null, schema: null });
  });

  it('individual setters do not affect other fields', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setDirection('exports'));
    act(() => result.current.setSchema('hs12'));
    expect(result.current.overrides).toEqual({ direction: 'exports', mode: null, schema: 'hs12' });
  });
});
