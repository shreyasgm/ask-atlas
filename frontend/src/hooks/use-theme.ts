import { useCallback, useEffect, useSyncExternalStore } from 'react';

type Theme = 'dark' | 'light';

const STORAGE_KEY = 'ask-atlas-theme';

function getSystemPreference(): Theme {
  return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') {
      return stored;
    }
  } catch {
    // localStorage unavailable
  }
  return null;
}

function resolveTheme(): Theme {
  return getStoredTheme() ?? getSystemPreference();
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.toggle('dark', theme === 'dark');

  // Update theme-color meta tag for mobile browser chrome
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute('content', theme === 'dark' ? '#0A0F1C' : '#FFFFFF');
  }
}

// Shared mutable state so all hook instances stay in sync
let currentTheme: Theme = resolveTheme();
const listeners = new Set<() => void>();

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function getSnapshot(): Theme {
  return currentTheme;
}

function setTheme(next: Theme) {
  currentTheme = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // localStorage unavailable
  }
  applyTheme(next);
  for (const cb of listeners) {
    cb();
  }
}

// Apply on module load so the first paint matches
applyTheme(currentTheme);

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot);

  const toggle = useCallback(() => {
    setTheme(currentTheme === 'dark' ? 'light' : 'dark');
  }, []);

  // Listen for system preference changes (only matters when no stored pref)
  useEffect(() => {
    const mq = globalThis.matchMedia?.('(prefers-color-scheme: dark)');
    if (!mq) {
      return;
    }

    const handler = () => {
      if (!getStoredTheme()) {
        setTheme(mq.matches ? 'dark' : 'light');
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return { isDark: theme === 'dark', theme, toggle } as const;
}
