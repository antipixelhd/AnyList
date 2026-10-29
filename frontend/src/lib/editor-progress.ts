export function editorProgressMaximum(title: {
  type: string;
  released_episodes?: number | null;
  entry?: {released_episodes?: number | null} | null;
  seasons?: {season_number: number; released_count: number | null}[];
}): number | null {
  if (title.type !== 'series') return null;
  const known = title.entry?.released_episodes ?? title.released_episodes;
  if (known != null) return known;
  const regular = title.seasons?.filter(season => season.season_number > 0);
  return regular?.length && regular.every(season => season.released_count != null)
    ? regular.reduce((sum, season) => sum + season.released_count!, 0) : null;
}
