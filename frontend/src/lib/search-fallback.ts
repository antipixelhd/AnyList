export type SearchPage = {results: unknown[]; has_more?: boolean; notice?: string; verification_pending?: boolean};

/** Partial can mean the curated candidate cap, rather than unfinished work. */
export function verifiedEmptySearch(data: SearchPage): boolean {
  return Array.isArray(data.results) && data.results.length === 0 && !data.has_more
    && !data.notice && !data.verification_pending;
}

export function shouldBroadenSearch(values: URLSearchParams, data: SearchPage): boolean {
  return !!values.get('q')?.trim() && values.get('unfiltered') !== 'true' && verifiedEmptySearch(data);
}

export function broadSearchParams(values: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(values);
  next.set('unfiltered', 'true');
  next.set('page', '1');
  next.delete('source');
  next.delete('cursor');
  return next;
}

/** Only automatic expansion follows this exact query/filter/layout context. */
export function createSearchExpansion() {
  let identity = '', active = false, suppressed = false;
  const sync = (values: URLSearchParams) => {
    const context = new URLSearchParams(values);
    for (const name of ['page', 'source', 'cursor', 'unfiltered']) context.delete(name);
    context.sort();
    const next = context.toString();
    if (next !== identity) { identity = next; active = false; suppressed = false; }
  };
  return {
    forSearch(values: URLSearchParams) { sync(values); return active; },
    allowed(values: URLSearchParams) { sync(values); return !suppressed; },
    activate(values: URLSearchParams) { sync(values); active = true; },
    suppress(values: URLSearchParams) { sync(values); active = false; suppressed = true; },
  };
}
