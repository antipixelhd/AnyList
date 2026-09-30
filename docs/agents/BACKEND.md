# Backend reference

Read only for backend changes or questions. Confirm details in the affected code.

## Where to look

- Python FastAPI application: backend/main.py wires routes and application lifecycle.
- backend/routers/: HTTP endpoints; backend/schemas.py: shared request/response schemas.
- backend/core/: tracking rules, provider integrations, reconciliation, and delivery.
- backend/models/: SQLAlchemy persistence; backend/db.py: async sessions.
- backend/migrations/: schema migrations; backend/tests/: existing behavior checks.
- backend/core/config.py: settings. Never copy credentials into documentation.

Shared credential resolution lives in core/settings_store.py. User TMDB keys
precede global keys. Request lookups cache values (including misses) per database
session; job lookups with an existing settings row refresh the global fallback.

## Behavior to preserve

- List status, granular watch history, playback position, and streaming-library
  membership are separate concepts.
- Database timestamp columns use millisecond precision. Python assignments,
  provider comparisons, SQL defaults, and updates truncate sub-milliseconds;
  timezone-aware columns retain their timezone behavior. Model-wide setup lives
  in models/timestamps.py and core/timestamps.py.
- Deleting a tracked entry does not delete the shared catalog title or its independent
  streaming-library membership.
- Statuses are Planning, Watching, Paused, Dropped, and Completed. Completed can mean
  caught up with released episodes; it does not assert that unreleased episodes were watched.
- Completed does not automatically return to Watching when new episodes appear.
- Zero means unrated. Meaningful ratings use 0.5â€“10.
- Manual show ratings do not populate season ratings. Calculated show ratings average
  explicitly rated regular seasons equally, excluding specials and unrated seasons.
- Private users' personal state must remain private, including social aggregates.
- Tracking-entry notes are shared with everyone who can access the profile list;
  private-profile and logged-out browsing checks still apply.
- Initial imports suppress historical social activity. Rating-only edits to titles
  imported already Completed have a seven-day activity grace period; status changes
  end that grace period.
- Provider capabilities differ. Remote removal is not automatically a local entry
  deletion. Preserve reconciliation, deletion markers, and delivery acknowledgments.
- Watch-date quality is episode-specific evidence, then inferred dates, then
  Stremio title-wide dates for other episodes (`WatchEvent.date_shared`). Shared
  dates cannot replace better evidence; exported estimates returning unchanged
  remain estimates. Streaming reconciliation makes one title decision per pull.
- Nuvio watch state is shared, but Next Up settings are platform-specific:
  TV uses title IDs in `trakt_settings.dismissed_next_up_keys`; Mobile uses
  episode seed keys in the JSON string `continue_watching_settings_payload`.
  Update and confirm both blobs; accepted resumption clears every title alias.

Shared title, entry, and activity display projections live in
core/tracking_projection.py. Airing and new-season indicators, metadata release
dates, and season notices share core/season_releases.py.

Plex watchlist persistence and delivery live in core/plex_watchlist.py;
core/watchlist_reconcile.py owns the pure three-way reconciliation plan.

## Implementation anchors

Streaming connections are independent by connection ID. Each AnyList user may
connect multiple Stremio accounts and multiple Nuvio accounts/profiles; duplicate
Stremio accounts or Nuvio account/profile pairs on the same canonical endpoint
are rejected within that user. The same remote identity may belong to another
AnyList user. Accepted changes propagate to same-provider peers using each
destination's enabled directions and approved reconciliation.
Nuvio connections need independent sign-in sessions because refresh tokens
rotate; an existing connection's token cannot be reused for another connection.

Changing a connection's account or Nuvio profile clears its snapshot, cursors,
reviews, and queued deliveries, requiring a fresh first import; same-identity
reauthentication preserves them. Deletion removes connection-scoped state while
preserving accepted AnyList history. Active syncs must finish before switching
or deleting; identity versions fence operations loaded before a switch.

For tracking changes, start with tracking_rules.py and the affected tracking router.
For provider sync, inspect pull_cycle.py, tracking_snapshot.py, and the relevant
reconciliation/delivery modules. For streaming-library changes, inspect
streaming_library.py and stream_actions.py.

Peer change delivery lives in core/outbound_sync.py: destination selection,
reconciliation gates, provider writes, and bounded batching. core/watch_echo.py
tracks pending Jellyfin/Emby echoes; core/bingebase.py sends playback events.
Inbound payload normalization lives in core/webhook_payloads.py, with independent
parser regressions in test_webhook_payloads.py.
Live playback forwarding uses core/scrobble_delivery.py. It owns provider gates,
Trakt token refresh, and Simkl completion fallback; handlers select the event.
Focused regressions are test_watch_echo.py, test_bingebase.py, and
test_scrobble_delivery.py.
Explicit watch/unwatch corrections live in core/watch_delivery.py, with focused
regressions in test_watch_delivery.py. Streaming writes go through durable watch
intents; provider rollback and exclusion rules do not depend on HTTP routes.

For Nuvio outbound state, inspect core/nuvio_payloads.py for wire formatting and
identity selection, and core/nuvio_projection.py for library, watch-history, and
Continue Watching queries. core/nuvio.py owns provider transport; routers/sync.py
orchestrates sync jobs and full pushes. Payload and projection tests live in
backend/tests/test_nuvio_payloads.py and test_nuvio_projection.py.

Trakt token validation and refresh live in core/trakt_auth.py. Shared MDBList
wire formatting and nested show/season/episode merging live in core/mdblist_payloads.py.
Their focused tests are test_trakt_auth.py and test_mdblist_payloads.py.

For Stremio outbound state, core/stremio_delivery.py owns projection, serialized
writes, and playback confirmation. core/stremio_payloads.py owns item formatting
and watch/resume state updates; core/stremio.py owns HTTP transport and Cinemeta
fetching. The focused tests are test_stremio_delivery.py and test_stremio_payloads.py.

The database is PostgreSQL with async SQLAlchemy. Existing naive timestamps are
interpreted as UTC; database sessions are pinned to UTC in db.py.
Keep schema changes in migrations and follow existing transaction boundaries.
Use the affected code and focused existing tests to establish current behavior;
historical plans may describe intentions that have since changed.
