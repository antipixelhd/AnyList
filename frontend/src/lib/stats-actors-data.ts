import type { ActorGroup, ActorTitle } from './stats-overview-data';
import type { GenreSort } from './stats-genres-data';

export type ActorArtwork = 'media' | 'characters';
export function actorArtwork(value: string | null | undefined): ActorArtwork { return value === 'characters' ? 'characters' : 'media'; }
export function rankedActors(rows: ActorGroup[], sort: GenreSort) {
  const value = (row: ActorGroup) => sort === 'mean_score' ? row.mean_score ?? -Infinity : sort === 'time' ? row.minutes : row.titles;
  return [...rows].sort((a, b) => value(b) - value(a) || b.titles - a.titles || a.label.localeCompare(b.label) || a.key.localeCompare(b.key)).slice(0, 30);
}
export function actorTitleLabel(title: ActorTitle) { return title.character ? `${title.title} – ${title.character}` : title.title; }
export function actorTitleImage(title: ActorTitle, mode: ActorArtwork) { return mode === 'characters' ? title.character_image || title.poster : title.poster; }
export function hasCharacterImages(rows: ActorGroup[]) { return rows.some(row => row.top_titles.some(title => !!title.character_image)); }
