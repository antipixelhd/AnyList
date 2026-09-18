# Test Media Tracker locally

This is a working local preview. It is ready for testing lists, ratings, progress, search and imported Stremio data. Provider integration is not yet complete for unrestricted bidirectional use.

## Open the app

1. Start Docker Desktop if it is not already running.
2. Double-click **Start Media Tracker.cmd** in this folder. It starts the isolated database and web servers, then opens **http://localhost:7340**.
3. Find your local passwords in **.venv/LOCAL-LOGIN.txt**. Sign in with **provider-test** for your private Stremio import, or **preview** for synthetic examples you can freely change. These are app logins, separate from your streaming account.

The app binds to this PC's loopback interface. Google SSO and VPS access are not part of this local test setup.

## Try these workflows

- **Movie List / Series List:** search, filter status groups, sort, and switch between compact rows and covers. These links open your own profile lists.
- **Quick edit:** use a row's **…** button to change status, half-point rating, notes, dates or favorites. Zero clears the score.
- **Title details:** click a title. For a real series, use **Refresh episodes** if offered before testing cumulative episode progress or season watched controls. Synthetic placeholders do not all have provider identities.
- **Season ratings:** choose averaging or a separate show rating. Changing modes preserves your manual show score; unrated seasons never enter the average.
- **Browse:** search for a movie or series using the configured TMDB access, open its details, then add it to a list.
- **Profile → Library:** inspect streaming library membership separately from tracked lists.
- **Home → Recent events:** inspect the first-import summary. Leave this real connection unapproved during this preview.

Your Stremio import currently contains **36 tracked entries**. Its outbound flags and schedules are disabled. Local edits do not currently write to your Stremio account. Keep these controls disabled; real outbound sync still needs isolated provider testing. Use the preview account for deletion experiments so your imported local lists remain convenient to inspect.

## Stop or restart

Double-click **Stop Media Tracker.cmd** to stop the two web servers. The isolated database and saved data remain available. Start again with **Start Media Tracker.cmd**. Logs are in `.venv/backend.error.log` and `.venv/frontend.error.log`; do not share them without checking for account details.

## Current limits

- Stremio authentication and full read-only import are verified against your supplied account. Repeated import performed **zero remote writes**.
- Playback dismissal/restoration and tracking-reset retries have automated tests for Stremio/Nuvio. No real outbound writes or live Nuvio session have been tested.
- Streaming library mirroring, non-streaming provider reconciliation, rating conversion echo protection, detailed field-level conflict review and independent retry scheduling remain unfinished.
- Some connection/settings screens still use Scrob's original layout. External IMDb/Rotten Tomatoes enrichment and Google SSO remain unverified.

Latest verification details and the implementation backlog are in `../docs/media-tracker/STATUS.md`.
