# Game detail loading and price performance

Baseline: `ef86a0b5`. No database migration is required.

## Changes

- Missing primary information/artwork, ratings, Steam price, completion estimates and PC requirements reserve loading placeholders. Existing content remains usable during refreshes. Terminal failures settle to a retry state rather than an endless skeleton.
- Optional requests start after paint without the previous one-second timer. Server retry intervals of one or two seconds are honored; provider cooldowns remain intact.
- Ready server-rendered optional data avoids duplicate browser reads. Cold primary publication retries an earlier unavailable optional read after its in-flight response finishes, so newly established identities are picked up safely.
- Metadata status can include changed public primary content in the same response. Revision matches retain the compact status response. Private tracking state is not republished.
- Committed price admission wakes the existing durable worker immediately. Two regional jobs can progress concurrently; durable leases, provider budgets, cooldowns and cancellation cleanup still apply. Successful full batches drain without another idle tick.
- ITAD current-price and historical-low requests overlap after the verified app lookup, through the existing provider budget. Failure cancels and awaits the sibling request.
- DLC reads begin when the tab is exposed, instead of competing with initial content after 1.5 seconds.

## Verification

- 718 frontend tests pass; type check reports zero errors/warnings; production build passes.
- 63 focused backend tests pass: metadata (21), regional offers (13), idle worker wake (1), ITAD parsing/concurrency/cancellation (5), and provider adapters (23).
- Actual authenticated game page checked on desktop/mobile, including a reload from 900px scroll, repeated warm reloads, and back navigation through the authenticated profile. Scroll was preserved and no horizontal overflow or stuck skeletons was observed. A temporary fixture rendered the actual cold skeleton components on both viewport sizes; it and its network overrides were removed.
- Local authenticated warm-page browser measurement: TTFB 309ms, FCP 388ms, LCP 428ms, CLS 0. After cached content settled, reloads made no duplicate ratings, regional offer, time-estimate, requirements or DLC requests; the achievement catalogue still refreshed independently.
- Baseline browser inspection confirmed optional requests beginning around 1,725ms, after DOM content loaded around 715ms. This diagnosed the one-second startup timer. Baseline and final browser samples differ in cache warmth and do not establish a percentage speedup or production percentiles.
- Existing `benchmark_search.py --case game_details_cold --case game_offers` ran before and after, with 24 samples per arm and a repeated final arm. Result fingerprints, response payload sizes and provider counts match. Warm regional offers remain five SQL reads and zero provider calls; the other primary-detail profiles remain 35 reads. The first cold primary profile varied between 39 and 42 reads as asynchronous primary/character worker checkpoints overlapped measurement. Its variable latency/count is not evidence of an endpoint speedup. These cases exclude browser timers, the worker's idle delay and real provider latency; focused tests establish immediate worker wake and bounded request overlap separately.
- Raw benchmark outputs remain in ignored `.venv/` files.

Sol-loop: GPT-6.1 Sol approved in one round with no findings. Reviewer usage metrics were not exposed.
