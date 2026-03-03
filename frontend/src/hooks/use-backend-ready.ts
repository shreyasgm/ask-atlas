import { useSyncExternalStore } from 'react';

let ready = false;
const listeners = new Set<() => void>();

function subscribe(callback: () => void) {
  listeners.add(callback);
  return () => {
    listeners.delete(callback);
  };
}

function getSnapshot() {
  return ready;
}

function setReady(value: boolean) {
  ready = value;
  for (const listener of listeners) {
    listener();
  }
}

// Fire-and-forget: must not block module evaluation, so top-level await is wrong here.
// oxlint-disable-next-line unicorn/prefer-top-level-await
fetch('/api/health')
  .then((res) => {
    if (res.ok) {
      setReady(true);
    }
  })
  .catch(() => {
    // Backend unreachable — leave ready as false
  });

export function useBackendReady(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot);
}
