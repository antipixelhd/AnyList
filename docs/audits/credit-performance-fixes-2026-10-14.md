# Credit access performance improvements

Date: 2026-10-14. Implements the access fixes identified in the
[original audit](credit-performance-2026-10-14.md). Complete source credits,
provider snapshots, verified identities and personal tracking facts are retained.
Migration **mt050** is additive and was applied to the local development database.
No wipe, production deployment or development-1 reset was performed.

## Results

The original audit found multi-second searches, per-credit identity/write work,
unbounded image hydration and quadratic character matching. Search now reads a
transactionally maintained contributor summary; imports and character links use
bounded database batches; image reads return only selected URLs; character
matching uses indexed in-memory lookups.

Observed local results with PostgreSQL restricted to **two CPUs and 2 GiB RAM**:

Python ran on the Windows development host and was not CPU-capped. The database
limit and application batching controls are distinct; these are not whole-VPS
capacity measurements.

| Operation | Original audit | Implemented change |
|---|---:|---:|
| Common-name local search | 9,354 ms | 54.8 ms |
| Studio local search | 8,404 ms | 5.9 ms |
| Initial 3,000-credit import | 21,590 ms / 14,417 SQL events | 2148 ms / 62 SQL events |
| Same-document refresh | 20,790 ms / 13,213 SQL events | 971 ms / 29 SQL events |
| Cached additive repair | 11,337 ms / 10,213 SQL events | 308 ms / 17 SQL events |
| Details with 5,000 additional character images | 533 ms / 63.6 MiB traced Python allocation | 51.5 ms / 0.295 MiB |
| Project 3,000 roles for one actor | 5,700 ms / 3,620 ms maximum loop delay | 1414 ms / 46.1 ms maximum loop delay |

Read values are warm medians. Write/stress values are diagnostic observations,
not distribution estimates. The new role case added 3,000 roles to an actor
already holding **9,001 registry roles**; it is a stronger retention
stress case than the original sequence, not an identical fresh registry.
All 3,001 source credits, including the studio, were compared column-for-column
before and after refresh and additive repair. Both comparisons were equal.
The detail response for 3,000 roles retains eight visible labels; complete
source/registry roles remain stored.

## Controlled query and CPU comparison

The original query implementation from commit `439abaf` was also rerun in the
same isolated fixture **with the new canonical-root index already installed**.
This separates the benefit of the maintained search projection from the earlier
index-only improvement. Ranked result objects were asserted equal for every case.

| Query | Original query + root index | Summary query |
| Common name | 828.8 ms | 54.8 ms |
| Two letters | 835.1 ms | 74.5 ms |
| Studio | 114.9 ms | 5.9 ms |
| Exact person | 524.1 ms | 12.9 ms |
| Person alias | 692.9 ms | 4.8 ms |
| No match | 682.1 ms | 4.2 ms |

Average database cgroup CPU per request across ten sequential searches:

| Query | Original query + root index | Summary query |
|---|---:|---:|
| Common name | 1835.4 CPU ms | 63.0 CPU ms |
| Studio | 112.3 CPU ms | 4.3 CPU ms |

These counters include PostgreSQL parallel workers, background activity and the
small in-container resource probe. Ten-request batches amortize probe overhead;
they do not isolate PostgreSQL statement CPU perfectly. The common-name query
uses roughly **96.6% less database CPU** in this controlled comparison.

## Concurrent work and VPS bounds

Each warm-pool batch contained 24 operations: eight credit details, eight common
people searches and eight studio searches. Connections were warmed before these
comparisons; the database retained the same two-CPU limit throughout.

| Concurrent local-search slots per worker | Whole batch | Request p95 | Maximum loop delay | Cgroup throttled counter delta |
|---|---:|---:|---:|---:|
| Unrestricted | 521 ms | 505 ms | 67.1 ms | 2888.7 ms |
| 2 (implemented) | 300 ms | 269 ms | 13.3 ms | 0.0 ms |
| 4 | 255 ms | 197 ms | 13.6 ms | 58.2 ms |

Two slots leave capacity for tracking and background work. Four slots were
slightly faster in this sample but caused some database CPU throttling. The
implemented gate waits asynchronously and remains cancellable; it does not drop
searches or create extra worker threads. Its limit is **per application worker**,
not a fleet-wide quota. Provider network latency is outside this comparison.

Character matching has one off-loop CPU lane per worker. Large ORM reads yield
between groups of 200, and character creation/link writes use batches of 200.
The catalogue transaction lock and existing provider leases remain intact.
Provider I/O still occurs outside catalogue write transactions. No connection
pool increase, new service or persistent process cache was added.

## Persistence and invalidation

Credit inserts/upserts and character-link updates expand JSON batches inside
PostgreSQL. They avoid both per-row selects and large Python SQL expression
construction. Returning IDs detects changed credits without loading whole rows.
Equal credits keep their IDs, row versions and derived links. Existing protected
fields and sparse-field preservation still apply. New or conflicting TVDB/TMDB
bridges follow the original reconciliation checks; unrelated single-ID records
can still use bulk resolution in mixed-provider documents.

The 3,000-credit cold import advanced the metadata revision **66 times**.
Its unchanged refresh advanced it **33 times**, compared with 7,803 in the
original unchanged-refresh experiment. Cached additive repair advanced it
**0 times**. Character-link updates are actual set-based statements,
not merely driver executemany calls hiding thousands of statement triggers.
The mixed-provider regression checks both client SQL counts and server-side
revision changes to guard against that distinction.

Search summaries are maintained by statement triggers for affected contributors,
with root locks before aggregation. Credit changes, deletions, principal flags,
canonical bridges, names and aliases refresh the projection in the same
transaction. Quick search, known-principal checks and studio-page checks all use
this projection. Full source credits are never grouped during quick search.
The partial trigram index contains only contributors with qualifying credits.
GIN pending-list writes are disabled to keep lookup costs predictable.

Migration backfill uses bounded root arrays, analyzes the new canonical expression
index before planning the backfill, builds the search index after loading its
rows, and analyzes the completed projection. The statistics step matters:
without expression statistics, the large-fixture backfill selected an expensive
plan and exceeded the harness's two-minute statement deadline. That setup issue
was corrected before the committed measurements.

## Memory, storage and remaining costs

Normal character-image lookup no longer joins/hydrates related title entities or
decompresses their unrelated JSON. It selects four distinct URLs per character,
in batches of at most 256 selected IDs. Anime-filtered statistics read only the
classification fields required for filtering, before applying the candidate cap.
Current-title images retain priority; complete character galleries remain on
their dedicated endpoint.

The search projection, including indexes, occupied approximately **40.2 MiB** in
the final synthetic fixture. This is additional derived storage, not a capacity
forecast. The fixture contains additional small imports and stress rows beyond
its original 1.06-million-credit seed. It has not been compacted for this storage
observation. Database cgroup memory includes reclaimable filesystem cache and
shared buffers; `memory.current` is not per-request allocation or process RSS.
Python allocation peaks are separate `tracemalloc` measurements, also not RSS.

Large imports still perform source persistence, FK/integrity validation and
linear metadata merging; those costs are intentional. The measured 3,000-credit
refresh still had a 135 ms maximum scheduling delay in its sample.
The global write lane serializes imports, and all workers share database CPU.
New multi-ID bridge reconciliation has regression coverage but no separate
large-scale TVDB cold-bridge capacity benchmark. No real provider calls, HTTP
middleware, frontend rendering or production VPS traffic were measured.

## Verification and reproduction

Regression coverage includes canonical person/studio aliases, literal search
escaping, principal eligibility changes/deletion, projection backfill, concurrent
updates to one root, unchanged credit row versions/links, protected fields,
changed performers, mixed TVDB bridges with bulk single-ID records, complete
credit retention, image bounds, anime filtering and ambiguous/generic role rules.
Catalogue, statistics and cross-domain book/game cases are included in targeted
PostgreSQL runs. Ruff and diff whitespace checks passed.
The final targeted runs passed **43 access/principal/statistics/search/character
checks plus 3 cross-domain catalogue checks**.

- [Raw final measurements and query plans](credit-performance-fixes-2026-10-14.json).
- [Fixture/baseline harness](../../backend/scripts/audit_credit_performance.py).
- [Final validation harness](../../backend/scripts/verify_credit_performance.py).
- [Current access policy](../principal-credits.md).

Use the disposable Docker database command in the original audit (port 55449,
two CPUs, 2 GiB RAM, 512 MiB shared memory). From `backend`, with the same isolated
`DATABASE_URL` and `CREDIT_AUDIT_DATABASE_URL` environment variables:

```powershell
../.venv/Scripts/python.exe scripts/audit_credit_performance.py `
  --output ../.venv/credit-optimized.json --keep-schema
../.venv/Scripts/python.exe scripts/verify_credit_performance.py `
  --fixture ../.venv/credit-optimized.json `
  --output ../.venv/credit-final-performance.json
```

Both scripts reject other hosts/ports/database names and validate the random
audit schema. The final harness also reads Docker cgroup counters and loads the
original query implementation from Git for comparison. `--skip-reads` reuses
completed read/load measurements while rerunning CPU batches, fresh imports and
stress cases; role names/import identities are unique across runs. Stop the
throwaway container after verification. The local development migration and
all source data remain separate from this disposable database.
