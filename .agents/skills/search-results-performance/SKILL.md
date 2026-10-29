---
name: search-results-performance
description: Improve AnyList search/results performance while protecting intended relevance and stable loading. Use for result features, changes to retrieval/ranking/filtering/pagination/caches/enrichment/loading, performance improvements, or benchmark changes across titles, browse, timelines, people, studios, and future media. Pure label/style edits or existing toggles with unchanged requests and loading behavior do not require this workflow.
---

# Search and results performance

Keep result correctness and responsiveness together. Read only the affected source paths and, for measurements, [references/benchmarks.md](references/benchmarks.md). Do not require the historical performance analysis or a full audit for every edit.

## Optimize outcomes, not the current implementation

Better performance is always allowed. The mechanisms, budgets, TTLs and examples below describe the current design and lessons from measurements; they are not permanent architectural requirements or performance ceilings. Replace or simplify them when a better approach meets the task's intended behavior. Do not require separate approval merely to improve performance within the authorized scope.

Evaluate latency, VPS load, transferred data, provider usage and result quality together. Fewer SQL/provider calls are useful signals, not absolute goals: additional cheap queries or bounded parallel requests can be a net improvement. Likewise, changing a budget, cache strategy or retrieval boundary is allowed when its effect on coverage, freshness and ordering is understood and verified. Honor intentional user-requested behavior changes rather than demanding identical old results.

## Choose proportionate verification

| Change | Required approach |
|---|---|
| Labels/styles, or forwarding an existing toggle with unchanged requests, cache keys and view lifecycle | Normal focused verification; no full performance analysis. If the change affects loading or creates extra work, use the next row. |
| New result feature, a specific filter/ranking change, or a local optimization | Invoke this skill. Trace the affected request and run the existing benchmark for relevant cases before/after, plus neighboring correctness tests. A field already in the payload does not justify a broad audit. |
| Changes with broad or uncertain cross-category effects, regressions across categories, or a broad performance request | Compare the full matrix when that breadth warrants it. A localized shared-helper edit can start with affected cases and representative consumers; expand if risk or results justify it. Add relevant stress, expiry, failure or UI checks. |
| Benchmark or this skill's instructions change | Invoke this skill, validate the changed measurement path, and check/update the guidance against the executable harness. Documentation-only maintenance needs validation and a representative benchmark smoke run, not a new full analysis. |

## Query and payload cost

- Count SQL statements **and** inspect transferred rows/columns and deserialization. A cached summary does not help if every poll still selects the full filmography JSON. The measured large-credit bottleneck was payload transfer, even with only four SQL reads.
- The current `backend/core/contributor_evidence.py` avoids repeated credit-payload transfer by reading evidence headers and reusing policy/role summaries. This is one effective approach, not a required two-step query design. Fetch only the data needed to establish eligibility/freshness. Requesting uncached summaries for irrelevant roles defeated mixed-search cache hits; preserve that lesson when replacing the mechanism.
- Keep private policy reads focused. `viewer_policies` currently shares one vote/popularity read across default sections, and editor/list state is attached to displayed cards together. Prefer equivalent batching/reuse over enriching full pages and discarding most cards; useful prefetch for the next interaction remains allowed.
- Preserve complete underlying domain records where the product requires them; search-only evidence may use bounded samples/projections. Indexed existence checks currently avoid transferring entire local works or querying once per credit. Alternative indexes, summaries or batching are welcome when they preserve the required evidence.

## Provider retrieval and enrichment

- Apply known adult/anime/category/runtime/popularity violations before expensive details. Handle missing evidence according to the result contract: titles may need enrichment, while unverified contributors may be temporarily omitted. Do not treat missing summary fields as proof of a negative or silently drop required filter checks.
- Prefer reusing or improving TMDB's shared request layer, concurrent-request coalescing, and base/appended component caches over adding duplicate retrieval paths. Compose a requested detail shape only when the base and **all** requested components are valid for its parameters/language. A cache hit alone does not establish renewed freshness; TTL changes or revalidation policies need explicit freshness reasoning. Do not mix failed credentials with another caller's in-flight request.
- Studio verification currently uses popularity-sorted samples bounded across worker ticks (two discovery pages per media kind per candidate); people share combined credits across actor/crew. Keep resource use controlled, but improve sampling, ordering or budgets when measurements and relevance checks support it. These numbers are not fixed limits for future designs or other subsystems.
- Chronological selection and text search have different ordering/coverage contracts. Shared evidence, precomputed indexes or merged retrieval are allowed. Current timelines finalize a bounded window before paging; changing the selection must account for stable paging and required country evidence, rather than silently sacrificing them for a timing.

## Cache, policy and failure semantics

- Share public provider evidence across viewers; scope private result/editor state to its viewer. Any eligibility cache must account for inputs that affect its answer, currently evidence identity/expiry, candidate role, effective policy and anime visibility. Reevaluate when these inputs change, without repeating valid provider discovery.
- Preserve unknown, partial, verified-empty and failed states. A complete negative can be reused until expiry; a timeout is not proof of irrelevance. Avoid repeating an expired slow check in every request; deduplicated refresh is the current solution, and alternative freshness strategies are allowed.
- Keep resource use bounded and shared-work ownership/cancellation correct. Queue admission and leases are current mechanisms, not mandatory architecture. A departing caller must not cancel another subscriber's needed work or poison retry state.
- Runtime enrichment has a short retry cooldown after failure/budget exhaustion. Unknown runtime remains unknown; local/durable runtime still wins, credentials remain isolated, and ordinary caller cancellation does not set a cooldown. Do not cache a provider outage as a successful empty search.
- Keep curation distinct from explicit user filters. Show all bypasses personal restrictions without changing account settings, the query/scope/sort or explicit filters. Adult/anime rules remain in force. It is not a quick-search feature. MDBList ranked feeds already supply curation and are exempt from TMDB popularity/vote floors; do not blanket-exempt other restrictions.

## Games detail pages

- Keep primary game metadata and usable cast projections independent of optional portraits, galleries, prices and career enrichment. Paint saved artwork first; request missing portraits for visible people, and expose character art and extra galleries on demand. The eight-person preview is a presentation budget, never a limit on stored or accessible credits.
- For missing person portraits, reuse a known TMDB identity before doing an IMDb-ID lookup. TMDB find results can supply `profile_path` without a second profile request. Keep that portrait projection separate from canonical identity merging, which retains its stronger reverse-ID checks. Avoid fetching filmographies, image galleries or Wikidata evidence merely to show a headshot.
- Bound portrait concurrency across batches (currently four TMDB lookups per event loop), coalesce overlapping people, and cache positive and verified-empty outcomes durably. Preserve timeouts/errors as retryable evidence, keep stale usable artwork, and batch only the remaining missing pictures through IMDb. Recheck identities and protected edits after provider I/O; no background portrait should replace an already usable image in the mounted page.
- Run `game_credits` separately for the complete cast/character projection, and `game_portraits` for eight-person acquisition, warm cache cost and provider fan-out. Include focused timeout, partial failure, cancellation, alias/edit and authorization checks. Distinguish controlled ASGI timings from live-provider and browser download/decode measurements; neither establishes complete authenticated page paint time.

## Result stability and extension

- Give a primary premiere/season a logical identity that survives corrected dates and richer country metadata. Optional rereleases remain distinct events. When merging local and provider pages, retain fresh-event precedence across the finalized window, not only the current slice; otherwise a cached copy can reappear later. Do not deduplicate by title text or collapse legitimate season/rerelease events.

- Keep title ranking/results stable during progressive people/studio verification. Contributor rows may evolve independently. Do not make background eligibility polls reload or reshuffle titles.
- Tab/view/sort changes must invalidate obsolete requests and show the new view or skeleton promptly. Preserve query ownership, pagination cursors, viewport refill and observer cleanup; stale responses must not overwrite the current selection. Inspect the real page when changing these paths.
- Reuse or improve media/provider and policy/composition boundaries when extending results; do not impose a particular adapter or retrieval topology. Combined movie/series ranking must remain comparable where that behavior is required. Measure added work from new books/games results, and use batching, caching or bounded concurrency where they improve the outcome.

## Verify and maintain

For covered implementation changes, run the existing benchmark at the scope above, before and after, with equivalent fixtures/settings. Run relevant neighboring tests. Compare result identities/order, pagination and pending states as well as counts/timings; explain intentional behavior differences. Investigate extra queries/provider calls and payload growth even if a single latency sample improves. Budget/filter changes may be valid optimizations or requested features; assess their result-quality tradeoffs. Do not hide a regression by weakening the measured workload or claiming a semantic change is an equivalent optimization.

Use the benchmark reference for supported commands, profiles and measurement limits. Keep raw measurements out of Git in a verified ignored location; keep reusable benchmark code and this skill version-controlled. If a required check cannot run, state the missing prerequisite and unverified behavior; never substitute a preview/production test database.

When changing providers, routes, cases, cache semantics, budgets or benchmark instrumentation, check this skill and its reference for drift in the same change. Update only facts demonstrated by code or measurements. Preserve meaningful stress/failure/quality checks; do not replace them with easier fixtures or universal millisecond promises. Report the cases run, SQL/provider/payload changes, result correctness, and whether timings are controlled backend, live-provider or browser measurements.

