# People and studio search performance — 15 November 2026

Two retained changes reduce search work while preserving the existing relevance
rules: people use a qualifying-credit existence check and read only the requested
acting/crew evidence; people and studios share one viewer-policy read per request.
Studio sample coverage, counts and ranking remain complete under the existing
bounded verification contract.

## Measured improvements

Before is commit `f4b8dcb05e49fb44a4ee64042e51d082a877c1b4`.
These are local backend measurements through real ASGI routes and disposable
PostgreSQL, with controlled providers and stubbed authentication. Values are
medians across the curated, popularity-disabled and permissive profiles.

| Workload | Before | After | Improvement |
|---|---:|---:|---:|
| Expired people search, 5,000 cast and 5,000 crew records per candidate | 443.97 ms | 173.91 ms | 61% |
| Expired combined people/studio search, same large histories | 460.01 ms | 258.41 ms | 44% |
| Eight concurrent users, expired people search, median reader latency | 3,516.86 ms | 1,316.44 ms | 63% |
| Eight concurrent users, expired combined search, median reader latency | 3,441.52 ms | 1,273.35 ms | 63% |
| Warm studio search, ordinary histories | 9.19 ms | 7.74 ms | 16% |
| Warm combined people/studio search | 11.72 ms | 9.05 ms | 23% |

The large-history fixture includes 10,000 local titles and eight provider people.
Role projection halves person-credit records returned to Python from 80,000 to
40,000 per uncached evidence read. The complete stored careers remain available.
The people policy scan in the expired large-history case fell from 151.41 ms to
0.071 ms: the first qualifying credit proves eligibility, so remaining credits
do not need to be counted. A negative or unknown result still requires examining
the relevant evidence; the test includes both.

Warm SQL reads fall from 6 to 5 for people, 7 to 5 for studios and 11 to 7 for
combined searches. With eight users, the combined warm wave uses 72 rather than
104 reads. Warm reads retain zero provider calls and zero writes. Provider totals
match in every paired workload: ordinary 378, large histories 162, slow providers
186 and concurrent readers 318. This reduces local processing and database
transfer without increasing upstream requests or widening worker concurrency.

## Steps and bottlenecks

The following post-change readings use the ordinary fixture with 50 ms controlled
provider delay. Each cell is a median; nested stages and parallel branches are
inclusive and must not be added together. Cold requests can return pending
contributors; worker completion is measured separately.

| Step | People | Studios | Interpretation |
|---|---:|---:|---|
| Local name/index candidates | 1.57 ms | 2.70 ms | Bounded indexed retrieval |
| Screen candidate discovery, including local retrieval | 59.38 ms | 57.96 ms | Mainly provider delay |
| Parallel Games discovery branch | 62.70 ms | 59.69 ms | Overlaps screen discovery |
| One viewer-policy read | 1.21 ms | 1.16 ms | Shared by both verification paths |
| Initial evidence lookup and worker admission | 7.94 ms | 7.36 ms | Unknown evidence queues shared work |
| Entire initial response | 74.98 ms | 71.41 ms | Includes overlapping branches |
| Verification worker drain and final poll | 336.60 ms | 638.19 ms | 8 people requests; 32 studio requests |
| Warm evidence headers/summaries | 1.35 ms | 1.36 ms | No career payload transfer |
| Warm final Games candidate projection | 1.24 ms | 1.24 ms | Fresh canonical results |
| Warm final ranking | 0.016 ms | 0.017 ms | Negligible compared with I/O |

With 750 ms controlled providers, complete worker verification takes about
3.14 seconds for people and 6.23 seconds for studios. Studio verification's
remaining cold cost is provider discovery: up to two popularity-sorted pages
per movie/series kind, for eight candidates. The worker benchmark forces due
jobs and skips scheduled waits; browser polling, network transport and rendering
are outside these timings. No production page-paint speedup is inferred from it.

For expired large people histories, evidence reading/application now takes about
154 ms while the eligibility scan itself is below 0.1 ms. JSON transfer and
decoding remain the main local cost in that workload.

## Relevance and shared-user behavior

People must have at least one qualifying credit in the requested role. Existing
adult, anime, runtime, category and role rules still apply; partial, unknown,
verified-empty and failed evidence remain distinct. Full verified credits retain
precedence over the provider's small `known_for` shortlist. Existence summaries
have separate cache identities from full-count summaries, including role,
effective filters, anime visibility and evidence expiry.

Studios still group reviewed identities, require qualifying released works,
apply work popularity and personal restrictions, and rank using the full bounded
sample's qualifying count, popularity and latest date. An early-exit boolean
cannot replace those ranking values.

All 498 paired benchmark samples retain identical result fingerprints/order,
row counts, pending state and response byte size. The matrix covers ordinary
actor, crew, studio, negative studio, people and combined search; large histories;
50/750 ms providers; cold, worker, warm and expired states; and eight distinct
concurrent users. Tests also cover different users' policies, policy changes,
cache freshness, role changes, unknown/empty evidence, queue contention,
cross-worker leases, cancellation and shared demand ownership.

Live read-only TMDB captures covered Tom Hanks, Samuel L. Jackson, Hans Zimmer,
Christopher Nolan, Hayao Miyazaki, Warner Bros., DC and A24. Across 38 candidates
and 12 policy/visibility combinations, all 456 relevance comparisons matched.
Studio summaries matched in full. These live samples verify real evidence
compatibility; their provider latency is not a controlled before/after endpoint
comparison.

All 101 tests passed across contributor evidence, search, studio discovery,
shared search demand, Games contributor search and contributor details. This
includes the new policy equivalence, distinct-viewer isolation, role projection,
full-career preservation and existence/full-count cache-separation checks.

## Reproduction

Use `scripts/benchmark_search.py` with an explicit disposable
`STATISTICS_TEST_DATABASE_URL`; never use beta or production databases. Raw JSON
and live captures are ignored under `.local-design/search-investigation/`.

```powershell
.venv/Scripts/python.exe scripts/benchmark_search.py --case actor --case crew --case studio --case negative_studio --case people_combined --case all_contributors --rounds 11 --stage-timings --output .local-design/search-investigation/ordinary.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case people_combined --case all_contributors --local-titles 10000 --credit-works 5000 --rounds 7 --stage-timings --output .local-design/search-investigation/large.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case people_combined --case studio --case negative_studio --provider-delay-ms 750 --rounds 7 --stage-timings --output .local-design/search-investigation/slow.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case people_combined --case all_contributors --case studio --case negative_studio --credit-works 5000 --concurrent-readers 8 --rounds 7 --stage-timings --output .local-design/search-investigation/concurrent.json
```

Repeat each arm with `--baseline-ref f4b8dcb05e49fb44a4ee64042e51d082a877c1b4`
for the original core implementation. This restores selected core modules, not
historical routes, models or fixtures; those interfaces are unchanged in this
comparison. See the search-results-performance benchmark reference for stage
and concurrent-counter interpretation.
