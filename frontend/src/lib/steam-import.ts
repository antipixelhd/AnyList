export type SteamGame = {
  app_id: string; name: string; poster: string; release_date: string | null;
  last_played?: number | null;
  mapping_state?: 'pending' | 'ready' | 'unavailable' | 'software';
  playtime_minutes: number; achievement_count: number | null; achievement_total: number | null;
  achievement_state?: 'loading' | 'ready' | 'unavailable'; achievement_refresh_due?: boolean; achievement_refreshing?: boolean;
};
export function steamProgressPending(game: SteamGame): boolean {
  if (game.mapping_state === 'software') return false;
  return game.mapping_state === 'pending' || game.achievement_refresh_due !== false || game.achievement_state === 'loading' || game.achievement_refreshing === true;
}
export type SteamDraft = {
  import_status: 'completed' | 'playing' | 'planning' | 'paused' | 'dropped';
  completion_dates: Record<string, string | null>;
  start_dates: Record<string, string | null>;
  minimum_playtime: number; minimum_achievements: number;
  playtime_enabled: boolean; achievements_enabled: boolean;
  overrides: Record<string, boolean>; sort: 'playtime' | 'alphabetical' | 'completion';
};
export function defaultSteamDraft(): SteamDraft {
  return { import_status: 'completed', completion_dates: {}, start_dates:{}, minimum_playtime: 30, minimum_achievements: 1, playtime_enabled: true,
    achievements_enabled: true, overrides: {}, sort: 'playtime' };
}
export function steamSelected(game: SteamGame, draft: SteamDraft): boolean {
  if (game.mapping_state === 'unavailable' || game.mapping_state === 'software') return false;
  if (Object.hasOwn(draft.overrides, game.app_id)) return draft.overrides[game.app_id];
  return (draft.playtime_enabled && game.playtime_minutes >= draft.minimum_playtime)
    || (draft.achievements_enabled && game.achievement_count !== null && game.achievement_count >= draft.minimum_achievements);
}
export function sortSteamGames(games: SteamGame[], sort: SteamDraft['sort']): SteamGame[] {
  const completion = (g: SteamGame) => g.achievement_total && g.achievement_count !== null ? g.achievement_count / g.achievement_total : -1;
  return [...games].sort((a, b) => {
    const value = sort === 'playtime' ? b.playtime_minutes - a.playtime_minutes
      : sort === 'completion' ? completion(b) - completion(a) : 0;
    return value || a.name.localeCompare(b.name) || a.app_id.localeCompare(b.app_id);
  });
}
