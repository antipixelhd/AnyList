import type { StudioGroup } from './stats-overview-data';
import { rankedGenres, type GenreSort } from './stats-genres-data.ts';

export function rankedStudios(rows: StudioGroup[], sort: GenreSort) {
  return rankedGenres(rows, sort);
}

export function studioKey(value: string | undefined): string | null {
  let decoded: string;
  try { decoded = decodeURIComponent(value || ''); } catch { return null; }
  const key = /^[1-9][0-9]*$/.test(decoded) ? `tmdb:${decoded}` : decoded;
  return /^(catalogue|tmdb):[1-9][0-9]{0,9}$/.test(key) && Number(key.split(':')[1]) <= 2147483647 ? key : null;
}
