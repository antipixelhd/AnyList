# Manual game health

Steam imports require a canonical IGDB identity on every new membership path,
including reviewed component targets and automatic additions. Exact, recent,
complete IGDB references create both identities atomically or reuse the existing
canonical root under the catalogue write lock. Provider work remains outside the
owner/connection locks, with ownership and generation revalidated before saving.
A capped component response cannot establish a unique target.
Component retargeting validates the canonical destination before moving existing
memberships, favorites or checkpoints, including ordinary progress sync.

Discovery reuses a verified legacy Steam root before allocating a new root.
If both roots already exist, ordinary discovery and metadata writes do not merge
them. Admin Data Health provides an explicit scan of up to 100 orphan records,
keyset pagination, reviewable exact matches and a separate confirmation to apply.
Application accepts a completed server-stored scan no older than one hour and
rechecks identity ownership inside the catalogue lock. Private or protected
conflicts are reported without merging. Permanent old-URL aliases remain intact.

Imports, detail visits and scheduled metadata workers no longer queue or run
legacy orphan repair. Migration `mt078_manual_game_health` retires their pending
jobs; normal canonical metadata enrichment continues. The operator CLI remains
an explicit alternative. Opening/rechecking Data Health only reads local counts.

## Verification (2026-11-04)

- Disposable PostgreSQL: 35 Steam importer tests, 20 manual health/identity bridge
  tests, six focused metadata/admin regressions, the capped component regression
  and migration retirement test passed. Sol's retarget finding added an existing
  membership/favorite/checkpoint regression for initial, auto-add and normal sync.
  Tests never use beta's database.
- Frontend: four Data Health tests, Astro check (zero errors/warnings), production
  build and focused Ruff checks passed.
- Built authenticated admin page with disposable API fixture: desktop/mobile
  scan results, cancellation without an apply request, confirmed apply using the
  server scan ID, disabled repeat reconciliation, no browser errors or mobile
  horizontal overflow. This is UI behavior coverage, not live provider timing.
- Controlled 60-work cold company/person benchmarks retained all result identity
  fingerprints, payload sizes and provider request counts (3/94). Company SQL
  reads/writes stayed 290/316; person 1828/1042 to 1822/1039. Warm reads stayed
  10/11. Single-sample latency was company 1.133 to 1.162 seconds and person
  12.986 to 14.332 seconds; these lab samples do not establish a latency change.

## Sol review

Two rounds with `gpt-6.1-sol`; final verdict APPROVE. S1 (should) identified
component retarget mutation before canonical admission. Fixed with validation
before retarget and before grouping/checkpoint writes; the new regression first
reproduced the failure and then passed for initial, auto-add and normal sync.
Three existing canonical component regressions also passed. No findings declined
or outstanding. Usage was not exposed. Tracked-file review diff grew from
277 insertions / 393 deletions to 286 / 394; new feature, migration and test files
were included in both reviews but excluded from those unstaged Git counters.
