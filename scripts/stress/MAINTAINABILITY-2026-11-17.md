# Maintainability review of the stress-test changes

Reviewed the 14 commits from `0b081db2` (the branch starting point) through
`96b6decd`. This is a source/contract review, not another capacity trial. No
VPS tests, runtime changes or schema changes were made for this review.

## Verdict

Correctness under concurrency, observability and measured efficiency improved.
Overall maintainability is mixed and approximately level with the starting
point: simpler business reads and better regression coverage are offset by
more process roles, transaction rules and some custom integration code.
Operational complexity increased. The branch has several fragile boundaries;
it should receive a focused consolidation pass before another broad redesign.

Ignoring line-ending-only changes, the range adds/removes 1,662/632 lines in
application/configuration files, 1,365/19 in tests and verification helpers,
2,075/0 in benchmark code and 827/0 in documentation. The net application
increase is about 1,030 lines. Many router edits merely make database-session
scope explicit; the raw 130-file diff overstates the architectural surface.
Neither line counts nor test counts measure maintainability on their own.

## Areas that improved

| Area | Comparison with the starting point |
| --- | --- |
| Account preparation | An explicit native-ID lookup replaces a quadratic nested scan. This is a simpler algorithm with direct parity/scaling checks. |
| Focused reads and no-op writes | Selecting required columns, reusing known metadata and avoiding identical updates make work and dependencies clearer. Most changes add little conceptual machinery. |
| Publication and concurrency correctness | Atomic credit/link publication, deterministic identity lock ordering and real race/rollback tests make several previously implicit requirements explicit. Implementation still requires transaction knowledge. |
| Shared page data | One account context and shared preference projections reduce repeated authentication and duplicated field-mapping logic. The browser notification store has more state to manage, however. |
| Job lifecycle | Structured cancellation/cleanup and a database-owned catalogue scheduler are stronger than leaving failed or duplicated tasks unobserved. The ownership protocol and extra process are an operating obligation. |
| Statistics revision | Committed SUM over transactional shards is well encapsulated and tested for rollback, snapshot isolation and reverse commit order. It is more sophisticated than one row, but the additional complexity addresses a reproduced contention defect. |

## Boundaries that are now harder to maintain

### 1. Custom API read-worker infrastructure is the largest added burden

`backend/core/read_workers.py` implements process startup, supervision, socket
allocation, route classification, HTTP proxying, streaming and fallback. It also
replaces the normal application lifespan for readers. A future startup resource
or GET route therefore needs an explicit process-role review, rather than only
an ordinary route edit. Broad path prefixes automatically classify future GETs
as reader-safe even when their lifecycle or limiting requirements differ.

The ownership checks and recovery probes are useful protection, but the small
unit suite largely mocks the worker pool. It does not automatically validate
the full route inventory or every application startup resource.

A configuration weakness was reproduced with controlled ASGI requests:
with a global two-request/minute limit and two distinct API client IPs, the
embedded case returned `[200,200]` for each client; proxying through a reader
returned `[200,200]` then `[429,429]`. Readers see the loopback client because
the proxy does not preserve the original ASGI client identity and readers use
`proxy_headers=False`. Global limits are not configured in the benchmark's
default, and the existing frontend proxy already has its own loopback-client
limitation. This is a conditional proxy/configuration weakness, not evidence
that the measured default pages were rate-limited incorrectly.

Prefer explicit application roles/lifecycles and route execution policies, with
integration coverage for resource startup, client context, rate limiting and
fallback. Standard deployment supervision should be considered when choosing
the eventual worker architecture.

### 2. Some database optimizations add non-local transaction rules

The cached-film import now coordinates `allocated`, `unclaimed`, `initial_dates`
and cleanup across the large `server_sync.sync_items` function. Success,
savepoint rollback, dates, statistics and empty membership cleanup must agree.
Tests exercise important races and invalid items, but a future edit has more
interdependent state to understand than the old per-item implementation.

Some read/enrichment helpers also now commit the supplied session before
provider waits. This releases scarce connections, but makes transaction ownership
less obvious to a future caller composing those helpers into a write flow.
The publication callback runs within the catalogue publication transaction;
future callbacks must preserve its short database-only critical section.

Encapsulate membership-batch ownership and document transaction boundaries at
the callable interface. Keep concurrency/failure behavior tests when simplifying
the internals; do not replace them with tests of a particular dictionary layout.

### 3. There are small library/planner-sensitive adapters

The rate-limit fast path reads SlowAPI's private `_default_limits` and
`_application_limits` attributes. The dependency is locked today, but upgrades
must verify this adapter rather than assuming only public API compatibility.

Browse ranking introduces custom SQL compilation coupled to expression-index
DDL. Changing the application expression requires a migration; equivalent-looking
SQL and changed filter shapes can alter index eligibility/plans. Correctness
tests and plan checks reduce the risk, but performance remains dependent on
catalogue distribution and pagination depth.

An initial concern that the browse plan test rebuilt its indexes from current
model metadata was incorrect. Its tracking fixture uses the supplied database,
and CI initializes that database with Alembic. The migrated-index plan guard
does exist. Historical migration DDL should remain frozen; importing mutable
runtime helpers into old migrations would weaken that protection.

### 4. Part of the new protection is not portable into normal CI

The new `test_sync_concurrency_pg.py` requires `SYNC_TEST_DATABASE_URL` and
hardcodes the `:55449/anylist_test_sync` address. The current CI workflow supplies
`TRACKING_TEST_DATABASE_URL` on port 5432, so these real sync race checks skip.
They passed in the dedicated lab, but that is not an automatic future guard.

Use a portable disposable-database fixture and enable those checks in CI.
Several frontend checks also slice and execute source strings using exact
markers, so harmless refactoring can break tests without changing behavior.
Prefer behavior-level boundaries where practical.

The wider suite already had failures before this branch. That is existing debt,
not caused by the performance work, but it weakens a future agent's clear
pass/fail signal. PostgreSQL migration tests are configured in CI; this review
does not claim that all database tests are skipped.

## Recommended order before further architecture work

1. Consolidate reader roles/lifecycles/routing and cover the reproduced client
   identity/global-limit case.
2. Make the real sync concurrency tests portable and part of normal CI.
3. Encapsulate membership batching and clarify helper-owned transaction commits.
4. Preserve the migrated-index, private-viewer, cancellation, reverse-commit and
   warm/provider-count controls when introducing public read projections.

The useful algorithm/query fixes should remain. A wholesale rollback would
restore measured inefficiencies and races. Further performance work should not
continue accumulating custom special cases without addressing these boundaries.

Ten existing worker/admission/lifecycle/rate-limit checks were rerun and passed.
The global-limit/client-identity experiment was a separate local ASGI probe;
no production traffic or provider/database calls were used.
