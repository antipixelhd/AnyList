

// #174: the season/episode numbers to *show* for an episode-like item - the
// display position when the show is on a non-aired ordering, else the canonical
// ones. Pass the item straight from the API (episode card, history row, ...).
export function displaySeasonEpisode(item: {
  season_number?: number | null;
  episode_number?: number | null;
  show_episode_order?: string | null;
  display_season_number?: number | null;
  display_episode_number?: number | null;
}): { season: number | null; episode: number | null } {
  const ordered = !!item.show_episode_order && item.show_episode_order !== "tmdb:aired";
  return {
    season: ordered && item.display_season_number != null
      ? item.display_season_number
      : item.season_number ?? null,
    episode: ordered && item.display_episode_number != null
      ? item.display_episode_number
      : item.episode_number ?? null,
  };
}

export function episodeCode(item: Parameters<typeof displaySeasonEpisode>[0]): string | null {
  const { season, episode } = displaySeasonEpisode(item);
  if (season == null || episode == null) return null;
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}
