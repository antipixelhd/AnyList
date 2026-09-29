export function safeReturnPath(value: unknown, fallback = '/'): string {
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020\u007f]/.test(value)) {
    return fallback;
  }
  return value;
}

export function requiresSameOrigin(method: string, pathname: string, hasSession: boolean, hasBearerAuth: boolean): boolean {
  if (['GET', 'HEAD', 'OPTIONS'].includes(method)) return false;
  return !pathname.startsWith('/api/proxy/') || (hasSession && !hasBearerAuth);
}

export function isSameOrigin(request: Request, url: URL, publicUrl?: string): boolean {
  const origin = request.headers.get('Origin');
  if (!origin) return false;
  try {
    const host = request.headers.get('Host');
    const expected = new URL(url.origin);
    // Astro can rewrite an unconfigured Host to localhost. Keep the actual
    // request host for direct access; trust proxy forwarding only for SERVER_URL.
    if (host) expected.host = host;
    if (publicUrl) {
      const configured = new URL(publicUrl);
      if ([host, request.headers.get('X-Forwarded-Host')].includes(configured.host)) {
        return origin === configured.origin;
      }
    }
    return origin === expected.origin;
  } catch {
    return false;
  }
}
