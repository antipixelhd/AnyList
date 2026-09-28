# Backend reference

Read only for backend changes or questions. Confirm details in the affected code.

## Where to look

- Python FastAPI application: backend/main.py wires routes and application lifecycle.
- backend/routers/: HTTP endpoints; backend/schemas.py: shared request/response schemas.
- backend/core/: tracking rules, provider integrations, reconciliation, and delivery.
- backend/models/: SQLAlchemy persistence; backend/db.py: async sessions.
- backend/migrations/: schema migrations; backend/tests/: existing behavior checks.
- backend/core/config.py: settings. Never copy credentials into documentation.

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

## Implementation anchors

For tracking changes, start with tracking_rules.py and the affected tracking router.
For provider sync, inspect pull_cycle.py, tracking_snapshot.py, and the relevant
reconciliation/delivery modules. For streaming-library changes, inspect
streaming_library.py and stream_actions.py.

The database is PostgreSQL with async SQLAlchemy. Existing naive timestamps are
interpreted as UTC; database sessions are pinned to UTC in db.py.
Keep schema changes in migrations and follow existing transaction boundaries.
Use the affected code and focused existing tests to establish current behavior;
historical plans may describe intentions that have since changed.
