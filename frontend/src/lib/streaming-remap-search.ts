import {quickSearchRequests, readQuickTitles} from './quick-search-data.ts';
import type {SearchPage} from './search-fallback.ts';

/** Remapping uses the same title retrieval as quick search, without contributor work. */
export async function readRemapTitles<T>(query: string, seriesOnly: boolean, signal: AbortSignal,
  request: (path: string, signal: AbortSignal) => Promise<SearchPage>) {
  const requests = quickSearchRequests(query).filter(value => seriesOnly
    ? value.kind === 'series' : value.kind === 'movie' || value.kind === 'series');
  const result = await readQuickTitles<T>(requests, signal, async (value, current, publish) => {
    publish(await request(value.url.replace(/^\/api\/proxy/, ''), current));
  }, !seriesOnly);
  if (result.failed && !result.movie.length && !result.series.length) {
    throw new Error('Search is unavailable. Try again.');
  }
  return result;
}
