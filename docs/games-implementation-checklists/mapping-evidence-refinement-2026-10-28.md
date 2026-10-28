# Mapping evidence and release-year refinement — 28 October 2026

Follow-up to the completed beta review: release year is supporting identity evidence, not a mandatory gate when independent facts establish the game. An early-access year can differ normally from a full-release year. Display uses the normal/full release; matching retains the underlying dated evidence. Deployment and authenticated acceptance are pending below.

## Implementation

The ordinary complete title/year path stays first. Only unresolved, completely retrieved candidate pools enter bounded deeper checks: at most four exact-name candidates, four seconds per title and eight seconds per discovery page. Provider timeouts and deferred checks return retryable outcomes; ready matches in the same batch publish immediately. Durable discovery resumes unresolved work with bounded retry times rather than caching a transport error for a day or discarding the completed prefix.

Corroboration requires the exact Steam game title and developer agreement with IGDB, together with independent direct IMDb credit evidence and one of: a distinctive full character name in the store description for an undated game; multiple cast/crew names in an official game announcement; an exact actor/character reveal; or an actual early-access availability announcement in IMDb's year followed by a known normal release. Future launch plans, delays and cancelled announcements do not establish availability. News reads use the fixed Steam API and official announcement/release feeds, with bounded payloads and one historical window when relevant. No arbitrary website fetches or paid/restricted IMDb fields are required.

This uses public source evidence acquired independently before binding. It never validates a proposed mapping using credits already imported through that mapping. Namesake/edition/unknown competitor checks, complete candidate retrieval and canonical-root checks remain in force. No manual mappings or title-specific repair seeds are introduced. Shared public support snapshots carry fingerprints, expiry and dependencies; changed support invalidates affected automatic evidence. Acceptance locks and validates the consulted snapshots through commit. Reviewed decisions and private tracking remain separate.

Normal release projection ignores explicitly early-access/alpha/beta/cancelled rows, retaining the original records for matching. Unlabelled historical normal releases remain valid; a later full-release port does not replace an earlier normal release. Early-access-only evidence does not invent a full-release year. Newest browsing uses normal release events ordered by date, checks each event against the game's canonical normal release and retains raw event offsets for bounded paging. Popular browse date filters admit release-date candidates before the same canonical post-filter. Existing protected dates remain protected.

Provider references: [IGDB release dates and statuses](https://api-docs.igdb.com/#release-date), [Steam news API](https://partner.steamgames.com/doc/webapi/ISteamNews).

## Evidence and verification

Read-only real-provider probes now resolve Evolutis: Duality (Steam 1395380, character Chelsea Williams and matching studio), Emberville (2295170, official multi-person cast announcement), Abandon Ship (551860, actual 2018 early access and 2019 normal release), and Starfinder: Afterlight (3245640, Neil Newbon/Preach reveal). These establish four concrete previously unresolved links without using release-year guesses. Comanche's undated competing port and Roger & James in They Came from Below's missing exact source identity remain unresolved in the probe; no source completeness is invented.

Focused source/matching/projection units passed 72 tests at the first checkpoint. The broader mapping/enrichment/metadata run passed 90 tests, including disposable PostgreSQL identity and source-change regressions; these selections overlap. After review corrections, 65 focused units and 31 mapping/discovery PostgreSQL tests pass, including future launch plans, full-release filtering/paging and retained ready progress alongside retryable gaps. A failed benchmark fixture omitted a company-credit edge ID; it was corrected before successful measurements. Raw logs and provider payloads remain in ignored runtime scratch. Tests use only isolated disposable databases.

The final focused selection passes 76 unit tests. Sol approved in three rounds with gpt-6.1-sol on high effort. All three findings were fixed: future launch plans versus actual availability; consistent full-release filtering/order and cursor policy; and isolation/bounded retries for optional deep failures. No findings were declined; usage was not exposed. The reviewed backend/scripts diff grew from approximately 400 insertions/35 deletions to 538 insertions/53 deletions including the affected callers, regression tests and benchmark fixture.

## Controlled performance

Before/after uses the existing benchmark and isolated PostgreSQL. All three profiles retain the fast path's six cold reads, six writes and two simulated provider calls; warm reads remain three with zero writes/provider calls and 120 response bytes. Curated cold/warm times are 161.75/8.53 ms before and 160.13/8.74 ms after. Exact result identity is unchanged.

The new undated deep case costs 14 cold reads, 18 writes and four simulated calls, versus five warm reads, zero writes/calls and 125 response bytes. Curated cold/warm times are 261.48/9.94 ms. This is the extra work reserved for an unresolved identity, not a new cost on ordinary matches. Provider delay is 50 ms; these are controlled backend samples, not production percentiles or live upstream timing.

Cached person cards/status/100-work captions retain exact fingerprints, counts and payloads: 10/4/6 reads, 10,406/446/7,449 bytes, zero writes/provider calls. Curated warm medians are 101.77→101.62, 10.09→9.00 and 32.27→31.36 ms respectively. No universal speedup is claimed.

Full-release browse was additionally compared against the exact committed backend in a separate disposable source directory with the same synthetic data/harness. Both arms cost 13 cold reads, 16 writes and three provider calls; warm reads are four with zero writes/calls. The final curated cold/warm times are 221.69/14.79 ms versus 298.81/37.00 ms in that baseline diagnostic arm. Membership/order intentionally changes to normal release instead of early access; opaque cursor bytes also change with the new policy. This is correctness with comparable bounded work, not identical-result optimization. Live IGDB range probes returned 65/69 unique canonical candidates per 100 raw release events in the inspected 2019/2021 pages. Duplicates/later ports are skipped while raw offsets and the outer cursor's seen set remain authoritative.

Deployment and authenticated desktop/mobile results remain pending.
