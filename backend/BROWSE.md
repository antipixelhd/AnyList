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
