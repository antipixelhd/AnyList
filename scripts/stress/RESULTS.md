# AnyList performance evidence

Work remains on `codex/anylist-stress-test`; beta/production are unchanged.
The aggressive 500-active-user workload is **not qualified**. The measured
ceiling is aggregate CPU, mainly API work, with sync queues growing under load.
There is no evidence that replacing PostgreSQL/asyncpg would solve it, or that
external provider quotas are the only remaining limit.

This record replaces five interim reports. Their detailed observations remain
in Git history through `4be084dc`; operator instructions are in [README.md](README.md).
Raw results and synthetic sessions stay ignored under `.cloud-runtime/anylist-stress/`.

## Retained changes and evidence

The implementation keeps focused SQL reads, linear account preparation, batched
identity/membership writes, atomic credit/link publication, stable lock ordering,
bounded sync admission, transactional revision shards, combined private page
context, indexed local browse and optional supervised API readers/catalogue work.
It retains provider admission, result policies, private viewer isolation and
optimistic edits. Reverted mapper gating and the slower full-page context split
are not active.

Controlled PostgreSQL comparisons from `ab6cc9fa` (cursor executions;
executemany counts once, savepoints included):

| Operation | Before SQL | After SQL | Before Python CPU | After Python CPU |
| --- | ---: | ---: | ---: | ---: |
| 100 cold identity publications | 1,200 | 400 | .359s | .172s |
| 100 warm identity publications | 800 | 300 | .297s | .109s |
| 20 empty season receipts | 200 | 12 | .109s | .031s |
| Import 100 cached films, new memberships | 506 | 308 | .250s | .078s |

Semantic fingerprints matched; provider calls were zero. Repeated warm imports
retained 206 SQL executions and showed no demonstrated wall-time improvement.
An overlapping-stack diagnostic placed SQLAlchemy on 65.3% of primary CPU
samples, season reconciliation on 15.4%, and asyncpg on 4.7%. These are not
exclusive overhead percentages or grounds for changing the stack.

`2763ad55` reduced ordinary authenticated page initialization from five backend
calls/20 SQL statements to one call/five SELECTs. Session revocation and fresh
settings still consult PostgreSQL. Full pages include notifications; profile
fragments use essential context. Splitting every full page into two calls/seven
statements regressed loaded p95 about 14–15% and was rejected.

`96b6decd` aligned popular/trending ranking expressions and exclusions with
PostgreSQL indexes. A default first-page plan on the VPS improved about 63ms
to .4ms; the 6,000-title local median improved 15–16ms to 1.1ms with matching
ordered identities. This is not a universal browse speedup: deeper offsets,
viewer policies and external ranking overrides may still use broad plans.

The final 40-second-per-level comparison in that commit used the same restored
snapshot, two readers, external catalogue owner, one sync slot, 10+10 SQL pool
per process, 16 generator pools, 3 CPU cores and 5 GiB. At 80/160/500 active
users, reference → candidate p95 was .194→.169s / 1.635→1.474s /
15.019→15.011s. At 500, successful requests/s was 52.16→55.74 and errors
406→374 (13.67→12.00%); this remained overloaded. Another repeat worsened
160-user p95, so these are modest run-specific gains, not a capacity guarantee.

`mt095_metadata_revision_shards` removes singleton revision-row contention;
`mt096_browse_ranking_indexes` supplies the browse indexes. Apply migrations
before updated workers. Revision readers and mt095 must move together; coordinate
any downgrade with application rollback. Both were exercised on disposable
PostgreSQL, including migration and concurrency checks; no deployment was migrated.
PostgreSQL's [row-lock compatibility and ordering rules](https://www.postgresql.org/docs/16/explicit-locking.html)
explain the retained `FOR NO KEY UPDATE` publication and sorted identity writes.

## Consolidation comparison, 2026-11-17

Reference: `4be084dc`. Candidate: this consolidation. Both restore the same
stopped PostgreSQL snapshot containing 500 accounts, 200 screen entries/account,
24 games/account, 6,000 titles and 2,000 games. Matching production frontend
builds issue authenticated HTTP through the real proxy/API/readers/SQL paths.
Normal mapper, metadata and statistics jobs run alongside manual edits and full
Jellyfin pulls. Providers return synthetic 100ms responses; Jellyfin returns
48 films over loopback HTTP with 60ms delay. Live quotas, CDN images and browser
paint are outside this measurement. Five-minute scheduled account bursts are
not established by these short runs.

Settings match the earlier final comparison: two readers, external catalogue,
one sync slot, 10+10 pool per process, 3-core/5-GiB lab quota, lower scheduling
weights and host-side deployment guard. Profiling is off. Mean think time is
3.5s, twelve primed keys, 4% varying-key journeys and 16 generator pools.
Varying keys do not guarantee misses. Successful req/s includes request drain;
p95 includes failures; sync completions use the stage deadline.

| Active users, 35s/level | Reference p95 | Consolidated p95 | Reference successful req/s | Consolidated successful req/s | Errors, reference / consolidated |
| --- | ---: | ---: | ---: | ---: | ---: |
| 80 | .2192s | .2653s | 39.64 | 39.36 | 0 / 0 |
| 160 | 1.4547s | 1.4823s | 61.32 | 60.80 | 0 / 0 |
| 500 | 15.0128s | 15.0126s | 48.24 | 48.05 | 450 / 454 |

Both arms reached about 2.96 CPU cores at 160/500. At 160 the consolidated
primary used .630 cores, readers 1.132 combined, PostgreSQL at least .478,
frontend .334, catalogue .155 and generator .166. Surviving PID-pair counters
undercount short-lived processes. Memory stayed below 1.6 GiB. This pass does
not raise capacity; the shorter 80-user tail was worse and required a repeat.

Longer diagnostic repeats: 70s at 80 users and 60 serial samples/operation,
restoring the same snapshot for each backend while holding the consolidated
frontend constant. Run order and all results:

| Backend | p95 | Successful req/s | Lab CPU cores | Outside CPU cores |
| --- | ---: | ---: | ---: | ---: |
| Reference | .1932s | 38.58 | 1.878 | .232 |
| Consolidated | .2111s | 38.50 | 1.955 | .320 |
| Reference control | .1756s | 38.65 | 1.917 | .412 |
| Consolidated control | .1597s | 38.57 | 1.935 | .251 |

Every repeat has zero HTTP errors and completes 20/20 admitted syncs. The tail
difference reverses; there is no consistent latency regression or demonstrated
speedup. Candidate lab CPU is 1–4% higher than the reference range, with varying
host competition and background scheduling; this does not prove exact resource
equivalence. Successful throughput differs by less than .4% across all four.
Serial screen-page medians range 53.2–53.7ms reference versus 53.5–55.3ms
consolidated; warm screen details 29.0–30.1ms versus 29.9–30.9ms; game pages
59.0–59.3ms versus 60.5–61.5ms. Short shared-host samples do not guarantee
unchanged tail latency.

All completed comparison arms pass reader replacement, active-sync preservation,
committed edit visibility, cross-viewer note isolation, reader write rejection,
catalogue single-owner/failover, queued publication and warm database replay
without additional IGDB calls. Timed SQL and host/process observations are
separate scopes from shutdown/recovery telemetry.

Artifacts: `consolidation/{before,after,repeat-before,repeat-after,reference-control,candidate-control}/`
contains results, process/host samples, effective runtime configuration, SQL,
serial latency probes and recovery outcomes. All deployment health probes
returned 200, worst .732s; none reached the guard's stop threshold. Normalized
source hashes confirm all 16 changed application modules match the final running
candidate. The initial shell line-ending failure,
unbounded seed timeout and accidental old-source startup were discarded before
measurement. Fixture setup now commits bounded batches before application
workers start; both arms use the same resulting snapshot, excluding setup time.
All benchmark/guard units stopped successfully and the verified disposable VPS
lab was removed after collection. Local verification servers, browser and test
database were also closed.

## Consolidation contracts and verification

`application.create_app(role=...)` composes both roles with one lifecycle.
Only primary owns recovery/schedulers/supervision; both close their clients.
`@reader` declares eligible GETs beside the route. Undeclared/wrapped routes,
writes and configured global limits stay primary. Router order and explicit
parameter constraints are enforced from the actual route registry; invalid
constraints fail during construction. This replaces a separate path allowlist
and child lifespan replacement. Trusted forwarding uses the primary ASGI client
identity, with loopback-only reader trust. Global-limit detection uses owned
public configuration instead of SlowAPI private collection attributes.

`CollectionMembershipBatch` owns importer allocation, successful-file and date
state; the importer still owns savepoints/commits. Publication's database-only
callback is named `publish_in_transaction`; request-owned search/browse session
contracts are stated at their entry points. SSR context selection is a small
directly tested helper, preserving fallback authentication and per-request data.

- All 126 before/after search samples match on ordered identities, rows, totals,
  pagination/pending state, payload bytes, SQL reads/writes and provider calls.
- 54 focused backend checks pass, including real PostgreSQL reversed-order
  imports, invalid items/add-dates, no-op show races and queued cancellation.
  The five sync race cases now accept the existing CI disposable test URL rather
  than requiring a particular developer lab. Another 14 architecture/role/rate
  checks pass after the final factory move.
- 15 SSR/notification/lifecycle checks pass. Brittle middleware/component source
  slices were replaced with behavior checks; meaningful privacy, transaction,
  cancellation, index-compatibility and recovery regressions remain.
- Production frontend build and typecheck pass (0 errors/warnings). Real local
  authenticated title/profile checks cover desktop/mobile, first nonzero-scroll
  reload, repeated reload, cold/warm HTTP/font caches and back/forward. Scroll
  positions and visible heading dimensions match after reload; startup adds no
  duplicate notification/preference calls. Full and partial HTML remain
  `private, no-store`; partials omit notification hydration. Synthetic image URLs
  are intentionally blocked; this does not validate live CDN/image behavior.
- Full frontend suite: 1,116 checks, seven unchanged contributor/import failures.
  Full backend discovery: 3,694 checks, one existing parent-proposal failure,
  68 existing SQLite/fake-fixture errors and 1,244 skips without opt-in DB URLs.
  Three additional opt-in details failures/errors reproduce on the pinned
  reference. These suites are not green; no new failure remains in the compared
  sets. Changed backend files pass Ruff. AGENTS.md gains no new rules.

The one-off alternate-backend account preparation launcher was removed; reusable
benchmarks remain. Public metadata projections and stronger sync process
isolation remain future work, outside this consolidation and any beta merge.
