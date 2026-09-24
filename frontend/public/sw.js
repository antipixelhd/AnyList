// AnyList service worker
// Strategy:
//   - Static assets (JS/CSS/fonts/icons): NetworkFirst, cached for offline fallback
//   - TMDB / Proxy images (posters, backdrops, rating posters): bypass service worker (native HTTP cache)
//   - /api/proxy/*: NetworkOnly — library data must always be fresh
//   - Navigation (HTML pages): NetworkFirst, offline fallback if all fail

const SHELL_CACHE  = 'media-tracker-shell-v4';

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(c => c.add('/offline.html'))
  );
  self.skipWaiting();
});

// ── Activate — prune old caches ───────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  const keep = [SHELL_CACHE];
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => !keep.includes(k)).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Content scripts can cause extension-origin requests to appear in the
  // controlled page's fetch stream. Service worker Cache APIs only accept
  // HTTP(S) requests, so leave other schemes entirely to the browser.
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;

  // Only handle GET — leave POST/PATCH/DELETE to the network
  if (request.method !== 'GET') return;

  // Vite development modules carry optimizer version hashes. Never cache
  // them across a rebuild or serve a stale module as an offline fallback.
  if (url.pathname.startsWith('/node_modules/.vite/') || url.pathname.startsWith('/@vite/') || url.pathname.startsWith('/@id/') || url.pathname.startsWith('/src/')) return;

  // Poster/backdrop images (incl. the RPDB rating-poster proxy) use native
  // HTTP caching, not the app-shell cache.
  if (url.hostname === 'image.tmdb.org' || url.pathname.startsWith('/api/proxy/media/image/') || url.pathname.startsWith('/api/proxy/media/rating-poster/')) {
    return; // fall through to browser default (native network/disk cache)
  }

  // API calls: always network, never cache
  if (url.pathname.startsWith('/api/')) {
    return; // fall through to browser default (network)
  }

  // Web manifest: NetworkFirst to handle potential auth redirects / CORS correctly
  if (url.pathname.endsWith('.webmanifest')) {
    event.respondWith(networkFirstWithOffline(request));
    return;
  }

  // Static assets (hashed JS/CSS/fonts/icons in /_astro/): network-first so
  // content is always fresh (avoids stale cache in dev and after deploys).
  // Cache is kept as an offline fallback only.
  if (url.pathname.startsWith('/_astro/') || isStaticAsset(url.pathname)) {
    event.respondWith(networkFirstWithCache(request, SHELL_CACHE));
    return;
  }

  // Navigation (HTML pages): network-first, offline fallback
  if (request.mode === 'navigate') {
    event.respondWith(networkFirstWithOffline(request));
    return;
  }
});

// ── Completion rating notifications ──────────────────────────────────────────
self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data?.json() ?? {}; } catch { payload = { body: event.data?.text() }; }
  const title = payload.title || 'AnyList';
  event.waitUntil(self.registration.showNotification(title, {
    body: payload.body || `${title} completed. Rate now!`,
    icon: payload.icon || '/web-app-manifest-192x192.png',
    badge: payload.badge || '/favicon-96x96.png',
    image: payload.image || undefined,
    tag: payload.tag || 'media-tracker-rating',
    renotify: true,
    data: { url: payload.url || '/recent-events' },
    actions: [{ action: 'rate', title: 'Rate now' }],
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/recent-events', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of windows) {
      if ('navigate' in client) await client.navigate(target);
      return client.focus();
    }
    return self.clients.openWindow(target);
  })());
});

// ── Strategies ────────────────────────────────────────────────────────────────

async function networkFirstWithCache(request, cacheName) {
  const protocol = new URL(request.url).protocol;
  if (protocol !== 'http:' && protocol !== 'https:') return fetch(request);

  try {
    const response = await fetch(request);
    if (response.ok) {
      // Keep cache storage best-effort. A quota or cache failure must not
      // reject the successful network response as an unhandled promise.
      try {
        const cache = await caches.open(cacheName);
        await cache.put(request, response.clone());
      } catch {
        // The response can still be used even when it cannot be cached.
      }
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached ?? Response.error();
  }
}

async function networkFirstWithOffline(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    return caches.match('/offline.html');
  }
}

function isStaticAsset(pathname) {
  return /\.(js|css|woff2?|ico|png|svg|webp|jpg|jpeg|webmanifest)$/.test(pathname);
}
