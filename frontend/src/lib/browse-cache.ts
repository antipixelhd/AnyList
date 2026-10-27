const BROWSE_STORAGE_PREFIX = 'anylist:browse:v2:';

const MINUTE = 60_000;

/** Bound client reuse on top of TMDB's 30-minute response cache. */
export function browseResultTtl(path: string, now: number): number {
  const url = new URL(path, 'https://anylist.invalid');
  const sort = url.searchParams.get('sort');
  const status = url.searchParams.get('status');
  const dateSensitive = url.pathname.endsWith('/sections') ||
    !!url.searchParams.get('q') ||
    ['trending', 'newest', 'upcoming'].includes(sort || '') ||
    ['airing', 'returning', 'upcoming'].includes(status || '') ||
    url.searchParams.has('timeline') || url.searchParams.has('cinema') ||
    url.searchParams.has('provider');
  const duration = (dateSensitive ? 30 : 60) * MINUTE;
  if (!dateSensitive) return duration;
  const nextUtcDay = Math.floor(now / 86_400_000) * 86_400_000 + 86_400_000;
  return Math.min(duration, nextUtcDay - now);
}

export const BROWSE_FACET_TTL = 6 * 60 * MINUTE;

export function clearStoredBrowseCache(storage: Storage) {
  try {
    for (const name of ['results', 'facets']) storage.removeItem(BROWSE_STORAGE_PREFIX + name);
  } catch { /* Storage is optional. */ }
}

/** Bounded cache; persistence is optional and scoped to one viewer and browser tab. */
export function createBrowseCache<T>(now = () => Date.now(), ttl: number | ((key: string, now: number) => number) = 5 * MINUTE, limit = 40,
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
      const stamp = now();
      const duration = typeof ttl === 'function' ? ttl(key, stamp) : ttl;
      const expires = !refresh && entries.get(key)?.expires || stamp + duration;
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
