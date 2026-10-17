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

/** Keep the destination page while reusing its profile preview identity. */
export function sharePagePath(pathname: string, search = ''): string {
  const identity = sharePath(pathname);
  if (identity) {
    let decoded: string;
    try { decoded = decodeURIComponent(pathname).replace(/\/$/, '') || '/'; }
    catch { return '/'; }
    const parts = decoded.split('/');
    const page = identity.startsWith('/user/')
      ? `${identity}${parts.length > 3 ? `/${parts[3]}` : ''}`
      : identity;
    if (page.endsWith('/stats')) {
      const input = new URLSearchParams(search);
      const allowed = new URLSearchParams();
      for (const key of ['media','section','sort','actor_sort','studio_sort','staff_sort','artwork']) {
        const value = input.get(key);
        if (value && value.length <= 50 && /^[a-z_-]+$/i.test(value)) allowed.set(key,value);
      }
      return page + (allowed.size ? `?${allowed}` : '');
    }
    return page;
  }
  // Token and auth callback URLs never become canonical share destinations.
  if (/^\/(?:auth|reset-password|forgot-password|confirm-email-change|oidc|link|api)(?:\/|-|$)/i.test(pathname)) return '/';
  if (/^\/(?:[a-z][a-z0-9-]*)(?:\/[a-z0-9-]+)*\/?$/i.test(pathname)) return pathname.replace(/\/$/, '') || '/';
  return '/';
}

export function mediaShareDescription(media: Record<string, any>): string {
  return [media.type === 'series' ? 'Series' : media.type === 'movie' ? 'Movie' : media.type,
    media.year, ...(media.genres || []), media.imdb_score == null ? null : `IMDb ${Number(media.imdb_score).toFixed(1)}`]
    .filter(Boolean).join(' · ');
}
