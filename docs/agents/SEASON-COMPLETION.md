# Season completion

`series_seasons` describes a shared season in an episode order. Its membership
hash identifies the canonical episode IDs, so a changed grouping cannot inherit
an unrelated manual date. Metadata remains public and season tracking private.
`season_tracking` retains completion, current coverage, date provenance and a
protected manual date (including an explicit unknown date). Previous membership
records survive catalogue corrections. Episodes remain the source of watch time
and rewatch attribution; season cards do not create watch events.

Migration mt086 installs transactional dirty receipts for episode watches,
tracked entries, episode metadata, selected orders and position mappings. This
covers direct SQL and bulk adapters as well as ORM writes. Initial installation
queues existing tracked series. The lifespan scheduler drains up to 20 titles
per batch, committing each independently. Idle polling takes ten seconds; pending
work continues after one second. Failed receipts survive and do not prevent the
rest of the batch from running. No provider calls run in reconciliation.

Completion requires a known full season with every required episode released
and watched. Dates use the earliest reliable watch of each episode and the date
when that coverage first became complete. If coverage is known but some dates
are missing, reliable dates from the final three episodes can provide an inferred
date. Shared, provisional and inferred episode clocks cannot independently date
a season. Without usable evidence the completed season has an unknown date and
no dated timeline card. A later reliable correction updates the same milestone;
deleting evidence recalculates it against surviving watches.

Each dated completion owns `window_key=season:<id>`. Ordinary activity writers
must exclude these cards from title-level window merging. Legacy season actions
are removed from bundled activity during reconciliation, preserving other actions.
An overlapping title-completion action is absorbed when it falls on the final
season's completion day. Changing episode order recalculates private coverage
and removes old-order cards without publishing new-order milestones. Metadata
membership changes preserve historical completions without duplicating them.

Owner endpoints:

- `GET /tracking/entry/{media_id}/seasons`: saved current season tracking.
- `PATCH /tracking/entry/{media_id}/seasons/{season_id}` with
  `{"completion_date":"2026-10-01"}` sets a protected date;
  `{"completion_date":null}` explicitly clears it;
  `{"automatic":true}` restores evidence-based dating.

Date edits never rewrite episode history. These endpoints intentionally do not
change the list editor or statistics. Existing scores and rewatch data retain
their existing storage until those interfaces are integrated in a later pass.

Verification: `test_season_completion` uses a disposable PostgreSQL schema and
applies the actual migration, including its triggers. Run with an explicit
`STATISTICS_TEST_DATABASE_URL` ending in `/anylist_test`.
