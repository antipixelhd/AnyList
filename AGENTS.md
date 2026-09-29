# Working rules

Commit changes you for completed features or tests, leave partial edits open until the user confirms them

if you make changes to the database apply the migration before ending the turn

Avoid cluttering UIs with excessive micro-copy, subtitles, helper text, badges, and tiny metadata. Prefer clean layouts with strong hierarchy, spacing, and obvious controls. If text is not necessary for understanding or action, leave it out.


## Start with the task

Read only the source files and documentation needed for the requested change.
No phase or historical document is mandatory reading.

Optional references:
- docs/agents/BACKEND.md: backend structure and important behavior.
- docs/agents/FRONTEND-STYLES.md: shared styles and layout conventions.
- docs/agents/HANDOFF.md: how to continue unfinished work.

Treat docs/media-tracker/ as historical specifications and implementation records.
Search a relevant section only when a requirement or past decision is unclear.
Do not read the entire STATUS.md or phase plans by default.

## Testing

Use existing neighbouring tests as the primary guide: backend tests use Python
`unittest`, including `IsolatedAsyncioTestCase`; frontend tests use `node:test`.

- Keep testing proportional to the change. Add regression tests for meaningful
  behavior changes and bugs; avoid tests that merely mirror implementation.
- Assert observable results, persisted state, and relevant side effects. A test
  should fail when the behavior it protects breaks; presence or truthiness alone
  is insufficient when an exact value or outcome matters.
- Cover distinct boundaries and failure paths relevant to the change. For sync
  and delivery behavior, consider retries, duplicate processing, partial failure,
  and isolation between users when applicable.
- Mock external services at their boundaries. Keep the behavior under test real,
  and verify important interactions without coupling assertions to incidental
  internal calls.
- When reviewing gaps, name a plausible production bug and check whether an
  existing assertion would catch it. Distinguish gaps established by inspection
  from mutation survivors verified by execution; restore any temporary mutations.
- Keep tests deterministic and independent. Avoid arbitrary sleeps, shared state,
  swallowed failures, and unexpected network access.
- Run the narrowest relevant tests and required project checks. Report what ran
  and any limitations; a successful build alone does not prove tests executed.
- Keep reviews focused on actionable findings with file locations. Use test
  metrics, formal smell taxonomies, tagging, or planning artifacts only when they
  serve the requested task.
