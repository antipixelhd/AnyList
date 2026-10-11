import type { ActorTitle, StaffGroup, StaffTitle } from './stats-overview-data';
import type { GenreSort } from './stats-genres-data';
export function rankedStaff(rows: StaffGroup[], sort: GenreSort) {
  const value = (row: StaffGroup) => sort === 'mean_score' ? row.mean_score ?? -Infinity : sort === 'time' ? row.minutes : row.titles;
  return [...rows].sort((a,b) => value(b)-value(a) || b.titles-a.titles || a.prominence-b.prominence || a.label.localeCompare(b.label) || a.key.localeCompare(b.key)).slice(0,30);
}
export function personTitleLabel(title: ActorTitle | StaffTitle) {
  const role = 'roles' in title ? title.roles.join(' / ') : title.character;
  return role ? `${title.title} – ${role}` : title.title;
}
