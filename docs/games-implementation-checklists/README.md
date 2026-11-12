# Games implementation checklists

This folder holds versioned Games implementation checklists and sanitized verification reports. Commit and push updates with the associated work. The user authorized this repository location on26October2026; it supersedes the previous local-only requirement for these artifacts. Older planning/research documents may remain local.

## Active work

- [Beta review fixes —26October2026](beta-review-fixes-2026-10-26.md): R01–R11, all open when added. Includes acceptance criteria, reported reproduction URLs and a copyable implementation goal.

## Adding work and evidence

Use dated descriptive filenames, stable checklist IDs and explicit open/closed status. Link each completed item to its verification report and relevant commit/deployment. Keep previous verified scope distinct from newly reported failures; do not turn historical reports into competing TODO lists.

Verification reports should record:

- Requirement IDs, source revision and environment tested.
- Reproduction, root cause and implemented behavior.
- Actual authenticated user journeys and focused checks; affected before/after benchmark results where required by AGENTS.md.
- Deployment/migration status, known limitations and any untested or blocked cases.

Check off work only after its named acceptance evidence exists. Use disposable databases for tests. Keep credentials, cookies, provider keys, private runtime manifests, database dumps and raw personal data out of this folder and Git. Link sanitized artifacts instead of copying sensitive diagnostics. Do not merge beta into main without user authorization.
