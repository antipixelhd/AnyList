type ViewerAuth = Pick<typeof import('./api').api.auth, 'me' | 'pageContext'>;
type PageRequest = {pathname: string; isStaticAsset: boolean; profilePanel?: boolean};
const AUTH_REDIRECTS = new Set(['/login', '/register', '/logout']);

export function needsPageContext(pathname: string, isStaticAsset: boolean) {
  return !isStaticAsset && !pathname.startsWith('/api/') && !AUTH_REDIRECTS.has(pathname);
}

/** Resolve fresh viewer data once for this SSR request, with the legacy fallback. */
export async function resolveViewerContext(auth: ViewerAuth, token: string, request: PageRequest) {
  const pageContext = needsPageContext(request.pathname, request.isStaticAsset)
    ? await auth.pageContext(token, !request.profilePanel).catch(() => undefined) : undefined;
  const user = pageContext?.user ?? await auth.me(token);
  return {user, pageContext};
}
