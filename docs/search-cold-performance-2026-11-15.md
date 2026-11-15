# Cold people and studio search — 15 November 2026

Two retained changes improve cold verification: budget work by actual provider
requests, and stop studio sampling once a qualifying work establishes relevance.
The comparison starts at commit `417952f3`, after the earlier cached/expired
evidence optimization. These measurements focus on ordinary cold queries.

## Measured improvements

These are real ASGI routes, the actual verification scheduler and disposable
PostgreSQL, with controlled provider bodies/delays and stubbed authentication.
Times include initial name discovery, worker verification and the final API
observation. Medians cover three policy profiles, with one cold journey per
profile. They are small backend samples, not production percentiles or browser
paint measurements. Provider/evidence caches reset before each cold case.

| Eight-candidate search, 750ms providers | Before | After | Faster |
|---|---:|---:|---:|
| People, actor and crew together | 3.93s | 2.39s | 39% |
| Studios | 7.22s | 3.96s | 45% |
| Combined people and studios | 10.27s | 5.65s | 45% |
| People, eight active concurrent viewers | 5.27s | 3.50s | 34% |
| Studios, eight active concurrent viewers | 8.77s | 5.31s | 39% |
| Combined, eight active concurrent viewers | 12.96s | 7.50s | 42% |

At 50ms providers, people improved 540→311ms, studios 4,024→547ms and combined
search 4,553→797ms. The previous studio continuation delay and subsequent idle
scan were significant when provider responses were fast. Actor-only and
crew-only cases also improved at both provider delays.

The following counts cover verification and its observations, after initial name
discovery, for one viewer at 750ms. Read/write counts are SQL statements, not
affected rows. Shorter completion also reduces observation work.

| Search | Provider requests | Provider JSON bytes | SQL reads | SQL writes |
|---|---:|---:|---:|---:|
| People | 8→8 | 392,448→392,448 | 143→76 | 23→14 |
| Studios | 32→16 | 179,424→89,712 | 264→148 | 35→23 |
| Combined | 40→24 | 571,872→482,160 | 551→311 | 84→53 |

Eight concurrent viewers retain those same provider totals; requests do not
multiply by eight. Their combined verification uses 2,551→1,448 reads and
1,237→729 write statements. Shared jobs, demand ownership, leases and the
four-request verification ceiling remain in force.

Recorded real public studio evidence also improves when replayed through the
same backend with 750ms controlled latency:

| Query | Cold completion | Verification requests | Provider JSON bytes |
|---|---:|---:|---:|
| Marvel | 8.71→3.96s | 20→16 | 166,878→126,457 |
| Warner Bros. | 5.00→3.98s | 17→16 | 35,613→29,681 |

These replay timings are not paired live-provider measurements. Saved work
depends on the query and policy; the synthetic all-positive studio workload's
50% request reduction is not a claim about every studio query.

## What verification establishes

People need one qualifying acting or crew credit. Studio results need one
qualifying released production work. Adult/anime visibility, role restrictions,
runtime and category filters still apply; studios also retain the viewer's
work-popularity requirement. A name, logo or unfiltered contributor count cannot
substitute for qualifying work evidence. Studio search does not apply an
independent vote-count floor.

Counting every sampled work is unnecessary to exclude contributors without
qualifying works. People already use existence summaries. Studios now retain
their first movie/series sample when it contains a witness. Unresolved checks
continue immediately within the existing two-page-per-kind bound. A later viewer
whose filters reject a parked sample can resume its remaining pages without
refetching page one. Worker policy reads are batch-owned and private; only public
works and coverage are durable/shared.

Partial positive summaries are cached with their actual coverage. A partial
sample without a witness remains unknown, and a negative requires completing
the bounded sample. Expired partial samples refresh from page one without
renewing old observations. Share previews can reuse already observed public
works without presenting incomplete coverage as an empty collection.

Studio ranking still uses popularity, observed sample count and dates. Sample
counts can now stop growing after a positive result; they are not identical
full-sample counts or complete filmography totals. Membership and ordering
matched in all measured workloads, including the real evidence comparisons.
The eligibility optimization does not promise identical ranking for every
possible sample-count tie.

## Why the worker is faster

The previous worker admitted two contributors regardless of cost. Two people
used only two of its four allowed requests, while two studios could use all
four. It now admits a fair prefix costing at most four requests: one per person,
two per initial studio, and only the remaining media pages for a fresh studio
continuation. It does not skip an earlier studio merely to fit cheaper work.

Useful studio samples no longer fetch extra pages solely to increase a sample
count. Unresolved samples continue without the former two-second delay or an
additional client admission. Initial name lookup and already-available first
results are separate from complete verification; the reported improvements do
not imply every exact single-person lookup becomes faster.

## Relevance and verification

All 270 paired samples retain identical public result identities/order, result
counts, pending states and response bytes. This includes 50/750ms providers,
actor/crew/studio/combined cases, eight distinct concurrent users with active
search generations, and the Marvel/Warner evidence replays. Warm and expiry reads
are compatibility checks, not the performance finding reported here.

Live public captures cover Warner Bros., DC, A24, Netflix, Universal, Pixar,
Marvel and Studio Ghibli: 52 studio candidates, 12 policy/visibility combinations,
624 candidate eligibility comparisons and 96 query-level ordering comparisons.
All matched the fully acquired bounded reference. This establishes compatibility
for those observations, not universal provider latency or ranking equivalence.

Focused PostgreSQL regressions verify distinct-viewer policy isolation, shared
continuation/leases, fair competing searches, provider request bounds, fresh
partial summary reuse, expiry, cooldowns and public share summaries.
All 126 focused tests passed. Ruff and compilation checks passed for the changed
Python paths.

## Reproduction

Use an explicit disposable `STATISTICS_TEST_DATABASE_URL` accepted by the guarded
fixture. Never point tests at beta/production. Raw measurements and public
captures are ignored under `.local-design/search-investigation/`.

```powershell
.venv/Scripts/python.exe scripts/benchmark_search.py --case actor --case crew --case studio --case people_combined --case all_contributors --provider-delay-ms 750 --verification-scheduler --stage-timings --rounds 3 --output .local-design/search-investigation/cold-after.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case people_combined --case studio --case all_contributors --provider-delay-ms 750 --verification-scheduler --active-search-demand --concurrent-readers 8 --stage-timings --rounds 3 --output .local-design/search-investigation/cold-concurrent-after.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case studio --studio-fixture .local-design/search-investigation/cold-live-witness.json --studio-query Marvel --provider-delay-ms 750 --verification-scheduler --stage-timings --rounds 3 --output .local-design/search-investigation/cold-marvel-after.json
```

Repeat with `--baseline-ref 417952f3` and a different output path for the baseline;
repeat the first command at 50ms. The baseline option restores the affected core
modules, not historical routes/models/fixtures; their relevant interfaces are
unchanged in this comparison. The benchmark reference documents capture shape,
stage meanings and controlled-measurement limits. No schema migration is needed.
