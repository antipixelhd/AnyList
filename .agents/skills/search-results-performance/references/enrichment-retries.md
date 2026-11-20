# Enrichment retries

Primary metadata, credits, ratings, achievements, optional artwork and prices
retain independent acquisition and last-good content. Public source caches stay
demand-driven; expired retry deadlines do not enqueue provider requests.
Provider-ID resolution validates IGDB IDs directly; catalogue aliases apply to
local work paths. DLC credit publication uses verified parent participation,
while positive mobile-only evidence and other established exclusions still win.

`backend/core/enrichment_retry.py` separates provider recovery from content gaps:

- Outages, timeouts, rate limits, authentication/access restrictions, malformed
  responses and ingestion failures keep exponential backoff and Retry-After.
  A malformed reply never establishes absence.
- Failed game jobs and regional offers keep recurring recovery work for seven
  days after the last demand. They then pause until another visit, which may
  re-admit immediately. Pending visits renew demand at most once per day;
  healthy warm reads remain read-only. Tracked screen-art backfills retain their
  existing scheduled demand. Provider Retry-After deadlines still apply.
- Explicit missing content, missing provider records and identity conflicts get
  three consecutive attempts before leaving the recurring worker queue. Their
  errors remain visible and their outcome remains unverified.
- Demand can re-admit deferred content after expiry: one day for imminent or
  recently released titles, one week after 90 days, one month after a year and
  90 days after two years. Unknown dates and distant future releases use one
  week. Release gates shorten the interval. Partial year/month/quarter dates
  use the latest possible date, so an unreleased title never ages early.
  Ongoing series and series with unknown status are capped at one week, even
  when their original premiere is old.
- Changed verified mappings release affected cooldowns through the existing
  mapping lifecycle. Changed dependencies can wake a settled stale binding,
  without repeatedly restarting active work or ordinary policy sweeps.
  Completed unmatched/unresolved jobs clear previous request errors and use
  the same age-based, on-demand expiry. Missing primary/type/parent evidence
  remains a pending dependency rather than a verified exclusion.

Steam component failures have independent deadlines. A deferred missing schema
must not hold up review summaries/global percentages or erase known definitions.
Failure evidence records the requested Steam app separately from the app owning
retained payloads; retargeting cannot reuse another app's old evidence. Omitted
schema arrays stay unknown; only an explicit empty array proves zero.

Data Health distinguishes worker retries, deferred gaps and on-demand lookup or
metadata failures. A retry deadline alone does not establish a queued job.
Counts represent request records, not titles. Component evidence is
not double-counted alongside its job. Error details are allowlisted; payloads
and credentials remain private.

The operational migration repairs settled errors, retires verified IGDB alias
jobs and releases already-demanded jobs affected by the corrected DLC, TVDB and
critic validators. It preserves active leases, provider restrictions, published
credits, private tracking and selected/protected artwork.
