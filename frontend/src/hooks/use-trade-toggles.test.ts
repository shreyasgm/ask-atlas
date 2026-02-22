import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useTradeToggles } from './use-trade-toggles';

describe('useTradeToggles', () => {
  it('initial state is all null (auto-detect)', () => {
    const { result } = renderHook(() => useTradeToggles());
    expect(result.current.overrides).toEqual({ direction: null, mode: null, schema: null });
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

  it('individual setters do not affect other fields', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setDirection('exports'));
    act(() => result.current.setSchema('hs12'));
    expect(result.current.overrides).toEqual({ direction: 'exports', mode: null, schema: 'hs12' });
  });

  it('setMode("services") clears schema to null', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setSchema('hs92'));
    expect(result.current.overrides.schema).toBe('hs92');
    act(() => result.current.setMode('services'));
    expect(result.current.overrides.mode).toBe('services');
    expect(result.current.overrides.schema).toBeNull();
  });

  it('setMode("goods") preserves existing schema', () => {
    const { result } = renderHook(() => useTradeToggles());
    act(() => result.current.setSchema('hs12'));
    act(() => result.current.setMode('goods'));
    expect(result.current.overrides.mode).toBe('goods');
    expect(result.current.overrides.schema).toBe('hs12');
  });
});
