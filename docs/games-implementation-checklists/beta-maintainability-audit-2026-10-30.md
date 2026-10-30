# Beta maintainability audit — 30 October 2026

Audited beta `5349a246` against the merge base with `origin/main` (`4a6f5aaa`), using `git diff origin/main...HEAD`. Main was `d9f8f17f`. These are the pre-audit numbers; this report is excluded. This was a code and test audit, with no application changes, database changes, deployment, or merge into main.

## Assessment

The concern about duplicated coordination and uneven reuse is justified. The evidence does **not** support treating most of the 33,889 added lines as abandoned code or disposable tests. Much of the size comes from a substantial feature set and its correctness coverage. The branch also includes shared Browse, cold-detail, navigation, and authorization improvements, so its diff is larger than Games-only code.

A reasonable first cleanup target is **roughly 3,000–5,000 net lines removed while preserving the current features**, about 9–15% of additions. This is an engineering estimate from the inspected duplication, not a measured deletion patch. It includes replacement code and replacement tests, and is not additive with deleting whole feature areas. A larger reduction needs further evidence or an explicit feature-scope decision.

The principal maintenance problem is repeated ownership of request/job/state lifecycles. Reducing the number of places a fix must touch matters more than the final line count. Several files already compress substantial logic into long, semicolon-separated lines; compressing them further would make the situation worse.

## Where the additions went

Every changed path is assigned once in this table. Counts include blank lines and comments and use Git's textual diff, not logical statements.

| Category | Changed files | Added | Removed | Net growth |
| --- | ---: | ---: | ---: | ---: |
| Backend runtime, routes, models, schemas, admin tools | 102 | 11,949 | 352 | 11,597 |
| Frontend runtime and styles | 182 | 5,988 | 988 | 5,000 |
| Backend tests | 75 | 11,159 | 21 | 11,138 |
| Frontend tests | 61 | 2,453 | 53 | 2,400 |
| Database migrations | 16 | 789 | 0 | 789 |
| Benchmarks, import CLI, operations tests | 3 | 1,016 | 10 | 1,006 |
| Historical checklists and verification reports | 7 | 394 | 0 | 394 |
| Configuration, operations installer, agent guidance | 7 | 141 | 27 | 114 |
| **Total** | **453** | **33,889** | **1,451** | **32,438** |

Tests account for **40.2%** of additions. Application backend/frontend code accounts for **52.9%**. Documentation accounts for **1.2%**. Deleting verification prose cannot materially solve the size problem.

Backend additions break down further as follows:

| Concern | Added lines | Interpretation |
| --- | ---: | --- |
| Cross-provider identity, title matching, IMDb credit ingestion, mapping lifecycle and review | 4,081 | Largest distinct subsystem; real product behavior with consolidation opportunities |
| Tracking, work capabilities, primary metadata, activity, children, access and source cache | 1,263 | Mostly necessary foundations |
| Contributor projections, person identity/portraits and company portfolios | 1,236 | Real behavior; some repeated orchestration |
| Browse/search and people/company candidate discovery | 1,204 | Preserve provider-specific retrieval; share coordination where equivalent |
| Optional achievements, ratings, requirements, offers and completion estimates | 1,010 | Good candidates for a common enrichment lifecycle |
| Game statistics | 312 | Domain arithmetic is distinct; snapshot lifecycle need not be |
| Shared backend integration | 1,406 | Catalogue, details, search, scheduler and related integration |
| Routes | 765 | Includes authorization and public/private projection contracts |
| Models and schemas | 496 | Mostly necessary typed state and persistence |
| Admin review tools | 172 | Operational tools, not disposable smoke scripts |
| Application entry point | 4 | Registration |

There are **58 new `backend/core/game_*` files totaling 9,040 lines**. The 24-file identity/credit/mapping cluster above has another **3,855 added lines in 18 corresponding test files**: 7,936 lines together. That is feature scope, not an established 7,936-line deletion opportunity.

## Findings and concrete treatment

### 1. Important database coverage is skipped by the checked-in CI configuration

`.github/workflows/ci.yml:32` sets `TRACKING_TEST_DATABASE_URL` and runs unittest discovery at line 50. It does not set `GAMES_TEST_DATABASE_URL` or `CATALOGUE_TEST_DATABASE_URL`. `backend/tests/test_games_pg.py:34–37` and `test_catalogue_pg.py:47–50` explicitly skip their guarded classes without those variables. Numerous new suites use those fixtures and the same guards.

This is a coverage gap, not evidence of excessive useful test execution. First wire the existing disposable CI PostgreSQL into the relevant suites and make unexpected skips visible. Do not weaken the disposable-database guards. Backend integration timing must be measured with these tests actually enabled before claiming they are slow.

### 2. The shared list-filter extraction is incomplete

`frontend/src/lib/list-filter-controls.ts:5` implements dropdown state, keyboard navigation, outside-click/focus dismissal, viewport placement and mobile/year controls. Games calls it from `game-list.ts:34`. Movie/series lists still implement much of the same behavior inline in `frontend/src/pages/user/[username]/[section].astro:121–220`.

The dropdown functions at helper lines 29 onward and page lines 136 onward are particularly close. A whitespace-normalized clone scan found 19 overlapping seven-significant-line matching windows between these files. This is direct duplication, not just similar names.

**Treatment:** use the existing shared module for both list families. Keep client-side movie/series filtering and server-backed Games filtering as separate adapters; the shared controls should not dictate how data is retrieved. Preserve Games' facet refresh and both lists' focus/mobile behavior.

### 3. Enrichment repeats infrastructure, and some existing reuse has the wrong owner

Compare `game_ratings.py:83–176` with `game_time_to_beat.py:77–150`: both implement shared in-flight work, bounded execution, database lease claiming, token-checked publication, retry/failure handling, release and due-job scans. Similar machinery appears in `game_imdb_enrichment.py`, `game_imdb_person.py`, `game_company_works.py` and `game_metadata.py`.

There is already reuse: `GameAchievements` and `GameInfo` inherit `GameRatings`. However, ratings is now effectively a generic Steam enrichment runner. Unrelated modules also import generic admission, status and clock functions from `game_imdb_enrichment.py`. Understanding requirements or estimates should not require following an IMDb credit module.

`core/scheduler.py:841–868` registers **12 additional Games scheduler tasks**, including mapping-index maintenance. Most expensive work is demand-driven and bounded; the existence of 12 tasks does not establish a production performance defect. It does show fragmented polling and lifecycle ownership.

**Treatment:** give durable enrichment jobs a small shared interface for admission, status and running due work. Keep identity checks, provider retrieval, component TTLs and publication in adapters. Extract the current ratings runner into a correctly owned module first. Preserve leases across processes, cancellation isolation, partial publication and provider-specific limits. Do not introduce a new queue system or force paginated filmographies into a single-fetch model.

`GameSourceCache.refresh` also contains mapping-specific invalidation logic for candidate/support kinds. Move ownership of those source-change effects into the mapping/enrichment implementation while retaining the existing cache's bounded storage and shared refresh behavior.

### 4. Statistics duplicates lifecycle as well as differing domain arithmetic

`game_statistics.py:67–142` and `statistics_snapshots.py:45` onward both claim due work, compute against a consistent revision, check the lease/revisions on publication, retain/retry work and run a periodic scheduler. The persistence shapes differ: Games stores a generation payload in its state row, while screen statistics uses an active snapshot row.

**Treatment:** share the claim/compute/publish lifecycle through adapters. Keep game facts, achievement/time arithmetic, movie/series watch-history arithmetic, and privacy/invalidation rules explicit. Reusing the lifecycle does not require a database redesign or merging domain tables.

On the frontend, `game-statistics.ts:49–89` and `stats-overview.ts:92–175` separately configure and update similar metric and distribution charts despite already sharing tooltip, viewport and navigation helpers. A shared chart renderer can accept metric definitions and labels without unifying the statistics calculations.

### 5. Browse shares presentation but retains two request/state controllers

`browse.ts:51` and `game-browse.ts:25` share card rendering, loading UI, menus, ranges, pagination demand and timeline rendering. Each still owns generation/abort handling, caches, mounted-card maps, optimistic context reconciliation, rendering and footer state. Their retrieval contracts differ: Games has verified prefixes and continuations; screen Browse has facets, curation and its own retrieval behavior.

**Treatment:** extract only the common request/view lifecycle and card reconciliation, with explicit adapters for query/filter interpretation and result continuation. Avoid a universal controller full of `if game` branches. Preserve verification progress, ordering, cache ownership and tab snapshots. This is a higher-risk cleanup and needs the relevant before/after search benchmarks and actual page checks.

### 6. Some tests are brittle, but most sampled tests assert meaningful behavior

Among changed frontend test files, **22 read source text**; those files contain 1,903 current lines, including pre-existing tests. Reading source alone does not make a test invalid. Several go further and strip imports, execute transformed modules in a VM, or locate function bodies using string positions. Examples include `game-gallery-completeness.test.mjs:8–10,23–25`, `game-metadata.test.mjs:20–21` and `screen-primary.test.mjs:14–15`. This couples tests to source layout and makes harmless refactors expensive.

There is also useful coverage worth retaining: concurrent consumers sharing work, cancellation isolation, unknown versus zero progress, private-state preservation, identity changes, reviewed mappings, stable pagination, and authorization/revocation. The sampled Games tests use reusable fixtures already; this is not thousands of lines of independently copied database setup. New backend test lifecycle/setup/cleanup methods total approximately 486 lines, so fixture extraction alone cannot save several thousand lines.

**Treatment:** test the real exported interface with controlled dependencies; use one shared worker contract suite for shared worker behavior, then retain adapter-specific parsing, identity, publication and access cases. Convert repetitive matching/input examples into named data cases where that reduces duplication without obscuring failures. Replace source-transform tests when touching the affected modules. Do not delete them wholesale without replacement behavior coverage.

### 7. One-off artifacts and abandoned paths are not the main finding

The branch adds no workflow files or tracked smoke-named scripts. Its seven historical reports total only 394 lines. `scripts/benchmark_search.py` grows by 878 lines and is now 1,267 lines long; fixtures and case-specific setup/assertions accumulate inside `main`. This is reusable measurement code with real correctness/payload checks, but it would be easier to maintain as a small runner plus case groups. Splitting it alone saves no lines.

The mapping import CLI and the two backend review CLIs are entry points for ongoing ingestion/review operations. They are not automatically disposable because they were introduced during implementation.

A first-pass textual reference scan found no new core/lib module with zero external runtime references. That is a triage result, not a complete reachability proof: unused exports and conditionally unreachable branches remain possible. I did not establish a large dead-code island.

Old-version handling such as `game_metadata.upgrade_cached_artwork`, `game_person_identity.backfill`, supported matcher versions and favorite-order repair has identifiable compatibility purposes. Retire it only after checking persisted versions/rows and deployment overlap. No database inventory was performed, so it is excluded from the safe cleanup estimate.

## Savings budget, preserving current behavior

These are non-overlapping planning ranges for net repository reduction after adding the shared replacements. They are not ready-to-delete line counts or a performance forecast.

| Work | Estimated net lines saved | Confidence |
| --- | ---: | --- |
| Shared enrichment coordination, job ownership and statistics lifecycle | 600–1,100 | Medium; repeated mechanisms confirmed, exact compatible interface unimplemented |
| List controls, Browse lifecycle, primary-detail publication and chart reuse | 350–650 | Medium; list duplication high-confidence, Browse integration higher-risk |
| Consolidated worker contracts, simpler test harnesses and repetitive case setup | 1,600–3,000 | Medium-low for quantity; depends on completing the shared implementations first |
| Benchmark setup cleanup and optional report consolidation | 150–350 | Low-medium; measurement coverage must remain |
| **Total planning range** | **2,700–5,100** | **Rounded target: 3,000–5,000** |

This leaves roughly **29,000–31,000 additions' worth of scope** using the original additions as the planning denominator. A future Git diff will also reflect deletions from main's original files, so this is not a prediction of its exact insertion/deletion statistics. Application runtime reduction is likely around 1,000–1,800 lines; the rest is verification/support code. The architecture improvement should be larger than the percentage reduction suggests.

Removing the entire 7,936-line identity/credit cluster and corresponding tests would also remove behavior and break dependents; it is not an extra cleanup allowance. The same applies to dropping achievements, prices or statistics. Keep feature-scope decisions separate from maintenance work.

## Recommended execution order

1. Enable Games/catalogue database coverage in CI and record actual enabled-suite timing. Establish the baseline before restructuring.
2. Finish the existing list-control extraction. This is the smallest demonstrated duplication with a clear replacement already present.
3. Extract shared enrichment coordination from the current implementations; move common worker tests to its interface in the same changes. Keep old and new worker implementations from coexisting after each migration.
4. Share statistics lifecycle and chart rendering without changing domain calculations or schema.
5. Consolidate the Browse lifecycle with representative cross-category benchmarks and authenticated navigation checks. Keep retrieval adapters distinct.
6. Remove demonstrated unused exports and proven-expired compatibility code, then reassess test/support savings from the resulting code rather than imposing a deletion quota.

Keep canonical identities, capabilities, typed subjects, viewer-scoped optimistic state, public/private separation, complete credits, progressive primary loading, image reuse, crawler/share metadata, and applied migration history. These are foundations for adding another media type. Extend the existing capabilities/adapter patterns; avoid one enormous configuration registry or a new generic framework covering every domain.

## Verification and limits

- Current beta frontend suite: **771 passed, 0 failed, 0 skipped**, about **4.6 seconds** on this machine (`node --experimental-strip-types --test --test-reporter=tap tests/*.test.mjs`). This is the whole frontend suite, not 771 newly added tests.
- Git inventory, source/call-site inspection and a whitespace-normalized repeated-block scan support the structural findings above. The scan detects exact textual repetition, not all semantic duplication.
- Backend integration tests and performance benchmarks were not run: this shell lacks backend dependencies and no explicit disposable test database variable is configured locally. No preview/production database was used. No live provider or browser performance claim is made.
- Frontend type checking passed (`npm run check`): 588 files, 0 errors, 0 warnings, 27 hints. Hints include unused imports in the list page; these are small cleanup candidates, not evidence of thousands of dead lines.
- No application code, migrations or feature behavior was changed. Estimates require implementation and verification before they become actual savings.
