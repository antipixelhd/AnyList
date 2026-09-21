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
| `/media/{type}/{id}`, `/show/*` including season/episode and TVDB variants | TMDB movie/show bookmarks resolve through `/discover-title` into canonical `/title/{AnyList id}` for signed-in users. Episode bookmarks first resolve their parent show. TVDB bookmarks resolve a known TMDB cross-ID or fall back to a title-filtered Series Browse result without treating a TVDB ID as an AnyList ID. | Removes inherited watch/detail/episode presentations while preserving safe provider-identity mapping. |
| `/person/{id}`, `/network/{id}`, `/studio/{id}` | Redirect to the relevant Browse surface. | Cast/company detail is outside the accepted release; no competing Scrob presentation remains. |
| `/top-rated-*/*`, `/recently-watched-*/*` | Redirect through the privacy-aware numeric-profile compatibility route into canonical Profile Overview. | The canonical profile already owns highlights and activity. |
| `/collection/{id}`, `/continue-watching`, `/next-up` | Redirect to the signed-in user's Library or Watching-filtered Series list. | Streaming/library and current progress are represented by the accepted tracking workflows rather than legacy playback dashboards. |

The inherited Base-menu Profile and Statistics links now point to the canonical routes. The public legacy Stats route is allowed through the anonymous middleware gate only so the backend's public-profile check can authorize its redirect.

## Remaining UI families to assess and retire

| Family | Examples | Retirement condition |
| --- | --- | --- |
| Administrative and auth flows | `/admin`, `/requests`, `/register`, password recovery, activation, OIDC/device linking | Retain until an equivalent supported AnyList route exists. Login has been redesigned; this does not make these workflows disposable. |
| Non-UI integration surface | `/api/proxy/*`, `/partials/*`, `/docs`, `/redoc`, `/openapi.json`, webhooks and callbacks | Keep independently of presentation retirement. Preserve their access checks and protocol behavior. |

All competing Scrob-style presentation families for released workflows are now retired. Administrative, account-recovery and protocol surfaces remain because they still provide required behavior; their presence is not a second movie/series tracking interface. Backend media/show/list APIs, provider callbacks, stored data, migrations and export compatibility remain unchanged.
