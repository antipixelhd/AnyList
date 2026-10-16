/** Share identities exclude filters, pagination, tracking tokens and tabs. */
export function sharePath(pathname: string): string | null {
  let path: string;
  try { path = decodeURIComponent(pathname).replace(/\/$/, ''); } catch { return null; }
  const profile = /^\/user\/([^/?#\\]{1,100})(?:\/(?:movies|series|list|social|stats|favorites|library))?$/.exec(path);
  if (profile) return `/user/${encodeURIComponent(profile[1])}`;
  if (/^\/title\/[1-9][0-9]{0,9}$/.test(path)) return path;
  if (/^\/(actors|person|studio)\/(?:(?:catalogue|tmdb):)?[1-9][0-9]{0,9}$/.test(path)) return path;
  return null;
}

export function mediaShareDescription(media: Record<string, any>): string {
  return [media.type === 'series' ? 'Series' : media.type === 'movie' ? 'Movie' : media.type,
    media.year, ...(media.genres || []), media.imdb_score == null ? null : `IMDb ${Number(media.imdb_score).toFixed(1)}`]
    .filter(Boolean).join(' · ');
}
