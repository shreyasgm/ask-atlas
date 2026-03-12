import { useCallback, useEffect, useRef, useState } from 'react';

const STORAGE_KEY = 'ask-atlas-sidebar-width';
const MIN_WIDTH = 180;
const MAX_WIDTH = 480;
const DEFAULT_WIDTH = 220;

function clampWidth(width: number): number {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, width));
}

function loadPersistedWidth(): number {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored !== null) {
      const parsed = Number(stored);
      if (Number.isFinite(parsed)) {
        return clampWidth(parsed);
      }
    }
  } catch {
    // localStorage unavailable
  }
  return DEFAULT_WIDTH;
}

function persistWidth(width: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(width));
  } catch {
    // localStorage unavailable
  }
}

interface UseSidebarResizeResult {
  isDragging: boolean;
  onDragHandlePointerDown: (e: React.PointerEvent) => void;
  width: number;
}

export function useSidebarResize(enabled: boolean): UseSidebarResizeResult {
  const [width, setWidth] = useState(loadPersistedWidth);
  const [isDragging, setIsDragging] = useState(false);
  const startXRef = useRef(0);
  const startWidthRef = useRef(0);

  const onDragHandlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (!enabled) {
        return;
      }
      e.preventDefault();
      startXRef.current = e.clientX;
      startWidthRef.current = width;
      setIsDragging(true);
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [enabled, width],
  );

  useEffect(() => {
    if (!isDragging) {
      return;
    }

    // Prevent text selection while dragging
    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const onPointerMove = (e: PointerEvent) => {
      const delta = e.clientX - startXRef.current;
      const newWidth = clampWidth(startWidthRef.current + delta);
      setWidth(newWidth);
    };

    const onPointerUp = () => {
      setIsDragging(false);
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
    };

    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', onPointerUp);

    return () => {
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', onPointerUp);
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
    };
  }, [isDragging]);

  // Persist width when drag ends
  useEffect(() => {
    if (!isDragging) {
      persistWidth(width);
    }
  }, [isDragging, width]);

  return { isDragging, onDragHandlePointerDown, width };
}
