# Browse discovery

Browse uses the existing server-wide MDBList key from Settings, falling back
to a member's key when the server key is absent. A TMDB key is needed for
title searches, Upcoming, tags, and streaming-service filters. Basic MDBList
browsing works without TMDB credentials.

The backend calls MDBList's JSON API; it never fetches IMDb chart HTML.
`/lists/official/moviemeter/items` supplies IMDb trending for movies and TV.
Public community lists 64016 (10,000 most-voted movies) and 177610 (5,000
most-voted shows) supply the broader rating/vote catalogue. These are
community-maintained lists, not guaranteed replicas of IMDb's Top 250.
`MDBLIST_BROWSE_MOVIE_LIST_ID` and `MDBLIST_BROWSE_SERIES_LIST_ID` can replace
those catalogue IDs with appropriate public MDBList lists.

MovieMeter refreshes on demand at most every six hours. The two broader
catalogues refresh on demand at most daily. All cursor pages are downloaded
with ratings, genres, dates, status, and posters. Both media types share one
MovieMeter snapshot; rating and vote sorts share each catalogue snapshot.
With the current list sizes and 1,000 items per API response, regular Browse
traffic uses approximately 19 MDBList requests per day (4 + 10 + 5), excluding
retries, account synchronization, and separate title-score enrichment.
Without Browse traffic, there are no discovery refresh requests.

Snapshots persist under `DATA_DIR/mdblist-discovery/`. Filenames use hashes of
the feed and credential; API keys are never written into snapshots. Failed
refreshes can serve cached feeds up to seven days old with a notice. Provider
failures have a cooldown; daily quota errors back off for six hours. A
read-only data directory retains only the in-process cache, so restarts there
can consume another full refresh. Run one backend worker to avoid independent
workers duplicating initial or expired refreshes.

IMDb vote and rating sorts are calculated locally. Highest rated requires
25,000 IMDb votes for movies and 10,000 for shows to exclude tiny voting
samples. Dates, genres, status, adult exclusions, and anime visibility are
applied across the cached feed before pagination. Tags and regional streaming
filters scan TMDB details in order until a full matching page plus one entry
is found, using the existing TMDB metadata cache. These never make additional
MDBList requests. The catalogue is finite; filters search that snapshot,
not every title known to IMDb.

No schema migration is required. Relevant checks:
`python -m unittest tests.test_mdblist_discovery tests.test_browse tests.test_mdblist`
from `backend/`; API tests require a disposable `TRACKING_TEST_DATABASE_URL`.

Editor actions use complete viewer-owned snapshots attached to Browse, search,
and the owner's list responses. Entry fields and streaming-library intent are
queried in batches from the database; preparing an editor never calls metadata
providers. Title pages seed the same client store from their existing data.
The authenticated `/tracking/editor/{id}` endpoint is a lightweight fallback
for an uncached entry. Public lists never seed another viewer's editor state.

`frontend/src/lib/editor-store.ts` keeps this data in viewer-scoped memory.
Status, rating, editor saves, and library changes paint optimistically and use
one ordered queue per title. Failed writes roll back only their own intent;
newer clicks remain visible. Imports are deduplicated and happen on writes,
so opening and cancelling an unimported title's editor leaves the catalogue
untouched. Account changes discard snapshots and fence queued requests.

Editor artwork reuses the displayed poster's exact URL. Pointer/focus intent
warms a known backdrop; each opening chooses a decoded backdrop or the cached
poster before showing the dialog and keeps it for that opening. No background
refresh changes active form fields or swaps its images. Saving errors restore
the draft for retry. Client regression checks: `npm test` from `frontend/`.
