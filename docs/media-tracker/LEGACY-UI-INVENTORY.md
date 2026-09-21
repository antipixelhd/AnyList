# Legacy UI retirement inventory

Updated 2026-09-21. The canonical Phase Two interface is Home, Browse, username-based public profiles/lists/Stats/Social, title details, Notifications, Connections, and Profile Settings. This inventory covers **frontend presentation routes** only. It does not authorize deletion of backend APIs, stored data, migrations, provider callbacks or Scrob attribution.

## Retired presentation in the current slice

| Old route | Result | Why |
| --- | --- | --- |
| `/profile/{numeric_id}` | Privacy-aware 302 to `/user/{username}/`; inaccessible/missing users retain a neutral error. | Removes the competing Scrob profile while preserving old bookmarks and comment/search links. |
| `/stats/{numeric_id}` | Privacy-aware 302 to `/user/{username}/stats`; public anonymous bookmarks pass the same profile access check. | Removes the competing Scrob statistics page. |
| `/profile` | Redirects directly to the signed-in user's username profile. | Avoids a second numeric-ID hop. |
| `/movies`, `/shows`, `/search`, `/discover`, `/trending/movies`, `/trending/shows`, `/airing-today` | Thin compatibility redirects to Browse; media type and title query are preserved where meaningful, and legacy user searches go to the signed-in user's Social page. | Removes seven inherited discovery presentations while retaining old bookmarks and one canonical live-search/trending surface. `/discover-title` remains a non-visual import bridge used by Browse. |
| `/lists`, `/list/{id}` | Signed-in users redirect to their canonical username list; anonymous custom-list bookmarks redirect to Browse. | Custom lists are outside the accepted release, so the inherited presentation is retired while its stored data and backend APIs remain available for compatibility. |
| `/collection/movies`, `/collection/shows` | Redirect to the signed-in user's canonical streaming Library. | Replaces two provider-oriented collection screens with the one owner-only Library surface. |
| `/progress` | Redirects to the signed-in user's Series list with the Watching status selected. | Preserves the useful shortcut through the canonical filtered list. |
| `/dropped` | Redirects to the signed-in user's combined list with the Dropped status selected. | Preserves the useful shortcut without retaining a separate presentation. |
| `/history` | Redirects to the signed-in user's Profile Overview. | Own activity belongs on Profile; Home remains following-only. |
| `/calendar` | Redirects to Home. | Calendar is outside the accepted release and no competing legacy presentation is retained. |

The inherited Base-menu Profile and Statistics links now point to the canonical routes. The public legacy Stats route is allowed through the anonymous middleware gate only so the backend's public-profile check can authorize its redirect.

## Remaining UI families to assess and retire

| Family | Examples | Retirement condition |
| --- | --- | --- |
| Old media and people details | `/media/*`, `/show/*`, `/person/*`, `/network/*`, `/studio/*`, top-rated and recently-watched subpages | Old TMDB/TVDB identifiers are not necessarily AnyList title IDs. Resolve a safe canonical title mapping before redirecting; never guess an ID. Keep provider/integration URLs that require the old identifier. |
| Administrative and auth flows | `/admin`, `/requests`, `/register`, password recovery, activation, OIDC/device linking | Retain until an equivalent supported AnyList route exists. Login has been redesigned; this does not make these workflows disposable. |
| Non-UI integration surface | `/api/proxy/*`, `/partials/*`, `/docs`, `/redoc`, `/openapi.json`, webhooks and callbacks | Keep independently of presentation retirement. Preserve their access checks and protocol behavior. |

For every retired family, remove its navigation entry, document its redirect or deliberately unavailable outcome, run focused authenticated/anonymous route checks, and verify that another accepted workflow has not been lost. The Phase Two gate remains open until this inventory has no competing Scrob-style UI for released workflows.
