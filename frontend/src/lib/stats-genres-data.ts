import type { GenreGroup, MediaScope } from './stats-overview-data';

export type GenreSort = 'count' | 'mean_score' | 'time';
export function genreSort(value: string | null | undefined): GenreSort {
  return value === 'mean_score' || value === 'time' ? value : 'count';
}
export function rankedGenres(rows: GenreGroup[], sort: GenreSort) {
  const value = (row: GenreGroup) => sort === 'mean_score' ? row.mean_score ?? -Infinity : sort === 'time' ? row.minutes : row.titles;
  return [...rows].sort((a, b) => value(b) - value(a) || b.titles - a.titles || a.label.localeCompare(b.label)).slice(0, 18);
}
export function genreBrowseHref(row: GenreGroup, media: MediaScope) {
  const type = media === 'series' ? 'series' : 'movie';
  return `/browse?${new URLSearchParams({ type, genres: row.browse_filters[type] })}`;
}
export function genreTime(row: Pick<GenreGroup, 'minutes' | 'runtime_missing_plays'>) {
  if (row.runtime_missing_plays && !row.minutes) return '—';
  const hours = Math.floor(row.minutes / 60), days = Math.floor(hours / 24), rest = hours % 24;
  const text = days ? `${days} ${days === 1 ? 'day' : 'days'}${rest ? ` ${rest} ${rest === 1 ? 'hour' : 'hours'}` : ''}`
    : hours ? `${hours} ${hours === 1 ? 'hour' : 'hours'}` : `${Math.floor(row.minutes)} minutes`;
  return `${row.runtime_missing_plays ? '≥ ' : ''}${text}`;
}
