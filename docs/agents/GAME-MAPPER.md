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
1–5 share the single lower-priority in-flight permit.

Within a provider priority, admission selects the least recently served user,
then that user's least recently served entity. Opening extra pages does not buy
extra user turns. Public cache subscribers and canonical jobs publish durable
demand, so a later subscriber can promote already queued shared work. Candidate
queries retain their subscribers' context and batch within a job/priority group.

IGDB retains the 300 ms start interval and at most two in-flight requests.
**At most one request from priorities 1–5 may be in flight.** Unsent work never
reserves a future start or slot. A new foreground request can therefore encounter
only one already transmitted lower-priority request. Existing higher-priority
requests still share their user/entity turns. Other catalogue providers retain
their concurrency, minimum start intervals, cooldowns and daily/monthly budgets.

One local dispatcher per provider/database checks the shared queue while there
are local waiters. Waits release SQL connections. Short database critical sections
select starts; transaction-owned advisory slots remain held through HTTP and are
released on cancellation or process loss. Waiting tickets expire unless renewed.
Automatic admission yields after 500 ms of contention without recording failed
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
request may publish, and work also needed by a user remains eligible.

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

## Benchmark record

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
