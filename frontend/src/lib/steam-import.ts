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
  ratings: Record<string, number | null>;
  statuses: Record<string, SteamDraft['import_status']>;
  import_status: 'completed' | 'playing' | 'planning' | 'paused' | 'dropped';
  completion_dates: Record<string, string | null>;
  start_dates: Record<string, string | null>;
  minimum_playtime: number; minimum_achievements: number;
  playtime_enabled: boolean; achievements_enabled: boolean;
  overrides: Record<string, boolean>; sort: 'playtime' | 'alphabetical' | 'completion';
};
export function defaultSteamDraft(): SteamDraft {
  return { ratings: {}, statuses: {}, import_status: 'completed', completion_dates: {}, start_dates:{}, minimum_playtime: 30, minimum_achievements: 1, playtime_enabled: true,
    achievements_enabled: true, overrides: {}, sort: 'playtime' };
}
export function steamStatus(game: SteamGame, draft: SteamDraft): SteamDraft['import_status'] {
  return draft.statuses[game.app_id] ?? draft.import_status;
}
export function chooseSteamStatus(game: SteamGame, draft: SteamDraft, status: SteamDraft['import_status']): void {
  draft.statuses[game.app_id] = status;
}
export function steamAccentText(color: string): string {
  const channels = color.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i)?.slice(1);
  if (!channels) return '#0b1622';
  const [r,g,b] = channels.map(channel => {
    const value = parseInt(channel,16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  const luminance = .2126*r + .7152*g + .0722*b;
  // Compare contrast against the actual dark text/surface color and white.
  const dark = .0077;
  return (luminance + .05) / (dark + .05) >= 1.05 / (luminance + .05) ? '#0b1622' : '#ffffff';
}
export function filterSteamGames(games: SteamGame[], query: string): SteamGame[] {
  const normalize = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase();
  const words = normalize(query).trim().split(/\s+/).filter(Boolean);
  return games.filter(game => {
    const name = normalize(game.name);
    return words.every(word => name.includes(word));
  });
}
// Keep mounted cards connected so shared selects and decoded artwork survive
// filtering. Reorder the visible window in DOM order for keyboard navigation.
export function reconcileSteamCards(grid: HTMLElement, visible: HTMLElement[]): void {
  const exposed = new Set(visible);
  for (const child of Array.from(grid.children) as HTMLElement[]) {
    const hidden = !exposed.has(child);
    if (child.hidden !== hidden) child.hidden = hidden;
  }
  let cursor = grid.firstElementChild;
  for (const node of visible) {
    if (node.hidden) node.hidden = false;
    if (node !== cursor) grid.insertBefore(node, cursor);
    cursor = node.nextElementSibling;
  }
}
export function steamScrollAnchor(cards: Iterable<HTMLElement>, visible: ReadonlySet<string>, bounds: {top:number;bottom:number}): {element:HTMLElement;top:number} | undefined {
  for (const element of cards) {
    if (!element.isConnected || !visible.has(element.dataset.appId!) || element.parentElement?.hidden) continue;
    const rect = element.getBoundingClientRect();
    if (rect.height > 0 && rect.top >= bounds.top && rect.top < bounds.bottom) return {element,top:rect.top};
  }
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
