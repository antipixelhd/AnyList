/** Public labels only: never derive a login preview from arbitrary return-path text. */
const generalPages: Record<string, string> = {
  '/': 'Welcome', '/login': 'Welcome', '/home': 'Home', '/browse': 'Browse',
  '/about': 'About', '/settings': 'Account', '/user-settings': 'Profile',
  '/settings/media': 'Movies & series', '/settings/lists': 'Lists',
  '/settings/notifications': 'Notifications', '/connections': 'Connections',
  '/recent-events': 'Notifications', '/requests': 'Requests',
  '/admin': 'Administration', '/admin/data-health': 'Data health', '/register': 'Register',
  '/movies': 'Movies', '/shows': 'Series', '/trending/movies': 'Trending movies',
  '/trending/shows': 'Trending series', '/collection/movies': 'Movie collection',
  '/collection/shows': 'Series collection', '/calendar': 'Calendar', '/lists': 'Lists',
  '/history': 'History', '/dropped': 'Dropped', '/progress': 'Progress',
  '/next-up': 'Next up', '/continue-watching': 'Continue watching', '/airing-today': 'Airing today',
  '/discover': 'Discover', '/search': 'Search',
};

export function generalSharePage(pathname: string, search = ''): {path: string; title: string} | null {
  const path = pathname.replace(/\/$/, '') || '/';
  if (!Object.hasOwn(generalPages, path)) return null;
  if (path === '/connections') {
    const section = new URLSearchParams(search).get('section');
    const sections: Record<string, string> = {imports: 'Import data', apps: 'Connected apps', developer: 'Developer'};
    if (section && Object.hasOwn(sections, section)) return {path: `${path}?section=${section}`, title: sections[section]};
  }
  return {path: path === '/login' ? '/' : path, title: generalPages[path]};
}

export function shareLoginPath(pathname: string, search = ''): string {
  if (sharePath(pathname)) return `/login?${new URLSearchParams({next: sharePagePath(pathname, search)})}`;
  const page = generalSharePage(pathname, search);
  return page ? `/login?${new URLSearchParams({next: page.path})}` : '/login';
}

/** Only known public card identities may supply metadata on a login page. */
export function loginShareIdentity(next: string | null): string | null {
  if (!next || !next.startsWith('/') || next.startsWith('//') || /[\\\u0000-\u0020\u007f#]/.test(next)) return null;
  return sharePath(next.split('?')[0]);
}

export async function shareMetadata(path: string, fetcher = fetch): Promise<{title: string; description: string} | null> {
  const identity = sharePath(path);
  if (!identity) return null;
  try {
    const port = process.env.BACKEND_PORT ?? '7331';
    const response = await fetcher(`http://127.0.0.1:${port}/sharing/metadata?${new URLSearchParams({path: identity})}`,
      {signal: AbortSignal.timeout(7000)});
    if (!response.ok) return null;
    const metadata = await response.json();
    return typeof metadata.title === 'string' && typeof metadata.description === 'string' ? metadata : null;
  } catch { return null; }
}

/** Crawlers get only the anonymous card projection, never a signed-in page. */
export async function crawlerShareResponse(url: URL, userAgent: string, origin: string, fetcher = fetch): Promise<Response | null> {
  if (!/(?:Discordbot|WhatsApp|TelegramBot|Twitterbot|facebookexternalhit|Facebot|Slackbot|LinkedInBot|SkypeUriPreview|Applebot)/i.test(userAgent)) return null;
  const path = sharePath(url.pathname);
  if (!path) return null;
  // The backend rechecks logged-out navigation, profile privacy and visibility.
  const metadata = await shareMetadata(path, fetcher);
  if (!metadata) return null;
  const escape = (value: string) => value.replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
  const canonical = new URL(sharePagePath(url.pathname, url.search), origin).href;
  const image = new URL(`/api/proxy/sharing/image?${new URLSearchParams({path})}`, origin).href;
  const tags: Record<string, string> = {'og:site_name':'AnyList', 'og:type':'website',
    'og:title':metadata.title, 'og:description':metadata.description, 'og:url':canonical,
    'og:image':image, 'og:image:type':'image/jpeg', 'og:image:width':'1200', 'og:image:height':'630',
    'og:image:alt':`${metadata.title} · AnyList`};
  const head = Object.entries(tags).map(([property, content]) => `<meta property="${property}" content="${escape(content)}">`).join('');
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escape(metadata.title)} · AnyList</title><link rel="canonical" href="${escape(canonical)}">${head}<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="${escape(metadata.title)}"><meta name="twitter:description" content="${escape(metadata.description)}"><meta name="twitter:image" content="${escape(image)}"></head><body><a href="${escape(canonical)}">${escape(metadata.title)}</a></body></html>`,
    {headers: {'Content-Type':'text/html; charset=utf-8', 'Cache-Control':'no-store', 'Vary':'User-Agent'}});
}

export function loginSharePage(next: string | null): {path: string; title: string} | null {
  if (!next || !next.startsWith('/') || next.startsWith('//') || /[\\\u0000-\u0020\u007f#]/.test(next)) return null;
  const split = next.indexOf('?');
  return generalSharePage(split < 0 ? next : next.slice(0, split), split < 0 ? '' : next.slice(split));
}

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
