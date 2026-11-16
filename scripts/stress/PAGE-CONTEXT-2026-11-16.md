# Ordinary-user page overhead

Baseline: `8d32f587`, branch `codex/anylist-stress-test`. No beta/production
deployment or schema changes. Raw observations are ignored under
`.cloud-runtime/anylist-stress/pages/` and `pages-*.json/log` alongside it.

## Implemented read boundary

Previously every authenticated page made five shared backend calls: session,
settings, tracking preferences, profile preferences and the full notification
inbox. The last call also read operational delivery queues, hydrated up to 200
complete sync jobs and committed notification cleanup. The browser then repeated
notification and popup-preference reads during startup.

`/auth/page-context` now authenticates once, reuses the joined profile, reads
settings/global settings/tracking preferences together, and reads only the user
notification feed. It executes **five SELECTs instead of twenty SQL statements**
across the previous five requests on the normal fixture. It shares the existing
settings/preference/review projections; credentials are not added to schemas.

The page hydrates the permitted notification snapshot and the two popup flags.
It does not embed the complete account settings or session response. Notification
JSON escapes `<`, is checked against the rendered viewer, and is consumed once
per document. Late responses are guarded by identity/generation and cancellation.
The browser avoids duplicate startup reads, keeps mutation refresh immediate,
polls visible pages every minute, and debounces focus/visibility wakes for 15s.

`/tracking/notifications` serves badges and attention prompts without the
operational queues. Only an inbox subscriber requests `/tracking/recent-events`;
a partial request cannot overwrite a full inbox response. Sync failures use
focused job columns, excluding history/statistics JSON. Import alerts, priority
overrides, the existing 100-item window, pending events and preference filtering
remain consistent with the inbox. Retention is a read predicate for the small
feed. The inbox still persists cleanup; extending retention retires expired
quiet notices transactionally so they cannot reappear.

Session revocation and fresh settings still consult PostgreSQL on every context
request; no cross-request private cache or longer authorization TTL was added.
The endpoint and authenticated HTML are `private, no-store`; public assets keep
their existing caching. Legacy endpoints remain available, including the old
frontend fallback if the context request fails.

## VPS comparison

400 synthetic accounts, **200 tracked screen entries per account**, 24 game
entries, 6,000 titles, 2,000 games. Real authenticated HTTP through matching
production frontend builds, real PostgreSQL migrations and real API/job code.
Two API readers, external catalogue process, 10+5 connections per process, one
media-server sync slot. A private network namespace, 3-core CPU quota, 5 GiB cap
and lower scheduling weights protected the existing deployment. Profiling was
disabled. Mean think time 3.5s, twelve primed keys and 4% varying-key journeys.

Order: baseline fresh -> candidate fresh -> candidate reused -> baseline reused.
Fresh phases lasted 50 seconds per level; reused phases 40 seconds. Each build
used its own database. Reused arms retain their respective prior caches/history;
they are not a snapshot-identical crossover. Background mapper/metadata/statistics
jobs and manual full pulls remained enabled. The account scheduler's five-minute
timer is not established by these short process lifetimes; this is not a new
scheduled-account burst benchmark. Synthetic Jellyfin still returns 48 films.

Mixed-request p95, seconds:

| Active users | Baseline fresh | Candidate fresh | Baseline reused | Candidate reused |
| --- | ---: | ---: | ---: | ---: |
| 80 | .348 | .208 | .332 | .182 |
| 120 | .930 | .507 | .998 | .579 |
| 160 | 3.506 | 2.102 | 2.894 | 1.769 |
| 240 | 10.754 | 9.905 | 8.772 | 8.479 |

At 80-160 users, p95 improved approximately 39-45% in both comparisons. In the
fresh 80-user phase, primary API CPU fell **.536 -> .390 cores** (27%), its
observed SQL rate **320.8 -> 226.8/s** (29%), and the whole benchmark used
**2.183 -> 1.945 cores** (11%). Successful traffic increased 38.22 -> 38.73
requests/s. Reader CPU was essentially unchanged (.668 -> .672 cores).
These SQL counters describe the primary API, not all database clients.

This is primarily reduced latency/headroom, not a proportional throughput gain:
successful request rates at 120 users were 51.46 -> 54.22/s fresh and
52.04 -> 54.79/s reused. At 160 they were 56.59 -> 59.74/s and 58.93 -> 59.70/s.

At 240, both builds were overloaded. The fresh candidate had no HTTP errors
versus 37 baseline timeouts, but completed fewer requests per second
(41.30 versus 53.24). The warm pair had no HTTP errors, 46.70 versus 48.16/s,
and only a 3% p95 improvement. Do not claim a 240-user capacity increase from
the absence of errors. Sync throughput is also mixed: the candidate completed
2 versus 4 admitted jobs at 160 fresh (3 versus 5 reused), but 11 versus 0
at 240 fresh (16 versus 9 reused). Increased foreground throughput changes
competition for the same CPU and sync slot.

At 160 fresh the complete lab consumed almost its three-core quota. The
candidate readers used about 1.105 cores together, primary .608, PostgreSQL
.478, Node .287, catalogue .193 and generator .260. The generator itself
rose to .600 cores at 240 fresh. This is not an API-only resource allocation.
Provider timings/quotas are synthetic, and the closed-loop driver slows its
request generation when responses slow. There is no demonstrated 1,000-user
capacity or provider-only ceiling.

## Remaining work with the greatest reach

The next target is reducing warm title/game/search/list query construction,
hydration and composition in the read workers: prepared, versioned public
projections plus focused fresh private state. This change removes repeated
page-shell work; it does not yet implement those broader public read models.
Ordinary detail/profile/list pages still incur their own queries and browser
follow-ups. Moving the same work between more workers cannot eliminate its CPU.

Bounded durable account workers and delta-first reconciliation remain valuable
for sync isolation/fairness. Their benefit for 100-500-item accounts still needs
its own scheduled-cycle workload; it must not be represented by the earlier
10,000-item preparation speedup or by simply reducing completed sync work to
improve foreground timings. PostgreSQL replacement and more threads remain
unsupported as the primary fix by these observations.

## Verification and limits

- Seven disposable-PostgreSQL page-context checks pass, including five-read
  composition, legacy parity, retention, failure recovery, narrow job columns,
  current settings, viewer isolation, revocation and artwork URLs.
- Existing notification/preference neighbors, 43 auth/credential/unit controls,
  11 profile-image checks and 13 browser-lifecycle/SSR checks pass in focused
  runs. The existing tracking-preference dictionary assertion is stale and was
  reproduced on the pinned baseline before excluding it from the focused run.
- 18 paired `benchmark_search.py` list-window/activity samples at 200 local
  titles match actual `result_identity`, payload bytes, order/counts, pagination,
  pending state and SQL/provider costs. They are compatibility controls, not a
  measurement of the page-context gain.
- Full frontend discovery: 1,114 tests, seven failures, exactly the same seven
  on the 1,109-test baseline. Full backend discovery: 3,682 tests, 68 legacy
  errors, the known game-parent assertion and an environment-sensitive Steam
  assertion. The latter passes without a Steam key and fails on both baseline
  and current source with a synthetic configured key. No new unresolved
  application failure was identified. This is not a green full suite.
- Actual authenticated desktop/mobile details and profile pages rendered;
  nonzero first/repeated reloads and back/forward were checked. Browser requests
  confirm no immediate notification/preference duplicate outside the inbox and
  a full inbox request on the inbox page. Controlled cold fonts transferred
  22-40 KiB each; warm reads transferred zero. The mobile profile retained
  scroll 400px and the same 151.6875 x 29.4375px heading dimensions across cold
  and warm reloads. No browser errors appeared. Data/providers were synthetic.
- Final production build and source passed VPS HTTP calibration, including a
  cached full sync. All 375 uploaded file hashes matched the local manifest.
  This final smoke also covered the later retention/legacy-wrapper preservation
  and private HTML headers; the timed runs predate those last compatibility
  changes. Their ordinary read cost is unchanged.
- 334 deployment guard probes returned 200, worst 115ms. Both labs and local
  test processes/container were removed after artifact collection.

The repeat exposed a monitor defect: it retained the old `pids` file when a
reused run started. Primary/frontend/Jellyfin per-PID attribution is therefore
missing in those two warm traces; use only their cgroup totals and recorded
latency, not invented process splits. Fresh-run process attribution is valid.
The monitor now refreshes PID roles on every sample. Three focused metrics checks
pass, and final VPS smoke observed the correct primary/frontend/reader roles.

The controlled sequential ASGI microbenchmark (`scripts/benchmark_page_context.py`)
also matches complete response fingerprints with empty and 200-job histories,
20 -> 5 statements and no provider requests. Its Windows CPU timer is coarse;
individual zero-CPU samples are not zero-cost requests. Prefer the VPS CPU
measurements above. It excludes network/browser/worker CPU and does not replay
the old frontend's parallel request scheduling.
