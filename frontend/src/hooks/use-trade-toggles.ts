import { useCallback, useState } from 'react';
import type { ClassificationSchema, TradeDirection, TradeMode, TradeOverrides } from '@/types/chat';

interface UseTradeTogglesReturn {
  overrides: TradeOverrides;
  resetAll: () => void;
  setDirection: (v: TradeDirection | null) => void;
  setMode: (v: TradeMode | null) => void;
  setOverrides: (o: TradeOverrides) => void;
  setSchema: (v: ClassificationSchema | null) => void;
}

const INITIAL: TradeOverrides = { direction: null, mode: null, schema: null };

export function useTradeToggles(): UseTradeTogglesReturn {
  const [overrides, setOverridesState] = useState<TradeOverrides>(INITIAL);

  const setDirection = useCallback((v: TradeDirection | null) => {
    setOverridesState((prev) => ({ ...prev, direction: v }));
  }, []);

  const setMode = useCallback((v: TradeMode | null) => {
    setOverridesState((prev) => ({
      ...prev,
      mode: v,
      schema: v === 'services' ? null : prev.schema,
    }));
  }, []);

  const setSchema = useCallback((v: ClassificationSchema | null) => {
    setOverridesState((prev) => ({ ...prev, schema: v }));
  }, []);

  const resetAll = useCallback(() => {
    setOverridesState(INITIAL);
  }, []);

  const setOverrides = useCallback((o: TradeOverrides) => {
    setOverridesState(o);
  }, []);

  return { overrides, resetAll, setDirection, setMode, setOverrides, setSchema };
}
