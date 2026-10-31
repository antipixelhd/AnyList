export type SteamGame = {
  app_id: string; name: string; poster: string; release_date: string | null;
  playtime_minutes: number; achievement_count: number | null; achievement_total: number | null;
};
export type SteamDraft = {
  minimum_playtime: number; minimum_achievements: number;
  playtime_enabled: boolean; achievements_enabled: boolean;
  overrides: Record<string, boolean>; sort: 'playtime' | 'alphabetical' | 'completion';
};
export function defaultSteamDraft(): SteamDraft {
  return { minimum_playtime: 30, minimum_achievements: 1, playtime_enabled: true,
    achievements_enabled: true, overrides: {}, sort: 'playtime' };
}
export function steamSelected(game: SteamGame, draft: SteamDraft): boolean {
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
