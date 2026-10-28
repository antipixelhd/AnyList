# Benchmark selection and interpretation

Run commands from the repository root. The maintained harness is `scripts/benchmark_search.py`; it uses real ASGI routes and PostgreSQL with controlled provider responses. Follow its current CLI and case list when they differ from this reference, then update this reference.

## Safe setup

Set `STATISTICS_TEST_DATABASE_URL` to an existing **disposable** PostgreSQL database named `anylist_test...` (or the dedicated fixture at `:55449/catalogue_test`). The shared `backend/tests/test_statistics_pg.py` fixture creates/drops a random schema and rejects normal application database URLs. Do not point it at preview/production or put credentials in the skill, Git or output logs. Use the project's Python environment and dependencies.

Use a verified Git-ignored output folder. `.local-design/search-browse/` was used for the October 2026 analysis, but its exclusion is local to that checkout; check `git check-ignore` and add a local `.git/info/exclude` entry if needed. The skill must not depend on those historical files being present.

## Select relevant cases

Trusted `--baseline-ref` comparisons load the affected contributor identity/details and IMDb person-component modules as well as search/evidence modules. This does not restore provider-discovery adapters or workers. Run `game_person_cached` separately from `game_person_availability`: the latter adds a TMDB identity to the shared fixture, changing whether the former needs a TMDB profile request. Keep that fixture distinction explicit when interpreting cached-read checks.

`game_person_cached` includes a fresh completed automatic-discovery attempt for its cached IMDb source generation and a fresh unresolved person-identity check. It measures repeat page reads after discovery, including a compact discovery-job header when source identities remain unresolved and a small identity-check field from the same component read; it does not measure first admission or provider mapping latency. The initial fetch-to-discovery/identity handoff, bounded continuation, cancellation, failures and identity/exclusion correctness are covered by `test_game_imdb_discovery_pg`, `test_game_imdb_discovery_source` and `test_game_person_identity_pg`. Keep initial admission separate from this zero-provider, zero-write cached-read workload.

Run `game_credits` separately from Games search/browse cases when measuring only the cached credit projection. Combining them gives the shared first-game fixture an IGDB identity and can admit unrelated primary metadata hydration, including its wait budget. Report that combined workload explicitly rather than interpreting its latency as credit-projection cost. The source tests also cover bounded exact-ID Wikidata statement-search recovery, independent WDQS/Action API cooldowns and verification against actual claims; the cached-person benchmark does not exercise that fallback.

Normal Games Browse cases establish ranking, pagination and request cost with usable source observations. They do not seed a recently fetched ranking whose provider timestamp has expired. `test_game_ranking_cache_pg` covers that recovery separately: unusable observations must await a shared refresh, usable stale rankings may paint immediately, and failures preserve cooldowns without presenting outdated evidence.

| Affected path | Useful existing cases |
|---|---|
| Title retrieval/ranking/runtime | `movie_exact`, `movie_typo`, `series_exact`, `combined_titles`, `empty_query`, `runtime_timeout` |
| Explicit filters | `movie_genre_score`, `date_genre_score`, `movie_streaming`, `movie_cinema`, `movie_released`, `series_airing`, `series_returning`, `series_finished`, `series_cancelled` |
| Browse sections/sorting | `default_sections`, `grid_popular`, `grid_trending`, `grid_score`, `movie_oldest`, `movie_alphabetical` |
| Dates, timeline, country release evidence | `movie_newest`, `movie_upcoming`, `series_upcoming`, `timeline_text`, `cinema_rereleases`, `details_then_timeline` |
| Contributor discovery/eligibility | `actor`, `crew`, `studio`, `negative_studio`, `people_combined`, `all_contributors` |
| Pagination/refill/options/failure | `page_two`, `sparse_viewport`, `filter_options`, `provider_failure` |
| Personal Games lists | `game_list`, `game_list_page_two`, `game_list_genre_year`, `game_list_status` (catalogue-rooted game entries, bounded page, full-collection facets/status counts; no provider requests) |
| Game favorites/profile activity | `game_favorites`, `game_favorites_page_two`, `game_activity_profile` (bounded private-safe projections; no provider requests) |
| Games browse | `game_browse_preview`, `game_browse_grid`, `game_browse_pages`, `game_browse_genre_platform`, `game_browse_timeline` (real source adapters, durable public page/reference/ranking cache and compact private projection; controlled IGDB payloads/50ms delay bypass the HTTP transport and budget layer. Cold caches reset per case; warm reads reuse public pages. The pages and timeline cases measure both first and next page and reject missing/repeated candidates. Text timelines finalize at most 1,000 raw search candidates before chronological paging; reaching that window is explicit partial coverage. The timeline case also asserts release-date order across both pages.) |
| Game cast/character detail projection | `game_credits` (real catalogue detail handler mounted under the benchmark tracking prefix; 30 performers, 100 characters including unvoiced appearances, 90 work-scoped links and sizeable stored statement evidence. Asserts complete associations and no raw evidence in the public payload; SQL selects role/language fields rather than full credit JSON. No provider requests.) |
| Game primary details and regional offers | `game_details_cold`, `game_offers` (controlled primary/character provider latency and a cached regional price projection). These cases establish response correctness, SQL and payload cost; they exclude browser startup/poll delays and the price scheduler's idle wait. Cold primary worker checkpoints can overlap SQL accounting. Verify skeletons, warm SSR reuse, primary publication and price-worker wake/concurrency separately in browser and focused tests. |
| Games quick-search candidates | `game_search` (50 controlled upstream candidates, eight result cards, one identity projection; upstream candidate reads are mocked and counted once per request, so this case does not establish IGDB response-cache/coalescing behavior) |
| Partial game-title browse search | `game_browse_partial_search` (real browse source and cursor path: the full `Elden R` query is empty, the broader `Elden` stream contains160 candidates split equally between matching and unrelated names. Three24-card pages must return72 distinct matching games in source order, crossing a raw100-candidate boundary without repeating the original probe. This added workload is post-change evidence rather than a historical before/after case.) |
| Cached Browse viewer-state refresh | `viewer_context` |
| Automatic empty-search expansion | `auto_combined_empty`, `auto_combined_established`, `auto_separate_empty`, `auto_browse_empty` |

Repeat `--case` for several cases. Group selectors are alternatives: `--filters-only`, `--contributors-only`, `--edge-cases-only`, `--stress-only`. Read the case list before combining a selector with `--case`, because named cases must belong to that selection.

The `auto_*` cases model the frontend's curated-then-broader HTTP sequence against real ASGI routes. Empty workloads use a low-popularity, zero-vote exact title; `auto_combined_established` uses a low-popularity movie with 10,000 votes and an empty series scope, so the movie must prevent both retries. Combined mode expands only when both title scopes are empty; separate mode expands only its empty scope. Browse retains explicit genre/score filters. `fallback_requests` counts broader retries; title discovery should reuse provider responses. These cases measure backend cost, not rendering or browser cancellation, which require frontend/UI verification.

```powershell
# Focused change: same cases and provider delay in both arms.
# Replace BASELINE_SHA with a trusted local pre-change commit SHA.
.venv/Scripts/python.exe scripts/benchmark_search.py --case movie_exact --case movie_cinema --baseline-ref BASELINE_SHA --output .local-design/search-browse/before.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case movie_exact --case movie_cinema --output .local-design/search-browse/after.json

# Broad cross-category risk: omit selectors for the full matrix.
.venv/Scripts/python.exe scripts/benchmark_search.py --output .local-design/search-browse/full-after.json

# Large credits and catalogue; include mixed actor/crew to catch role-cache misses.
.venv/Scripts/python.exe scripts/benchmark_search.py --case people_combined --case all_contributors --local-titles 10000 --credit-works 5000 --output .local-design/search-browse/large-credits.json
.venv/Scripts/python.exe scripts/benchmark_search.py --stress-only --local-titles 10000 --credit-works 5000 --output .local-design/search-browse/stress.json

# Only when investigating eligibility cost versus result quality.
.venv/Scripts/python.exe scripts/benchmark_search.py --contributors-only --skip-contributor-relevance --output .local-design/search-browse/relevance-bypassed.json
```

`--baseline-ref` executes selected trusted local core modules inside the benchmark process, without changing the checkout. It does **not** restore routes/models/frontend/fixtures. If those changed, compare isolated checkouts or equivalent fixtures instead; do not label this option a complete historical application replay. A baseline run must not discard unrelated working changes.

For frontend loading/view changes, use these backend cases where relevant and verify actual tab/sort/grid/timeline transitions, stale responses and viewport refill in the real page. ASGI measurements cannot establish UI stability.

## Compare equivalent profiles and stages

The harness runs three profiles automatically:

- `curated`: votes 250 OR popularity 25, movie runtime 30, documentary exclusion.
- `floor_disabled`: popularity 0, votes 250; runtime/category restrictions remain.
- `show_all`: personal popularity/runtime/category curation bypassed; explicit filters remain. Quick-search contributors use permissive preferences for this comparison, not a product Show all control.

`--skip-contributor-relevance` separately bypasses bounded contributor work eligibility **for the benchmark only**. It changes result quality and does not disable acquisition limits or add an account setting. Use it for explicit relevance-cost investigations, not as the normal performance target. Popularity-off and eligibility-off are different controls.

Each case resets relevant in-process caches; contributor cases also reset durable evidence. `cold` records initial discovery/admission. `verification_worker` drains contributor jobs and includes a final poll; scheduled waiting is skipped. Three `warm` repeats use fresh caches/completed evidence. `expired` forces durable expiry to check refresh admission. Inspect pending/row counts; cold contributors may intentionally be incomplete.

## What the output establishes

- JSON records `ms`, `db_ms`, read/write statement counts, SQL signatures (`sql_patterns` plus `sql:...` counts), provider endpoint counts, result identities, row counts, pagination and pending state. Missing counters mean zero.
- The harness also records response `payload_bytes` and `total`. Game list identities exclude fixture insertion timestamps and the newly projected year/genres; dedicated API tests verify those descriptive fields. Game cases seed `local_titles` actual game entries across five statuses, two genres and 26 release years. Their list membership/order/progress fingerprint remains comparable while response metadata grows. They do not establish expansion constraint behavior or UI performance; use the game PostgreSQL and browser checks for those.
- Explicit game-favorite cases also seed ordered favorites for those works; their fingerprints omit insertion timestamps and descriptive year/genre fields, matching list comparisons. Explicit game-activity cases seed fixed-time, verified public status-change windows; legacy unverified rows, privacy exclusions, exact achievement evidence and note-only writes are tested separately in PostgreSQL. Avoid interpreting a benchmark seeded by a new case as historical before/after evidence for a route that did not previously exist.
- Compare matching case/profile/stage. Compare result fingerprints and flags for optimizations; document deliberate relevance/behavior changes separately. A fingerprint covers returned content/order, not whether a synthetic fixture represents user intent well.
- Compare SQL counts **and payload cost**. Large-credit stress exposed roughly 400 ms of repeated DB transfer with just four reads. Query-count parity alone does not establish performance parity.
- Healthy warm cases should reuse provider evidence. The forced-outage fixture can still record one immediate failing provider invocation per poll; failures are not cached as successful empty results. Negative studios should stay omitted without repeating provider discovery until expiry.
- Worker counters include harness queue probes and forced-clock writes. When reporting production worker SQL costs, identify and exclude only verified harness-control operations. Current examples are `WHERE ...next_attempt_at IS NOT NULL` probes and clock-advancement updates. Check the current callers before subtracting counts: matching a SQL pattern alone is not proof that an operation is artificial. Retain real lease/settings/evidence operations; explicit instrumentation labels are preferable if this evolves. State whether final-poll work is included.
- Default provider misses simulate 50 ms; auth is stubbed and MDBList feed retrieval is mocked. HTTP counts cover TMDB misses. Cold does not flush PostgreSQL's buffer cache or necessarily reconnect HTTP. `db_ms` includes driver transfer; it is not PostgreSQL CPU time.
- `details_then_timeline` includes priming 20 detail requests. `sparse_viewport` aggregates up to six refill requests. Do not present either as a single ordinary API call.
- One cold sample and three warm samples are diagnostic, not production percentiles. Use repeated comparable arms for small latency claims. Browser debounce/network/rendering and concurrent VPS load are excluded. Initial real connectivity retries may exceed the immediate-failure fixture's timing.

## Correctness tests and self-maintenance

Select neighboring `unittest` modules rather than adding tests that mirror implementation. Relevant suites include `test_browse`, `test_discovery_policy`, `test_tmdb`, `test_result_filters`, `test_contributor_evidence`, `test_contributor_evidence_pg`, `test_contributor_search`, and `test_studio_discovery` under `backend/tests/`. Database suites require their explicit disposable DB variables; do not count skipped DB tests as successful database verification. UI changes also use neighboring `node:test` coverage and real-page checks as appropriate.

When modifying the harness, preserve profile distinctions, cache reset/expiry behavior, SQL/provider instrumentation, realistic data volume and result-quality checks. Extend case coverage for new result features when existing cases do not exercise them. Run the changed cases and relevant regression paths, then update this reference and the skill's architecture guidance if their claims changed. Do not store machine-specific timing thresholds as universal pass/fail requirements.

## Improving the implementation or benchmark

`game_details_cold` measures the catalogue details route with primary IGDB and optional character acquisition each delayed by `--provider-delay-ms`. Run it separately, for example `--case game_details_cold --provider-delay-ms 2000 --rounds 3`. Compare an otherwise identical arm with `--primary-blocking-reference`, which restores the former 15-second initial wait; this is a controlled blocking-reference comparison, not a full historical checkout replay. Each cold profile reacquires the fixture snapshot. The initial progressive response intentionally lacks primary content; compare its cost separately from `primary_ready_ms`, public primary projection size, and complete-journey SQL/provider/write counters. The latter waits for optional source work too. Verify canonical identity and exclusion of private entry/notes and credit payloads from the primary delta. Backend ready-event timing excludes browser polling, network, decoding and rendering; it is not LCP. Warm `game_metadata` fixtures carry current release/artwork policy versions so a warm-header case does not silently measure an upgrade.

`game_person_roles` measures the approved status route with 100 displayed work IDs. It checks exact work membership, character evidence, no provider work/writes, and no raw source payload. The batch is a request bound, not a stored/displayed collection limit: the browser publishes further mounted cards sequentially in batches on a public revision change. Cached person fixtures carry the current person-connection version; legacy version refresh is tested separately in disposable person integration tests. Do not describe these cached/header cases as a cold provider acquisition benchmark.

`game_title_mapping` exercises the shared public IGDB candidate cache, both search/exact query branches, complete retrieval evidence and exact accepted identity. Cold profiles remove only their candidate snapshot; warm samples reuse it. Explicit Wikidata discovery is controlled as unmapped, so its timing is not live SPARQL or full person acquisition latency. Provider requests use the configured delay. Candidate changes also perform targeted evidence invalidation; count that SQL alongside cache reads/writes. Cached person fixtures carry the current discovery matcher version so legacy upgrade admission is not mistaken for a warm lookup.

`game_title_mapping_deep` uses an undated title with independent Steam character/studio corroboration and direct IMDb cast evidence. Cold profiles remove its candidate/support snapshots; warm reads reuse them. It asserts the exact game and corroboration tier. All provider calls are controlled and delayed; neither this nor the fast case measures real Steam/IMDb latency. The existing fast case must retain its provider/SQL profile when deeper matching is added. Metadata fixtures carry the current release policy so warm headers do not accidentally benchmark a policy upgrade.

The case names, fixture sizes, profiles and current mechanisms above describe the harness, not limits on future improvements. Existing checks are a starting point: add or replace instrumentation, fixtures and comparisons when they better represent the changed path, while retaining meaningful quality, scale and failure coverage. If the workload changes, run comparable arms or document the break in comparability. Do not force a better design to reproduce obsolete query counts, payload shapes, cache layers or provider budgets. More work in one counter can be justified by lower overall latency/resource cost; explain the tradeoff.
