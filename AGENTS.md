# Working rules

Commit changes you for completed features or tests, leave partial edits open until the user confirms them

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
- Resolve people through verified identities and show works for meaningful credited roles; incidental acknowledgments should not populate works timelines.
- Keep reads focused, share concurrent requests, and reuse cached enrichment. Bound expensive work and keep it off the event loop on the shared VPS.
- Make page initialization repeat-safe and clean up observers, timers, and requests when views change. Keep private caches scoped to the viewer.
- Measure real bottlenecks before adding complexity; preserve correctness and distinguish preview lab results from production performance.

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

For Linux Codex Cloud tasks, use `bash ops/cloud/setup.sh` for installation,
`bash ops/cloud/start.sh` for services, and `bash ops/cloud/check.sh` for checks.
Tests use a separate disposable database. Never point tests at a preview or
production DB. Keep `.env.providers` and `.cloud-runtime` out of Git and logs;
server metadata keys are private environment defaults, not database seed data.

Use existing neighbouring tests as the primary guide: backend tests use Python
`unittest`, including `IsolatedAsyncioTestCase`; frontend tests use `node:test`.
