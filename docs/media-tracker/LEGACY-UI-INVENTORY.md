# Legacy UI retirement inventory

Updated 2026-09-20. The canonical Phase Two interface is Home, Browse, username-based public profiles/lists/Stats/Social, title details, Notifications, Connections, and Profile Settings. This inventory covers **frontend presentation routes** only. It does not authorize deletion of backend APIs, stored data, migrations, provider callbacks or Scrob attribution.

## Retired presentation in the current slice

| Old route | Result | Why |
| --- | --- | --- |
| `/profile/{numeric_id}` | Privacy-aware 302 to `/user/{username}/`; inaccessible/missing users retain a neutral error. | Removes the competing Scrob profile while preserving old bookmarks and comment/search links. |
| `/stats/{numeric_id}` | Privacy-aware 302 to `/user/{username}/stats`; public anonymous bookmarks pass the same profile access check. | Removes the competing Scrob statistics page. |
| `/profile` | Redirects directly to the signed-in user's username profile. | Avoids a second numeric-ID hop. |

The inherited Base-menu Profile and Statistics links now point to the canonical routes. The public legacy Stats route is allowed through the anonymous middleware gate only so the backend's public-profile check can authorize its redirect.

## Remaining UI families to assess and retire

| Family | Examples | Retirement condition |
| --- | --- | --- |
| Old discovery/search | `/movies`, `/shows`, `/search`, `/discover`, `/trending/*`, `/airing-today` | Replace links with Browse and preserve useful query/type bookmarks where possible. Confirm any still-used public catalogue flows before redirecting. |
| Old personal collections and custom lists | `/lists`, `/list/{id}`, `/collection/*`, `/progress`, `/dropped`, `/history`, `/calendar` | Map accepted tracking workflows to the username list/History surfaces. Keep stored collections and export compatibility even if their old UI is retired. |
| Old media and people details | `/media/*`, `/show/*`, `/person/*`, `/network/*`, `/studio/*`, top-rated and recently-watched subpages | Old TMDB/TVDB identifiers are not necessarily AnyList title IDs. Resolve a safe canonical title mapping before redirecting; never guess an ID. Keep provider/integration URLs that require the old identifier. |
| Administrative and auth flows | `/admin`, `/requests`, `/register`, password recovery, activation, OIDC/device linking | Retain until an equivalent supported AnyList route exists. Login has been redesigned; this does not make these workflows disposable. |
| Non-UI integration surface | `/api/proxy/*`, `/partials/*`, `/docs`, `/redoc`, `/openapi.json`, webhooks and callbacks | Keep independently of presentation retirement. Preserve their access checks and protocol behavior. |

For every retired family, remove its navigation entry, document its redirect or deliberately unavailable outcome, run focused authenticated/anonymous route checks, and verify that another accepted workflow has not been lost. The Phase Two gate remains open until this inventory has no competing Scrob-style UI for released workflows.
