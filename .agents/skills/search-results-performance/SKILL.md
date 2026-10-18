---
name: search-results-performance
description: Preserve AnyList search and result relevance, latency, query budgets, and stable loading. Use for result features, changes to retrieval/ranking/filtering/pagination/caches/enrichment/loading, performance improvements, or benchmark changes across titles, browse, timelines, people, studios, and future media. Pure label/style edits or existing toggles with unchanged requests and loading behavior do not require this workflow.
---

# Search and results performance

Keep result correctness and responsiveness together. Read only the affected source paths and, for measurements, [references/benchmarks.md](references/benchmarks.md). Do not require the historical performance analysis or a full audit for every edit.

## Choose proportionate verification

| Change | Required approach |
|---|---|
| Labels/styles, or forwarding an existing toggle with unchanged requests, cache keys and view lifecycle | Normal focused verification; no full performance analysis. If the change affects loading or creates extra work, use the next row. |
| New result feature, a specific filter/ranking change, or a local optimization | Invoke this skill. Trace the affected request and run the existing benchmark for relevant cases before/after, plus neighboring correctness tests. A field already in the payload does not justify a broad audit. |
| Shared retrieval/cache/policy/composition changes, regressions across categories, or a broad performance request | Compare the full matrix. Add only the relevant stress, expiry, failure or UI checks; do not rerun unrelated suites without a reason. |
| Benchmark or this skill's instructions change | Invoke this skill, validate the changed measurement path, and check/update the guidance against the executable harness. Documentation-only maintenance needs validation and a representative benchmark smoke run, not a new full analysis. |

## Query and payload cost

- Count SQL statements **and** inspect transferred rows/columns and deserialization. A cached summary does not help if every poll still selects the full filmography JSON. The measured large-credit bottleneck was payload transfer, even with only four SQL reads.
- In `core/contributor_evidence.py`, keep fresh policy/role summaries reusable while reading expiry/queue headers. Load raw payloads for missing summaries, changed policy or stale evidence. Request only each candidate's relevant actor/crew/studio summaries; requiring both people roles for every candidate defeats mixed-search cache hits.
- Keep private policy reads focused. `viewer_policies` shares one vote/popularity read across default sections; attach editor/list state once to the displayed cards. Do not enrich full pages and then discard most of their cards.
- Preserve complete domain evidence; bound selection/rendering rather than truncating stored credits. Keep indexed existence checks for local qualifying work instead of transferring entire works or querying once per credit.

## Provider retrieval and enrichment

- Apply known adult/anime/category/runtime/popularity violations before expensive details. Missing evidence stays eligible for the needed lookup; do not turn uncertainty into a negative or reject an explicit filter because its field is absent from a search summary.
- Reuse TMDB's shared request layer, concurrent-request coalescing, and base/appended component caches. Compose a requested detail shape only when the base and **all** requested components are valid for its parameters/language. Do not extend their TTLs on a cache hit or mix failed credentials with another caller's in-flight request.
- Keep studio verification popularity-sorted and bounded across worker ticks. Current studio samples use at most two discovery pages per media kind per candidate; people share combined credits across actor/crew. Read the implementation before changing budgets rather than copying these numbers into another subsystem.
- Keep cold release/country evidence work separate from text search. Chronological selection finalizes a bounded candidate window before paging; silently reducing that pool or dropping country evidence to improve a timing changes correctness.

## Cache, policy and failure semantics

- Share public provider evidence across viewers; scope private result/editor state to its viewer. Eligibility summaries must distinguish evidence identity/expiry, candidate role, effective policy and anime visibility. Reevaluate when these inputs change, without repeating valid provider discovery.
- Preserve unknown, partial, verified-empty and failed states. A complete negative can be reused until expiry; a timeout is not proof of irrelevance. Evidence expiry should admit deduplicated refresh work, not perform the full check inside every search request.
- Preserve bounded cache sizes, queue admission, leases and cancellation cleanup. A departing caller must not cancel another subscriber's shared work or poison retry state.
- Runtime enrichment has a short retry cooldown after failure/budget exhaustion. Unknown runtime remains unknown; local/durable runtime still wins, credentials remain isolated, and ordinary caller cancellation does not set a cooldown. Do not cache a provider outage as a successful empty search.
- Keep curation distinct from explicit user filters. Show all bypasses personal restrictions without changing account settings, the query/scope/sort or explicit filters. Adult/anime rules remain in force. It is not a quick-search feature. MDBList ranked feeds already supply curation and are exempt from TMDB popularity/vote floors; do not blanket-exempt other restrictions.

## Result stability and extension

- Keep title ranking/results stable during progressive people/studio verification. Contributor rows may evolve independently. Do not make background eligibility polls reload or reshuffle titles.
- Tab/view/sort changes must invalidate obsolete requests and show the new view or skeleton promptly. Preserve query ownership, pagination cursors, viewport refill and observer cleanup; stale responses must not overwrite the current selection. Inspect the real page when changing these paths.
- Extend through media/provider adapters and shared policy/composition boundaries. Avoid parallel queries for evidence another category already fetched. Combined movie/series ranking must remain comparable; new books/games adapters should not introduce per-result fan-out into existing categories.

## Verify and maintain

For covered implementation changes, run the existing benchmark at the scope above, before and after, with equivalent fixtures/settings. Run relevant neighboring tests. Compare result identities/order, pagination and pending states as well as counts/timings; explain intentional behavior differences. Investigate extra queries/provider calls and payload growth even if a single latency sample improves. Do not lower budgets or disable filtering simply to make a benchmark pass.

Use the benchmark reference for supported commands, profiles and measurement limits. Keep raw measurements out of Git in a verified ignored location; keep reusable benchmark code and this skill version-controlled. If a required check cannot run, state the missing prerequisite and unverified behavior; never substitute a preview/production test database.

When changing providers, routes, cases, cache semantics, budgets or benchmark instrumentation, check this skill and its reference for drift in the same change. Update only facts demonstrated by code or measurements. Preserve meaningful stress/failure/quality checks; do not replace them with easier fixtures or universal millisecond promises. Report the cases run, SQL/provider/payload changes, result correctness, and whether timings are controlled backend, live-provider or browser measurements.

