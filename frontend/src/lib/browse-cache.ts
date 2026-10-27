const BROWSE_STORAGE_PREFIX = 'anylist:browse:v1:';

export function clearStoredBrowseCache(storage: Storage) {
  try {
    for (const name of ['results', 'facets']) storage.removeItem(BROWSE_STORAGE_PREFIX + name);
  } catch { /* Storage is optional. */ }
}

/** Bounded cache; persistence is optional and scoped to one viewer and browser tab. */
export function createBrowseCache<T>(now = () => Date.now(), ttl = 5 * 60_000, limit = 40,
  persistence?: {storage: Storage; name: 'results' | 'facets'; viewer: string}) {
  const entries = new Map<string, {value: T; expires: number}>();
  const storageKey = persistence && BROWSE_STORAGE_PREFIX + persistence.name;
  try {
    const saved = storageKey && persistence?.storage.getItem(storageKey);
    const data = saved && JSON.parse(saved);
    if (data?.viewer === persistence?.viewer && Array.isArray(data.entries)) {
      for (const [key, entry] of data.entries.slice(-limit)) {
        if (typeof key === 'string' && entry && typeof entry.expires === 'number' && entry.expires > now()) entries.set(key, entry);
      }
    }
  } catch { /* Unavailable or corrupt storage falls back to memory. */ }
  const persist = () => {
    if (!storageKey || !persistence) return;
    try {
      const saved = [...entries];
      let encoded = JSON.stringify({viewer: persistence.viewer, entries: saved});
      while (encoded.length > 1_000_000 && saved.length) {
        saved.shift();
        encoded = JSON.stringify({viewer: persistence.viewer, entries: saved});
      }
      persistence.storage.setItem(storageKey, encoded);
    } catch {
      try { persistence.storage.removeItem(storageKey); } catch { /* Storage is optional. */ }
    }
  };
  return {
    get(key: string): T | undefined {
      const entry = entries.get(key);
      if (!entry) return;
      if (entry.expires <= now()) {
        entries.delete(key);
        persist();
        return;
      }
      entries.delete(key);
      entries.set(key, entry);
      return entry.value;
    },
    set(key: string, value: T, refresh = true) {
      const expires = !refresh && entries.get(key)?.expires || now() + ttl;
      entries.delete(key);
      entries.set(key, {value, expires});
      while (entries.size > limit) entries.delete(entries.keys().next().value!);
      persist();
    },
    values() { return [...entries.values()].map(entry => entry.value); },
    clear() { entries.clear(); persist(); },
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
