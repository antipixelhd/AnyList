# Benchmark selection and interpretation

Run commands from the repository root. The maintained harness is `scripts/benchmark_search.py`; it uses real ASGI routes and PostgreSQL with controlled provider responses. Follow its current CLI and case list when they differ from this reference, then update this reference.

## Safe setup

Set `STATISTICS_TEST_DATABASE_URL` to an existing **disposable** PostgreSQL database named `anylist_test...` (or the dedicated fixture at `:55449/catalogue_test`). The shared `backend/tests/test_statistics_pg.py` fixture creates/drops a random schema and rejects normal application database URLs. Do not point it at preview/production or put credentials in the skill, Git or output logs. Use the project's Python environment and dependencies.

Use a verified Git-ignored output folder. `.local-design/search-browse/` was used for the October 2026 analysis, but its exclusion is local to that checkout; check `git check-ignore` and add a local `.git/info/exclude` entry if needed. The skill must not depend on those historical files being present.

## Select relevant cases

| Affected path | Useful existing cases |
|---|---|
| Title retrieval/ranking/runtime | `movie_exact`, `movie_typo`, `series_exact`, `combined_titles`, `empty_query`, `runtime_timeout` |
| Explicit filters | `movie_genre_score`, `date_genre_score`, `movie_streaming`, `movie_cinema`, `movie_released`, `series_airing`, `series_returning`, `series_finished`, `series_cancelled` |
| Browse sections/sorting | `default_sections`, `grid_popular`, `grid_trending`, `grid_score`, `movie_oldest`, `movie_alphabetical` |
| Dates, timeline, country release evidence | `movie_newest`, `movie_upcoming`, `series_upcoming`, `timeline_text`, `cinema_rereleases`, `details_then_timeline` |
| Contributor discovery/eligibility | `actor`, `crew`, `studio`, `negative_studio`, `people_combined`, `all_contributors` |
| Pagination/refill/options/failure | `page_two`, `sparse_viewport`, `filter_options`, `provider_failure` |

Repeat `--case` for several cases. Group selectors are alternatives: `--filters-only`, `--contributors-only`, `--edge-cases-only`, `--stress-only`. Read the case list before combining a selector with `--case`, because named cases must belong to that selection.

```powershell
# Focused change: same cases and provider delay in both arms.
# Replace BASELINE_SHA with a trusted local pre-change commit SHA.
.venv/Scripts/python.exe scripts/benchmark_search.py --case movie_exact --case movie_cinema --baseline-ref BASELINE_SHA --output .local-design/search-browse/before.json
.venv/Scripts/python.exe scripts/benchmark_search.py --case movie_exact --case movie_cinema --output .local-design/search-browse/after.json

# Shared-system changes: omit selectors for the full matrix.
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

- `curated`: popularity 25, movie runtime 30, documentary exclusion.
- `floor_disabled`: popularity 0; runtime/category restrictions remain.
- `show_all`: personal popularity/runtime/category curation bypassed; explicit filters remain. Quick-search contributors use permissive preferences for this comparison, not a product Show all control.

`--skip-contributor-relevance` separately bypasses bounded contributor work eligibility **for the benchmark only**. It changes result quality and does not disable acquisition limits or add an account setting. Use it for explicit relevance-cost investigations, not as the normal performance target. Popularity-off and eligibility-off are different controls.

Each case resets relevant in-process caches; contributor cases also reset durable evidence. `cold` records initial discovery/admission. `verification_worker` drains contributor jobs and includes a final poll; scheduled waiting is skipped. Three `warm` repeats use fresh caches/completed evidence. `expired` forces durable expiry to check refresh admission. Inspect pending/row counts; cold contributors may intentionally be incomplete.

## What the output establishes

- JSON records `ms`, `db_ms`, read/write statement counts, SQL signatures (`sql_patterns` plus `sql:...` counts), provider endpoint counts, result identities, row counts, pagination and pending state. Missing counters mean zero.
- Compare matching case/profile/stage. Compare result fingerprints and flags for optimizations; document deliberate relevance/behavior changes separately. A fingerprint covers returned content/order, not whether a synthetic fixture represents user intent well.
- Compare SQL counts **and payload cost**. Large-credit stress exposed roughly 400 ms of repeated DB transfer with just four reads. Query-count parity alone does not establish performance parity.
- Healthy warm cases should reuse provider evidence. The forced-outage fixture can still record one immediate failing provider invocation per poll; failures are not cached as successful empty results. Negative studios should stay omitted without repeating provider discovery until expiry.
- Worker counters include harness queue probes and forced-clock writes. When reporting production worker SQL costs, subtract signatures for the harness `WHERE ...next_attempt_at IS NOT NULL` probes and `UPDATE ... SET next_attempt_at=...` clock advancement; retain real lease/settings/evidence operations. State whether final-poll work is included.
- Default provider misses simulate 50 ms; auth is stubbed and MDBList feed retrieval is mocked. HTTP counts cover TMDB misses. Cold does not flush PostgreSQL's buffer cache or necessarily reconnect HTTP. `db_ms` includes driver transfer; it is not PostgreSQL CPU time.
- `details_then_timeline` includes priming 20 detail requests. `sparse_viewport` aggregates up to six refill requests. Do not present either as a single ordinary API call.
- One cold sample and three warm samples are diagnostic, not production percentiles. Use repeated comparable arms for small latency claims. Browser debounce/network/rendering and concurrent VPS load are excluded. Initial real connectivity retries may exceed the immediate-failure fixture's timing.

## Correctness tests and self-maintenance

Select neighboring `unittest` modules rather than adding tests that mirror implementation. Relevant suites include `test_browse`, `test_discovery_policy`, `test_tmdb`, `test_result_filters`, `test_contributor_evidence`, `test_contributor_evidence_pg`, `test_contributor_search`, and `test_studio_discovery` under `backend/tests/`. Database suites require their explicit disposable DB variables; do not count skipped DB tests as successful database verification. UI changes also use neighboring `node:test` coverage and real-page checks as appropriate.

When modifying the harness, preserve profile distinctions, cache reset/expiry behavior, SQL/provider instrumentation, realistic data volume and result-quality checks. Extend case coverage for new result features when existing cases do not exercise them. Run the changed cases and relevant regression paths, then update this reference and the skill's architecture guidance if their claims changed. Do not store machine-specific timing thresholds as universal pass/fail requirements.
