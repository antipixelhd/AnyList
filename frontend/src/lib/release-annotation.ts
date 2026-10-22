export function releaseAnnotation(newSeasons = 0, newEpisodes = 0): string | null {
  if (newSeasons > 0) return newSeasons === 1 ? 'new season' : `${newSeasons} new seasons`;
  if (newEpisodes > 0) return newEpisodes === 1 ? 'new episode' : `${newEpisodes} new episodes`;
  return null;
}
