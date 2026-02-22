import { beforeEach, describe, expect, it, vi } from 'vitest';

const SESSION_KEY = 'ask_atlas_session_id';

// Mock localStorage since Node 22's built-in localStorage conflicts with jsdom
const store = new Map<string, string>();
const mockLocalStorage = {
  getItem: vi.fn((key: string) => store.get(key) ?? null),
  removeItem: vi.fn((key: string) => store.delete(key)),
  setItem: vi.fn((key: string, value: string) => store.set(key, value)),
};

vi.stubGlobal('localStorage', mockLocalStorage);

// Import after stubbing
const { getSessionId } = await import('./session');

describe('getSessionId', () => {
  beforeEach(() => {
    store.clear();
    vi.clearAllMocks();
  });

  it('creates a UUID and stores it in localStorage', () => {
    const id = getSessionId();
    expect(id).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
    expect(mockLocalStorage.setItem).toHaveBeenCalledWith(SESSION_KEY, id);
  });

  it('returns the same UUID on subsequent calls', () => {
    const first = getSessionId();
    const second = getSessionId();
    expect(first).toBe(second);
  });

  it('returns a different UUID after storage is cleared', () => {
    const first = getSessionId();
    store.clear();
    const second = getSessionId();
    expect(first).not.toBe(second);
  });
});
