import type { StudioGroup } from './stats-overview-data';
import { rankedGenres, type GenreSort } from './stats-genres-data.ts';

export function rankedStudios(rows: StudioGroup[], sort: GenreSort) {
  return rankedGenres(rows, sort);
}
