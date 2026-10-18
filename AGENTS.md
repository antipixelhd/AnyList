# Working rules

Commit changes for completed features, tests, verifications etc, leave specific smaller edits requested by the user open until the user confirms them

if you make changes to the database apply the migration before ending the turn

Avoid cluttering UIs with excessive micro-copy, subtitles, helper text, badges, and tiny metadata. Prefer clean layouts with strong hierarchy, spacing, and obvious controls. If text is not necessary for understanding or action, leave it out.
For UI use subtle motion where possible that stays consistent with the rest of the design language
Do not use outlines for active or focused UI states; use color and background changes instead.

Dont prefer bandade fixes but prefer concrete systems that fix the problem or implement the features

## Architecture direction

- Prioritize the next interaction: metadata, user state, primary poster and background first; detail portraits and character art next; optional galleries on demand.
- Keep optimistic state shared and stable. Background enrichment should preserve pending writes, selected artwork, and already usable content.
- Reuse canonical image identities, responsive URLs, and decoded images across views; prefer native browser image caching.
- Load extra gallery images only when exposed or selected. Optional disk caching should stay bounded and favor primary images over extras.
- Preserve complete domain data while rendering large collections progressively. Display limits should not become storage limits.
- Keep reads focused, share concurrent requests, and reuse cached enrichment. Bound expensive work and keep it off the event loop on the shared VPS.
- Make page initialization repeat-safe and clean up observers, timers, and requests when views change. Keep private caches scoped to the viewer.
- Share browser-locale date/time formatting; keep calendar dates zone-free, timestamps local, and API/input values ISO.
- Measure real bottlenecks before adding complexity; preserve correctness and distinguish preview lab results from production performance.

## Search and results performance

- Read and apply [search-results-performance](.agents/skills/search-results-performance/SKILL.md) when changing retrieval, ranking, filtering, pagination, caching, enrichment or result loading; adding result features; improving search/results performance; or modifying their benchmarks. This includes browse, timelines, people and studios, and future media types.
- Reject known exclusions before provider fan-out, share valid metadata components, and reuse policy-specific eligibility summaries. Warm contributor searches should read evidence headers without transferring full credit payloads. Keep unknown evidence distinct from a verified negative, and private viewer state separate from shared metadata.
- Run the existing `scripts/benchmark_search.py` for affected cases before and after covered implementation changes. Compare result correctness, SQL statements/payload cost and provider requests as well as latency; use the skill's benchmark reference for scope and interpretation.
- Label/style-only edits and toggles that merely reuse existing parameters without changing requests, state lifetime or loading behavior do not require the full performance workflow. New result features still invoke the skill; use focused checks when their risk is small. Expand to the full matrix only for shared-system changes or demonstrated cross-category risk.

## Reload and first-paint stability

- Verify startup changes on the actual authenticated details and profile pages. Record the first reload from a nonzero scroll position, repeated reloads, cold/warm font caches, mobile/desktop, and back/forward. A standalone fixture or a reload at the top does not establish that the page is stable.
- Keep native reload restoration enabled on the outgoing idle document; switching it only in the incoming head misses the first reload. Let Astro own scroll restoration during route preparation and history traversal, then return to native mode on page-load completion.
- Initial list-tab alignment applies to new visits only. Neither inline initializers nor after-swap handlers may override reload/back/forward positions.
- Preload primary body/navigation fonts through the shared layout component. Do not gate essential font weights on route names or opt-in page props. Measure rendered text dimensions as well as computed font size: fallback font swaps can look like a size change and alter layout.
- Render changed components in a real page before declaring success; a successful build alone does not catch runtime template errors such as unsupported icon names. Do not hide the whole page to mask initialization or restoration errors.

## Start with the task

Read only the source files and documentation needed for the requested change.
No phase or historical document is mandatory reading.

Optional references:
- docs/agents/BACKEND.md: backend structure and important behavior.
- docs/agents/FRONTEND-STYLES.md: shared styles and layout conventions.

Treat docs/media-tracker/ as historical specifications and implementation records.
Search a relevant section only when a requirement or past decision is unclear.
Do not read the entire STATUS.md or phase plans by default.

## Testing
Tests use a separate disposable database. Never point tests at a preview or
production DB. Keep `.env.providers` and `.cloud-runtime` out of Git and logs;
server metadata keys are private environment defaults, not database seed data.

Use existing neighbouring tests as the primary guide: backend tests use Python
`unittest`, including `IsolatedAsyncioTestCase`; frontend tests use `node:test`.

## Images

- Send public images straight to the provider CDN. Use our server only when it is needed.
- Keep browser caching enabled for public images. Keep private data private.
- Reuse the same image URL and decoded image. Do not download a second size just to warm an editor.
- Show one small artwork preview quickly. Load extra gallery images only when needed.
- Reveal the right image when it is ready. Never flash the previous entry's background.
- Check real requests and repeat entry switches before calling image work done.
