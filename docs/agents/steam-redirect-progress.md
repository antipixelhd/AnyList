# Steam redirects as progress targets

The importer first applies software/type exclusions, existing canonical mappings,
reviewed components and existing progress targets. Only a recent complete exact
IGDB miss can reach the Steam Store redirect fallback. Ambiguous, partial and
failed provider evidence cannot establish a miss.

The fallback reads the initial response headers with no redirect following or
HTML body download. Only an explicit HTTPS `store.steampowered.com/app/<id>`
redirect to a different valid app ID qualifies. The destination must have one
complete exact IGDB reference to an independent, non-mobile game and cannot be
known software. Source identities, associations and exclusions are rechecked
after provider work before publication under the catalogue write lock.

The result uses `steam_progress_target`, like Black Ops II Zombies: independent
selection and app counters, a shared canonical list entry, and the existing
protected progress-retarget behavior. It does not bind the playtest Steam ID to
the released game's identity, merge catalogue roots, or schedule orphan repair.
Existing individual achievement/child-state conflicts still require review.

Redirect observations and verified misses are shared for 30 days. Database leases
prevent duplicate concurrent reads; failures defer retries for 15 minutes. Checks
run in groups of four, with the existing Steam provider budget and timeouts. There
is no new scheduled worker or schema migration. Existing mappings bypass both
redirect probing and repeated source IGDB classification.

Concurrent callers wait outside owner locks for an active redirect check, then
verify/publish its parent relationship. A 12-second wait limit returns HTTP 409
from import without finalizing the connection or losing selected apps. Preparation
reports a retryable failure; normal sync keeps its existing failure handling.

Verification (2026-11-04):

- Live Steam returned `2076040 -> 2073850` for THE FINALS PLAYTEST. Initial HEAD
  and streamed GET headers measured 333 and 178 ms in one beta-host sample. This
  is not an isolated method comparison; the second request reused a connection.
- Six initial redirect tests passed (one URL-validation unit test and five
  disposable PostgreSQL tests). Fifteen follow-up tests passed, including updated
  picker-ready coverage, legacy membership/baseline preservation, an identity
  race, existing Black Ops II component regressions and nine Steam source tests.
- Tests cover cache hits, concurrent claims, complete misses, failure cooldown and
  recovery, source exclusions, malformed evidence, ambiguous/capped/ineligible
  parents, canonical parent creation without ownership, combined progress and
  private note preservation. Ruff and diff checks passed.
- Six adjacent `game_list` benchmark samples retained all result fingerprints,
  row counts, payload sizes (36,260 bytes), 12 SQL reads and zero provider calls.
  First curated cold sample was 67.29 to 65.70 ms. These measurements do not cover
  redirect acquisition; the focused cache test verifies shared provider reads.
- Sol round 1 found S3 (should): a busy lease looked like a completed miss and
  could drop a selected app during concurrent preparation/import. Fixed by waiting
  for the shared result and preserving import state on timeout. Three focused
  PostgreSQL regressions pass for concurrent import, timeout/retry and cache reuse.
- Final picker audit found an intermediate IGDB miss could stop polling before
  redirect association completed. While preparation is active with Steam
  configured, those misses remain pending. Verified exclusions remain unavailable;
  published relationships become ready and completed misses settle unavailable.

Sol-loop completed in three rounds with `gpt-6.1-sol`; final verdict APPROVE.
S3 (should) and the final picker audit S4 are resolved; no findings declined or
outstanding. Usage was not exposed. Tracked-file counters grew from 18 insertions
/ 4 deletions to 37 / 6; the new redirect module and tests were reviewed separately
as untracked files. Final ten source/redirect unit tests and nine frontend tests
passed alongside the PostgreSQL checks above.
