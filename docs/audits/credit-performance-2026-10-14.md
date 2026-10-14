# Full-credit performance audit

Date: 2026-10-14. Application baseline: `439abaf` on `beta`.

**The current implementation does not scale adequately for contributor search or
large credit imports. Keeping full credits is compatible with a faster system,
but limiting displayed people is insufficient.** Ordinary credit detail reads
are efficient in the representative fixture. Search, ingestion, character-image
fan-out and character matching need separate bounds and access patterns.

This was an audit. No application code, production indexes, development data or
deployment configuration was changed. All index and write experiments ran in a
disposable PostgreSQL database. No database wipe or development-1 operation was
needed. The existing image-dismiss edits were excluded.

## Method and limits

- PostgreSQL 16 in Docker, limited to two CPUs and 2 GiB RAM; Python 3.13.16 on
  Windows. The corrected container used 512 MiB shared memory. The application
  connection pool matched the configured 20 connections plus 10 overflow.
- The fixture schema ran every actual Alembic upgrade, including integrity and
  statistics-invalidation triggers. Measured ingestion retained those triggers.
  Bulk fixture creation bypassed user triggers only during setup, keeping FK
  constraints; triggers were re-enabled before measurement.
- Synthetic provider-shaped data, not a production copy or a calibrated sample
  of TMDB popularity. Two catalogue sizes: 1,000 titles / 100,000 people /
  **212,000 credits**, and 5,000 titles / 200,000 people / **1,060,000 credits**.
  Each title had 40 cast credits, 160 crew credits, ten second-provider cast
  associations and two company associations resolving to one studio. Fixtures
  included person/company aliases, repeated contributors, principal flags,
  character images and metadata snapshots.
- Normalization and application ingestion used the real functions. Local search
  exercised `local_matches` and final ranking; detail reads exercised
  `work_details(..., extras=False)`. A separate full contributor-search invocation
  used two synthetic provider responses with 50 ms latency each. **No real API
  calls were made.** Authentication, HTTP middleware, user settings reads,
  frontend rendering, provider reliability and production network latency were
  not benchmarked.
- Twelve warm samples per baseline read, eight per ordinary index experiment,
  three for its unchanged control, five for image fan-out. Reported p95 is the
  nearest-rank sample statistic; these small samples are diagnostic, not an SLA.
  Cache state was first-touch/warm, not a controlled cold-disk experiment.
- SQL counts are SQLAlchemy execution events. They exclude statements executed
  inside PostgreSQL triggers and do not expand an executemany event into its
  individual server statements. SQL timing includes driver/network time.
- Python memory numbers use `tracemalloc` in separate instrumented passes. They
  describe Python allocations, not process RSS or database memory. Instrumented
  timing is not compared with ordinary latency. Normalization memory includes
  constructing its source input; additive repair memory excludes the already
  allocated input document.
- Initial high concurrency exhausted Docker's default 64 MiB shared memory.
  That result was excluded from the final load comparison. The synthetic schema
  was backed up/restored into the corrected container and tests were rerun.
  A bulk fixture-load timeout also left free/dead space; the controlled index
  comparison compacted and analyzed the fixture before its unchanged control.
  Thus use that control, rather than the earlier baseline, for index speedups.

These are local measurements of scaling and access patterns, **not estimates of
production response times or demonstrated production capacity**.

## Baseline reads

Warm medians, milliseconds:

| Operation | 212,000 credits | 1,060,000 credits | SQL executions |
|---|---:|---:|---:|
| Credit details | 14.9 | 16.2 | 5 |
| Common-name people search | 1,594.7 | 9,353.5 | 2 |
| Studio search | 1,656.3 | 8,403.7 | 2 |
| Exact person name | 110.0 | 244.3 | 2 |
| Company alias | 50.1 | 91.0 | 2 |
| Person alias | 177.6 | 358.3 | 2 |
| No match | 153.6 | 282.7 | 1 |

The detail path used indexed title/character lookups and returned bounded people
in this fixture. A separate ingested 3,001-row source document still produced
32 detail people and about 10.6 KB of JSON in a 27.3 ms single read.

Search was dominated by its second query, which recovers roles for the selected
canonical people/companies. `EXPLAIN (ANALYZE, BUFFERS)` showed parallel credit
table scans and qualification/regex checks across the catalogue before resolving
the selected canonical IDs. At the large size this query took about 8.6 seconds
for people and 8.4 seconds for studios in the captured plans. The final result
limit did not bound the database work.

The first people query also remained expensive: it joins and groups matching
principal credits, counts distinct works, carries whole entity rows, and sorts
before limiting candidates. The indexed experiment's common-name plan still
processed about 201,000 qualifying credit rows, grouped 25,022 matching people,
and used external merge sorts. Filtering stored principal flags prevents object
hydration of every person, but does not make that query cheap.

## Controlled search experiments

The same source credits remained stored. Result keys and result counts were
asserted unchanged between experimental stages.

Warm medians, milliseconds, at the large size:

| Stage | Common name | Two letters | Studios | Exact person | Company alias |
|---|---:|---:|---:|---:|---:|
| Unchanged, compacted control | 8,476 | 8,771 | 7,675 | 546 | 93 |
| Canonical contributor expression index | 886 | 905 | 111 | 187 | 17 |
| Also add name/alias trigrams | 888 | 998 | 110 | 195 | 18 |
| Also add principal-credit partial index | 897 | 1,011 | 111 | 193 | 16 |
| Also load only needed search entity columns | 677 | 589 | 107 | 519 | 17 |

The expression index was on
`coalesce(canonical_person_id, canonical_company_id, id)`. It reduced the captured
people-role query to about **7 ms**, while the first query still cost about
843 ms. This is strong evidence that retaining full credits is not, by itself,
the cause of the role-query failure.

The tested trigram and principal partial indexes added little benefit under the
existing joins, alias-subquery `OR` and legacy fallback predicate. Adding indexes
without changing that query shape is not a complete remedy. Narrow projection
reduced common-name work but did not improve every case; exact-name latency was
planner-sensitive and regressed in that stage.

The full contributor-search function exposed two more canonical expressions:
`coalesce(canonical_person_id,id)` and `coalesce(canonical_company_id,id)`.
Optimizing only the three-argument expression did not optimize those checks.
With narrow selection and the earlier experimental indexes already present,
the stubbed full search had a 1,722 ms median. Adding the two matching expression
indexes reduced that to 540 ms. It issued five SQL executions and exactly one
people and one company provider call per search; there was no profile fan-out.
The 50 ms stub is not a measurement of TMDB response time.

## Concurrent reads

Each batch contained 24 operations: eight credit detail reads, eight common-name
searches and eight studio searches, with fresh sessions. Concurrency was limited
to eight or 24. Baseline operations had a **20-second audit deadline**; timed-out
latencies are censored, not successful 20-second requests.

| Catalogue / stage | Concurrency | Deadlines exceeded | Whole batch |
|---|---:|---:|---:|
| 212,000 credits, baseline | 8 | 4 / 24 | 32.4 s |
| 212,000 credits, baseline | 24 | 14 / 24 | 20.3 s |
| 1,060,000 credits, baseline | 8 | 16 / 24 | 40.9 s |
| 1,060,000 credits, baseline | 24 | 16 / 24 | 20.5 s |
| Large catalogue, all tested indexes + narrow projection | 8 | 0 / 24 | 8.1 s |
| Large catalogue, all tested indexes + narrow projection | 24 | 0 / 24 | 7.4 s |

Even the indexed experiment is insufficient as a finished quick-search system:
service p95 remained about 6.1 seconds at concurrency eight and 7.4 seconds at
concurrency 24. Queue-inclusive p95 was about 7.9 and 7.4 seconds respectively.
The expensive candidate aggregation still consumes shared database capacity.

## Ingestion and repeated identity resolution

Each synthetic series source had the listed number of people credits plus one
studio credit. Cast contributors repeated across three named roles; crew
contributors repeated across two jobs. Full credits were retained.

| Input people credits | Initial import | SQL executions | Same-document refresh | SQL executions | Additive cached-document repair | SQL executions |
|---|---:|---:|---:|---:|---:|---:|
| 100 | 0.75 s | 498 | 0.73 s | 453 | 0.41 s | 353 |
| 1,000 | 7.22 s | 4,816 | 6.69 s | 4,413 | 3.67 s | 3,413 |
| 3,000 | 21.59 s | 14,417 | 20.79 s | 13,213 | 11.34 s | 10,213 |

The 3,000-credit initial import made 6,004 identity SELECTs; additive repair made
9,006. `upsert_entity` repeats identity lookups/binds for credits belonging to the
same person. `upsert` writes an individual credit and selects it back, even when
the caller ignores the returned credit. Additive repair prepares the contributor
and then resolves it again through `upsert_entity`.

Pure normalization/selection was about 1 ms / 10 ms / 33 ms for 100 / 1,000 /
3,000 input credits. It was not the dominant ordinary import cost. Traced input
construction plus normalization peaked at 2.4 MiB for 1,000, 7.5 MiB for 3,000
and 25.5 MiB for 10,000. Additive repair's separate peak was about 12.1 MiB for
3,000, excluding the existing input document.

### Write experiment preserving the full source

A document-local cache reused only **identical contributor records**, retaining
the existing identity checks and field merges. Credit upserts were then grouped
by supplied columns and written in batches of 200, followed by the existing
character projection. This was a temporary experiment, not shipped code.

| People credits | Unchanged refresh | Identical-contributor reuse | Reuse + credit batches | SQL executions before → after |
|---|---:|---:|---:|---:|
| 1,000 | 6.78 s | 5.73 s | 2.36 s | 4,413 → 1,217 |
| 3,000 | 21.33 s | 16.83 s | 6.90 s | 13,213 → 3,627 |

All credit columns were compared before and after, including IDs, contributors,
source keys, roles, character links and attributes. Both comparisons were equal;
all 1,001 / 3,001 credit rows remained and both detail responses had 32 people.
This verifies unchanged-document state preservation in this fixture, **not the
correctness of a production bulk writer for all conflict/protection cases**.

An unchanged 3,000-credit refresh also advanced the global metadata revision
**7,803 times** through statement triggers. The batched experiment reduced that
to 1,833; resetting/rebuilding actor-character links still caused writes.
Avoidable no-op writes have invalidation and MVCC costs beyond visible SQL counts.

Four simultaneous 300-credit imports took 8.82 seconds as a batch. Their write
lock waits were approximately 0.03, 2.20, 4.37 and 6.54 seconds. The transaction-wide
catalogue advisory lock serializes imports. Reducing work while holding it is a
safer first step than removing concurrency protection around shared identities.

## Bounds missing beyond the people count

### Character-image fan-out

`character_images` loads every matching credit plus the related work entity,
including work attributes. `role_image_candidates` chooses up to four images
afterwards. A bounded set of selected character IDs therefore does not bound
matching source rows or hydrated metadata.

An adversarial fixture reused one selected actor/character across many titles.
Related works had about 5 KB of synopsis/gallery metadata. The displayed cast and
crew stayed at 32 people throughout. This is an upper-bound test, not a claim
that a typical character appears in thousands of films.

| Matching added source images | Warm detail median | Separate Python allocation peak |
|---|---:|---:|
| 100 | 31 ms | 1.6 MiB |
| 1,000 | 119 ms | 12.8 MiB |
| 5,000 | 533 ms | 63.6 MiB |

The final image query returned 5,003 rows in the largest case. PostgreSQL itself
took about 15 ms for that query in the captured plan; transferring and hydrating
unneeded objects/JSON dominated the application read.

### Many roles for one selected person

The person cap also does not bound role associations. One actor with 3,000
distinct roles remained one selected person, but all 3,000 credits/role labels
were processed. The response contained all labels and was about 68 KB.

`project_characters` scans an actor's registry list for each credit, including
alias matching when no exact role exists. With 100 / 1,000 / 3,000 roles in the
stress sequence, projection took 94 ms / 1.08 s / 5.70 s. The largest case caused
a **3.62-second maximum event-loop delay**. Some prior roles were already in the
registry by the later tests. This is a stress sequence, not an isolated curve
fit, but the code's repeated list scans and the observed stall establish a
separate CPU risk. Its detail read was 184 ms despite returning one person.

## Storage

After compaction, the large fixture plus its small ingestion scenarios used
approximately 397.5 MiB for credits including indexes, 159.3 MiB for entities,
31.3 MiB for character records and 6.1 MiB for snapshots.

These are fixture-specific sizes, not a storage forecast. Snapshot padding was
highly compressible and biographies were about 500 bytes; real provider payloads,
people profiles and galleries vary. An earlier pre-compaction credit size of
532 MiB included space from the failed bulk fixture load and must not be used as
live-data size. No disk-capacity conclusion requires deleting source credits.

## Recommended implementation order

1. **Fix search access and candidate aggregation.** Use a consistent indexed
   canonical-ID expression or an equivalent canonical read projection across
   local search, known-person checks and studio checks. Then remove full-credit
   grouping from every keystroke: evaluate a maintained principal-contributor
   search projection with distinct-work counts and searchable aliases, or a
   candidate-first query that uses direct indexed contributor IDs. Preserve exact
   identity semantics and validate exact, short, common and alias queries. A
   precomputed search projection was not implemented/measured in this audit.
2. **Batch identity resolution and credit persistence.** Resolve identities in
   bounded sets; merge each distinct contributor record correctly; batch credit
   upserts and avoid selecting ignored results back. Preserve protected fields,
   conflicting-provider rejection, source-key identity and authoritative removal
   behavior. Detect unchanged records/character links rather than clearing and
   rewriting them. The prototype demonstrates headroom but still takes seconds.
3. **Bound image results in SQL.** Return only the required candidate URLs and
   minimal fields per selected character, ordered to preserve current-title and
   provider preference. Load complete galleries on demand. Retain every source
   image association in storage.
4. **Make character projection scale with useful work.** Use keyed role/alias
   lookups preserving ambiguity rules, rather than repeated full registry scans.
   Defer unnecessary derived character work and move substantial pure matching
   off the event loop. Bound presentation of role labels independently of the
   selected-person limit while preserving the complete role data.
5. **Reassess concurrency after shortening writes.** Keep provider I/O outside
   write transactions and shared refresh/lease protection. Measure before
   changing lock granularity; the global revision counter is another shared
   write point. Benchmark the full HTTP path on the VPS after changes.

The measurements support fixing these access patterns before deciding to discard
structured credits. Keeping full provider snapshots and maintaining only a
principal canonical/search projection is another possible storage/ingestion
design if batching is still too costly; its maintenance and feature tradeoffs
were not benchmarked here. Neither approach requires a database wipe.

## Evidence and reproduction

- [Committed measurements and selected query plans](credit-performance-2026-10-14.json).
- [Baseline harness](../../backend/scripts/audit_credit_performance.py).
- [Isolated experiments](../../backend/scripts/audit_credit_experiments.py).
- Full local raw results: `.venv/credit-audit-baseline.json` and
  `.venv/credit-audit-experiments.json` (not committed).

From the repository root, start only the disposable audit database:

```powershell
docker run -d --rm --name anylist-credit-audit `
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=anylist_test `
  -p 127.0.0.1:55449:5432 --cpus=2 --memory=2g --shm-size=512m postgres:16-alpine
```

Wait for PostgreSQL readiness. From `backend`, using the project's environment:

```powershell
$env:PYTHONPATH='.'
$env:SECRET_KEY='credit-audit-only'
$env:DATABASE_URL='postgresql+asyncpg://test:test@127.0.0.1:55449/anylist_test'
$env:CREDIT_AUDIT_DATABASE_URL=$env:DATABASE_URL
../.venv/Scripts/python.exe scripts/audit_credit_performance.py `
  --output ../.venv/credit-audit-baseline.json --commit 439abaf --keep-schema
../.venv/Scripts/python.exe scripts/audit_credit_experiments.py `
  --baseline ../.venv/credit-audit-baseline.json `
  --output ../.venv/credit-audit-experiments.json
```

Both scripts reject non-loopback databases, ports other than 55449, and database
names other than `anylist_test`. They create/use only a validated randomly named
audit schema. The preserved schema is intentional for the second command.
Stop `anylist-credit-audit` afterwards; `--rm` removes this disposable database.

The full measurements executed successfully. The index stages asserted unchanged
search results; write experiments asserted unchanged full credit state. Both
harnesses also passed Ruff and Python compilation checks. No application
performance fix is included in this audit commit.
