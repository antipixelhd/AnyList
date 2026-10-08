/** Page-local, bounded cache: never mixes viewers or keeps discovery results indefinitely. */
export function createBrowseCache<T>(now = () => Date.now(), ttl = 5 * 60_000, limit = 40) {
  const entries = new Map<string, {value: T; expires: number}>();
  return {
    get(key: string): T | undefined {
      const entry = entries.get(key);
      if (!entry) return;
      if (entry.expires <= now()) {
        entries.delete(key);
        return;
      }
      entries.delete(key);
      entries.set(key, entry);
      return entry.value;
    },
    set(key: string, value: T) {
      entries.delete(key);
      entries.set(key, {value, expires: now() + ttl});
      while (entries.size > limit) entries.delete(entries.keys().next().value!);
    },
    values() { return [...entries.values()].map(entry => entry.value); },
  };
}

export function waitForBrowseRequest(delay: number, signal: AbortSignal) {
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  if (!delay) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, delay);
    signal.addEventListener('abort', abort, {once: true});
  });
}
