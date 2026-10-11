import type { ActorGroup, ActorTitle } from './stats-overview-data';
import type { GenreSort } from './stats-genres-data';

export type ActorArtwork = 'media' | 'characters';
export function actorArtwork(value: string | null | undefined): ActorArtwork { return value === 'characters' ? 'characters' : 'media'; }
export function rankedActors(rows: ActorGroup[], sort: GenreSort) {
  const value = (row: ActorGroup) => sort === 'mean_score' ? row.mean_score ?? -Infinity : sort === 'time' ? row.minutes : row.titles;
  return [...rows].sort((a, b) => value(b) - value(a) || b.titles - a.titles || a.label.localeCompare(b.label) || a.key.localeCompare(b.key)).slice(0, 30);
}
export function actorTitleLabel(title: ActorTitle) { return title.character ? `${title.title} – ${title.character}` : title.title; }
export function actorTitleImages(title: ActorTitle, mode: ActorArtwork) {
  return [...new Set((mode === 'characters' ? [title.character_image, ...(title.character_images || []), title.poster] : [title.poster]).filter((url): url is string => !!url))];
}
export function actorTitleImage(title: ActorTitle, mode: ActorArtwork) { return actorTitleImages(title, mode)[0] || null; }
export function hasCharacterImages(rows: ActorGroup[]) { return rows.some(row => row.top_titles.some(title => !!title.character_image || !!title.character_images?.length)); }
