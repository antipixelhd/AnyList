# Working rules

Commit changes you for completed features or tests, leave partial edits open until the user confirms them

if you make changes to the database apply the migration before ending the turn

Avoid cluttering UIs with excessive micro-copy, subtitles, helper text, badges, and tiny metadata. Prefer clean layouts with strong hierarchy, spacing, and obvious controls. If text is not necessary for understanding or action, leave it out.

Dont prefer bandade fixes but prefer concrete systems that fix the problem or implement the features

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
