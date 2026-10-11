import { mountMediaGroups } from './stats-media-groups';
import { genreBrowseHref, genreSort, rankedGenres } from './stats-genres-data';
import { rankedStudios } from './stats-studios-data';

export function mountGenres(root: HTMLElement) {
  return mountMediaGroups(root, { section: 'genres', prefix: 'genre', parameter: 'sort',
    rows: data => rankedGenres(data.genres || [], genreSort(root.dataset.genreSort)),
    href: (row, data) => genreBrowseHref(row, data.media_type) });
}
export function mountStudios(root: HTMLElement) {
  return mountMediaGroups(root, { section: 'studios', prefix: 'studio', parameter: 'studio_sort',
    rows: data => rankedStudios(data.studios || [], genreSort(root.dataset.studioSort)),
    href: row => row.href });
}
