# Phase Two verification notes

## Overlapping provider pulls

The bounded pull-cycle barrier is intentionally process-local because the current deployment runs one backend scheduler process. Baselines and outbound actions remain durable in PostgreSQL, but a future deployment with multiple scheduler processes should replace this barrier with a database-backed lease or advisory lock before enabling every process to schedule pulls.

## Retained page validation

A retained page is shown immediately and its online validation refreshes the cache for the next visit. It does not replace the visible page while the user is reading it, which avoids a surprise content swap but means the current view can remain stale until the next navigation.

## Offline Connection Settings

The in-tab Connection Settings cache retains a deliberately sparse read-only summary: provider names and only whitelisted, last-viewed status labels. It excludes forms, credentials, tokens, scripts, and live operations. Online navigation fetches the full settings page instead of replaying this summary.

## Existing media server connections

Jellyfin, Emby, and Plex now require a complete, error-free import and confirmation of its Notifications summary before outbound synchronization. Existing connections have no reviewed baseline yet, so their next full import will create that review; enabled outbound fields will wait for approval.

## Media server rating order

The current Jellyfin, Emby, and Plex rating feeds do not provide a comparable per-rating modification time in this integration. A score changed from its reviewed source baseline can replace a matching, unchanged local score. If the local score also changed or the source first appears with a different score, AnyList keeps the local value and asks for review in Notifications.
