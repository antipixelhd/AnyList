# Games background mapping

Games use the existing canonical metadata, credit, person-discovery and company
jobs. The automatic runner feeds those jobs; it does not maintain another set of
bindings or merge rules. `game_work.py` arbitrates provider starts for every
database-backed `ProviderHTTP` client, including details, search and Steam sync.

## Architecture choice

Three approaches were considered with Sol:

| Approach | Assessment |
| --- | --- |
| Separate mapper daemon and limiter | Duplicates ownership and cannot reliably give interactive calls the shared API budget. |
| Replace every stage with a universal job broker | Requires rewriting tested publication, evidence, lease and retry contracts. |
| Canonical jobs, shared provider admission, automatic relevance graph | Selected. Adds one scheduling boundary while retaining the existing mapping and correctness rules. |

Automatic work uses one resumable stage across processes. It bypasses FIFO
service semaphores, but takes the same canonical database job leases. Normal
workers exclude automatic-only demand; a foreground subscriber promotes the
existing job instead of admitting duplicate work. Scheduler-driven person,
company and game-credit acquisition uses one page per turn. Successful turns
continue immediately, with durable timestamps ordering competing continuations.

## Priority and fairness

| Priority | Work |
| --- | --- |
| 0 | Direct details, resolution and search |
| 1 | First useful contributor bulk publication |
| 2 | User-triggered continuations, validation and enrichment |
| 3 | Connection synchronization |
| 4 | Scheduled retries |
| 5 | Automatic mapper |

Direct demand can boost the first provisional bulk job into priority 0 alongside
cold details. Priority 1 is its fallback admission class; lower-priority classes
1–5 share two background positions and cannot occupy the foreground reserve.

Within a provider priority, admission selects the least recently served user,
then that user's least recently served entity. Opening extra pages does not buy
extra user turns. Public cache subscribers and canonical jobs publish durable
demand, so a later subscriber can promote already queued shared work. Candidate
queries retain their subscribers' context and batch within a job/priority group.
Within an entity's turn, candidates needed for the next source-prefix publication
precede later siblings. Each physical query carries only its actual subscribers,
so a sibling cannot inherit that earlier position or another query's promotion.

IGDB retains the 300 ms start interval with three shared in-flight positions.
**Background work may use positions 0/1; position 2 is reserved for priority 0.**
Foreground work takes its reserve first, then may borrow either background
position. Unsent work never reserves a future start or slot. A foreground call
therefore need not wait for a background request to finish, even with both
background positions occupied. Shared rate limits, cooldowns and existing
foreground work still apply. [IGDB documents four starts per second and eight
open requests](https://api-docs.igdb.com/#rate-limits); this service stays below
both limits. Existing higher-priority requests share their user/entity turns.
Other catalogue providers retain
their concurrency, minimum start intervals, cooldowns and daily/monthly budgets.

One local dispatcher per provider/database checks the shared queue while there
are local waiters. Waits release SQL connections. Short database critical sections
select starts; transaction-owned advisory slots remain held through HTTP and are
released on cancellation or process loss. Waiting tickets expire unless renewed.
Automatic admission yields after 500 ms when higher-priority work is queued;
its own healthy in-flight requests and rate spacing do not cause it to cancel
and restart. Yielding does not record failed
or negative evidence. Providers still enforce their network timeouts after admission.

## Automatic relevance

The durable graph contains unique games, unique people/companies and game-to-
contributor edges. Interests are idempotent per user/source/game, with separate
counts for lists, views, browse highlights and catalogue exploration. Counts
deduplicate users within each class. The strongest class wins; accumulated
counts weight fair automatic turns within that class. Initial stages receive a
turn before repeatedly advancing an already checked career.

Lists are seeded in indexed batches of 250 and reconciled every ten minutes.
The scan covers main entries, listed expansions and favorites independently.
List edits and detail/browse visits also submit local observations after the
HTTP response. Views remain relevant for 30 days; browse interests for two hours.
Unchanged observations renew at most hourly. Short SQL transactions serialize
interest/edge changes with their cached weights, preserving concurrent additions
and removals. Canonical roots publish before their scheduling hints are updated.
Highlights refresh one category at a time. The deeper catalogue frontier is
bounded to 100 due games, advances ranked pages first, then uses an IGDB ID
keyset to reach beyond ranked feed windows. Its durable position survives process
restarts and is independent of expiring interactive pagination cursors.

Each game acquires compact primary facts and canonical title credits, then
selects eight distinct cast people, eight crew and two companies. People appearing
in both groups produce one queue item. Contributor weight accumulates from
distinct relevant games; a mapped career does not recursively enqueue every
career game for full expansion. Existing protected fields and identity ownership
checks remain in the canonical ingestion path. Optional galleries and characters
are not acquired by the game expansion stage.

Completion schedules the next check from evidence expiry. Expired relevant
games/contributors become due again. Provider errors retain their existing
backoff; yielding priority does not increment attempts. Removed relevance updates
shared contributor weights, including contributors removed by a refreshed game.

## Controls and visibility

`GAME_BACKGROUND_MAPPER_ENABLED=true` enables the runner for this beta patch.
Setting it false is an operator-level disable; it does not disable foreground
mapping. The admin Game mapper tab persists Start/Stop independently in the
database. Stopping prevents new automatic HTTP starts; an already transmitted
requests may publish, and work also needed by a user remains eligible.

The admin endpoint reads scheduling aggregates, never filmography payloads.
The runner wakes on local observations and controls, with a bounded cross-process
catchup check; successful stages drain without an idle sleep. The mounted visible
page polls every five seconds. Hiding or leaving it aborts
the request and timer. Category counts exclude the open-ended catalogue frontier.
Completed-check counters include successful repeat checks; games checked is a
distinct game count.

## Verification

Use disposable PostgreSQL only. `test_game_work` covers ranking; the
`test_game_background_pg` suite covers promotion, idempotent interest counts,
contributor deduplication/budgets, cancellation, the one-lower-request bound,
stop, expiry and admin authorization. Existing provider, bulk, source and
portfolio tests retain mapping/failure coverage.

Run `scripts/benchmark_search.py` before/after for `game_person_cold`,
`game_company_cold` at 50/750 ms, competing/shared people pipelines and cached
person/company/details/browse/search controls. Compare canonical fingerprints,
payloads and logical/physical provider calls as well as time and SQL. Controlled
HTTP/database measurements exclude browser paint and upstream variability.

The implementation was compared with a separate daemon and a universal job
broker in an architecture review, followed by five implementation checks with
`gpt-6.1-sol`, configured at high effort. Final verdict: approved. Token usage
was not exposed. The final check also approved canonical-root publication before
feed-locked scheduling hints and serialized interest/weight updates.

| Finding | Severity | Outcome |
| --- | --- | --- |
| S1: FIRST_WORKS could bypass the lower-priority permit | Blocker | Fixed |
| S2: cancellation could leak an admitted slot | Should | Fixed |
| S3: admission could overwrite a concurrent cooldown | Should | Fixed |
| S4: discovery could spin before its retry deadline | Should | Fixed |
| S5: ephemeral fairness state needed bounded independent cleanup | Should | Fixed |
| S6: membership cleanup needed bounded scans and a target index | Should | Fixed |
| S7: automatic identity work could escape through backfill | Should | Fixed |

No findings were declined. The initial tracked-only review stat was 24 files,
+174/−158; it excluded the new implementation files, so a comparable full
before-review size is unavailable. The completed change spans 38 files including
tests, migration, admin UI and guidance; its exact final size is in the commit.

Final checks: all 28 new tests passed; build and type check passed; the full fresh
Alembic chain reached `mt080_game_background_runner`. Authenticated desktop and
mobile checks covered Start/Stop, no overflow and actual visibility changes:
the resource count remained unchanged while the tab was hidden. Neighboring
suites retain two failures also reproduced at the starting commit: the gallery
viewer test's missing `artworkActions` fixture and the archived-performer detail
test's empty people projection. Live beta/provider latency was not measured.

## Initial benchmark record (superseded scheduling policy)

The baseline is the complete starting tree at
`73331dbda5f820a2c00de8fa9c7e80fbafddc4c5`, replayed in an isolated local
archive. Both trees used disposable PostgreSQL and identical controlled provider
fixtures. Timings below are individual lab samples, not production estimates.

| Workload | Before | After | Correctness / upstream cost |
| --- | ---: | ---: | --- |
| 60-work person, 50 ms providers | 12.87 s | 13.59 s | Same complete fingerprint, 94 physical requests / 120 logical IGDB queries |
| 60-work company, 50 ms providers | 1.08 s | 1.20 s | Same complete fingerprint and 3 requests |
| 60-work person, 750 ms providers | 57.54 s | 47.96 s | Same complete fingerprint and request counts |
| 60-work company, 750 ms providers | 3.10 s | 3.10 s | Same complete fingerprint and request counts |
| Two distinct 12-work careers, actual admission | 17.33 s | 25.51 s | Same identities and 36 requests; lower-priority concurrency 2 → 1 |
| Two shared 12-work careers, actual admission | 12.42 s | 17.15 s | Same identities and 24 logical IGDB queries; 18 → 19 physical requests |

Cold portfolio fixtures control the provider transport and bypass actual HTTP
admission. Their synthetic concurrency improvement does not establish live
rate-limited throughput. The competing/shared fixtures exercise real admission:
their longer background drain reserves the second slot for foreground work.
Shared first publication remained about 3.1–3.3 seconds for both careers; distinct
first publications changed from 3.30/5.54 to 3.06/7.07 seconds. One extra shared
physical batch reflects different collection timing; logical query count and
canonical membership are unchanged. SQL observation totals rise with drain time
because those fixtures poll every 20 ms; they are not per-route query budgets.

Cached controls retained every result fingerprint across all three curation
profiles. Curated warm medians: person 164 → 153 ms, company 136 → 120 ms, search
76 → 76 ms, details 52 → 77 ms, browse 22 → 27 ms. Person/company/search SQL and
provider counts are unchanged. Warm details add two reads and two pending-demand
writes; warm browse adds one read, with no writes. Neither adds provider requests.
The cold details journey retains two provider components; its controlled response
changed 329 → 422 ms and primary publication 353 → 453 ms. ASGI response timing
includes post-response observation tasks and does not measure browser paint.

The separate complete cast/character projection retained all fingerprints, with
zero provider calls: curated warm 34 → 45 ms, 17 → 18 reads and one additional
pending-demand write. Its payload changed by four status bytes, with every
performer, character and work-scoped association retained.

Raw JSON, SQL patterns, test logs and screenshots remain local under the ignored
`.local-design/search-browse` directory. No live user data or provider credentials
were used. Migration verification applied the actual full Alembic chain to a
disposable database; deployment databases were not changed by this patch.

### Follow-up: one scheduled portfolio

The initial pass did not establish non-regression for a single job through real
provider admission. A follow-up replay used the same pipeline fixture in both
complete trees, changing only `for index in range(2)` to `range(1)` in the fixture
to seed one pre-indexed 12-work career. Three sequential before/after pairs ran
at each provider delay, with fresh disposable schemas and no competing jobs.

| Provider delay | Median completion before → after | First publication before → after |
| --- | --- | --- |
| 50 ms | 5.328 → 5.797 s (+8.8%) | 1.042 → 1.628 s |
| 750 ms | 11.349 → 15.635 s (+37.8%) | 3.689 → 3.864 s |

All twelve runs retained the same complete fingerprint and 18 provider requests
(15 IGDB, three Wikidata; 24 logical IGDB queries). First-publication card counts
vary with batching, so those times do not compare a fixed number of cards.
At 750 ms the old single career reached two concurrent IGDB calls internally;
the new lower-priority limit allows one. At 50 ms both reached only one, so the
measured regression also includes uncontended scheduling overhead. These are
scheduled continuation measurements, excluding initial source acquisition and
browser paint. They establish a real single-job regression; it has not been
corrected by this documentation update. Raw results are `mapper-single-*.json`
and the fixture adapter is `.local-design/mapper-single-benchmark.py`.

### Regression fixes and reserved foreground capacity

The user approved two background calls with separate foreground capacity.
The current three-position policy above replaces the initial single-background-
position limit. Both background calls can continue while a foreground call starts
at the next allowed rate interval; they cannot occupy its reserve. This restores
parallel acquisition without admitting a FIFO queue upstream.

Matching retains complete independent exact/search branches. Fresh complete
Wikidata reverse batches serve subset turns, including verified empty results,
without renewing timestamps. Initial and contended turns retain four titles;
after its first turn a lone eligible career can fill a ten-query batch. Leased
competitors count; automatic-only jobs excluded from normal workers do not
disable the lone-user optimization. Collection waits are bounded at 50/75 ms.
Publication-prefix ordering applies after user/entity fairness, and physical
requests carry only their actual subscribers' scopes.

Admission now registers a ticket in one SQL statement, skips fairness-history
reads for a sole contender, upserts both turn keys together and avoids deleting
an already-deleted successful ticket again. A post-commit cancellation guard
releases a slot that could not be handed to its cancelled waiter. Automatic work
yields to queued higher-priority demand, rather than cancelling its own slow
batch after 500 ms.

| Single 12-work scheduled career, median of three | Before service | Regressed service | Current |
| --- | ---: | ---: | ---: |
| 50 ms provider responses, completion | 5.328 s | 5.797 s | 5.188 s |
| 750 ms provider responses, completion | 11.349 s | 15.635 s | 9.166 s |
| 50 ms responses, first publication | 1.042 s | 1.628 s | 1.046 s |
| 750 ms responses, first publication | 3.689 s | 3.864 s | 3.067 s |

Every single-career result retains the same canonical fingerprint and 24 logical
IGDB queries. Physical requests fall from 18 to 15: IGDB 15 → 14, Wikidata 3 → 1.
First-publication card counts depend on batching; these are database publication
timings, excluding initial IMDb acquisition, browser paint and real upstream
variability. The executable harness now supports `--pipeline-people 1`, `2` and
`3`; historical replay uses the complete starting tree and the documented
single-career fixture adapter.

| Additional workload | Before service | Regressed service | Current |
| --- | ---: | ---: | ---: |
| Two distinct careers, actual admission | 17.33 s | 25.51 s | 18.00 s |
| Two shared careers, actual admission | 12.42 s | 17.15 s | 12.03 s |
| 60-work person, controlled 50 ms transport | 12.87 s | 13.59 s | 11.50 s |
| 60-work person, controlled 750 ms transport | 57.54 s | 47.96 s | 30.84 s |

The two-career samples retain every participation identity and use 32 requests
instead of 36 for distinct careers, and 17 instead of 18 for shared careers.
The distinct-career drain is about 4% above the original single sample, with
durable fairness and reserved foreground admission. Current first publications
are 3.24/6.68 s for distinct careers and 3.99/4.13 s for shared careers; publication
sizes vary. Three distinct careers finish in 25.94 s, with first publications
at 3.09/5.81/9.20 s. No historical three-career timing is claimed.

Complete 60-work person acquisition retains its full fingerprint and payload,
with 76 requests instead of 94 and 120 logical IGDB queries unchanged. Reads
fall from 1,820 to 1,462 in the 50 ms fixture; writes are 1,038 → 1,053 against
the original tree, or 1,234 → 1,053 against the regressed service. These transport
fixtures bypass real HTTP admission, so their gains are separate from actual
rate-limited pipeline timings. Company membership, payload and three provider
requests remain unchanged, with 293 reads/316 writes versus 290/316 originally.
Single-sample timing varies; paired company controls are recorded separately.
Three sequential before/after company pairs at 750 ms resolve that variance:
median completion 3.261 → 3.186 s, first publication 1.160 → 1.161 s, identical
fingerprints and provider/write counts. Their raw files are
`mapper-company-control-{before,after}-{1,2,3}.json`.

The performance fixes passed five Sol implementation checks. P1 (should), a
cancellation during admission commit that could leak a slot, was fixed. After
the user approved reserved capacity, a separate architecture preflight and two
narrow implementation checks approved that revision and R1 (should), automatic
self-cancellation under its own contention. No findings were declined. The
configured model was `gpt-6.1-sol` at high effort; usage was not exposed.
The first follow-up review covered seven files, +116/−34; the final diff is
13 files, +433/−77, including regression tests, benchmark options and this guide.

Verification: 83 matcher/collector/scheduler unit checks passed, followed by the
33-check scheduler/PostgreSQL suite and three final automatic-pressure/Stop
checks. The latter confirm that four automatic requests survive their own slow
calls, yield to queued higher-priority work, and still obey Stop. Independent
dispatchers verify foreground admission while both background calls remain held;
additional checks cover reserve preference, borrowing, the three-position cap
and cancellation releasing all positions. All final portfolio fingerprints,
complete payloads and logical query counts match their starting-tree controls.
No schema changes were needed in this follow-up.

## Live Mercer and Activision check — 4 November 2026

Committed backend `0ab1455e` was uploaded to a separate VPS temporary directory
and exercised with actual IMDb/IGDB/Wikidata/Steam calls. Every case used fresh
disposable PostgreSQL data. Provider credentials stayed on the VPS. The deployed
checkout and application databases were not changed. Only the two reviewed
public mappings used by the saved benchmark were seeded: White Knight Chronicles
II → 7483 and Warframe: Tau → parent Warframe 2903.

| Foreground-priority cold works pipeline | Saved live reference | Current live runs |
| --- | --- | --- |
| Mercer, first 245 visible works | 2.861 s | 3.476 / 3.434 s |
| Mercer, 247 works | 4.999 s | 4.551 / 5.045 s |
| Mercer, all 257 reference works | 25.013 s | 27.290 / 23.522 s |
| Activision, final visible portfolio | 695 at 4.649 s | 693 at 4.910 / 4.921 s |

Mercer membership matches the saved 257 canonical IGDB identities exactly, with
no missing or additional works. The two foreground samples bracket the saved
completion time; they do not demonstrate a reliable live speedup. Initial bulk
publication is about 0.6 s slower than the saved sample. Live response variability
and different validation progress prevent attributing that difference to a
specific scheduling stage from these samples alone.

Activision's count change is explained by provider data. The saved raw IGDB
responses classify Spider-Man 3 IDs 200967 and 252200 as main games (`game_type=0`)
with no parent. Direct live probes now classify both as ports of 5178 (GBA and
Nintendo DS respectively). The current portfolio contains parent 5178 and every
other historical canonical work: exactly the old 695 minus those two ports, with
no additions. The existing company projection collapses ports into their parent;
the policy was not changed for this test. The full compatibility run received
1,213 participation records instead of the saved 1,214 and completed the company
job without an error. Both port participation records still exist upstream, so
that one-record source-count difference is separate from the two-work collapse.

The compatibility harness also ran without an interactive context, as older
saved scripts did: Mercer reached 257 at 30.071 / 25.729 / 43.904 s; Activision
reached 693 at 7.075 / 6.999 / 7.073 s. The foreground runs above seed requests
under `work_context(DIRECT, user)` to exercise current website priority admission.
All runs retain the same normalized membership. No job error codes were reported.

Each foreground Mercer run made six `ProviderHTTP.request` calls for IMDb (two
person-source connections), 20 IGDB, nine Wikidata and eight Steam calls, compared
with the saved six/17/10/eight. Activision made 14/15 IGDB calls. These counters
include concurrent validation admitted before stopping at the visible milestone;
they count provider-method invocations, not independently instrumented transport
retries. The first compatibility run intentionally observed five extra seconds
after the milestone, so its final counters are not directly comparable.

These are backend publication milestones with 0.5-second observation, not browser
paint or full accepted-title validation completion. Mercer still had discovery
validation queued after all 257 works were visible, just as in the saved report.
Live foreground contention against other website users was not simulated.
The final benchmark worker did not exit promptly during cancellation/teardown
after emitting both results; it was terminated from its verified temporary
working directory. That cancellation wait remains unprofiled and is not included
in publication timings. All temporary databases/roles were removed, and zero
remaining benchmark databases/roles were verified. Raw public
results, membership comparisons and the classification probe are stored locally
as `mapper-live-actual.log`, `mapper-live-repeat-{2,3}.log`,
`mapper-live-foreground-{1,2}.log` and `mapper-live-studio-probe.jsonl` under
`.local-design/search-browse`. The compatible harness adapters are under
`.local-design`; these raw artifacts remain outside Git.
