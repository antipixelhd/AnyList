# Steam game identity reconciliation — 4 November 2026

The [beta audit](beta-steam-identity-audit-2026-11-04.md) identified contributor discovery creating IGDB counterparts for legacy Steam-only imports. Company/person ingestion now retains exact IGDB-supplied Steam references before choosing the root. Provisional bulk acquisition includes those compact identity fields too, and batch-checks legacy app ownership before allocating IGDB stubs. Existing different IGDB mappings remain separate, protecting ports/editions sharing store references.

The shared reconciliation boundary accepts exact IGDB bridges only. It keeps the Steam root and its tracking, achievements, activities, favorites and checkpoints in place. Compatible public metadata, identities, credits, appearances, performances, relationships and source snapshots move onto it. Protected edits survive; conflicting protected fields, private tracking on the duplicate, incompatible types and graph collisions abort the savepoint. Superseded leases cannot publish to a removed root. Mapping payloads and provisional rows follow the retained root.

Permanent `catalogue/game_root_alias` records use the existing durable mapping store, without expiry or refresh admission. Old detail URLs redirect to the canonical game, tracking requests resolve old IDs, and share metadata retains the Games access checks. No schema migration is needed.

`backend/scripts/reconcile_steam_games.py` checks unique exact IGDB external-game references outside the catalogue lock. The default run rehearses and rolls back catalogue changes; `--apply` persists successful bridges. Ambiguous, excluded and conflicting records remain explicit. Before/after fingerprints assert that tracking, child state, favorites, activities, achievement definitions/unlocks and Steam connection state remain unchanged. A private server-local beta database backup precedes applying repairs.

Verification includes disposable PostgreSQL tests for company, person and provisional ingestion, concurrent identity recovery, graph preservation, protected/private conflicts, old tracking URLs and dry-run/apply behavior. Source normalization tests, targeted metadata/bulk tests, frontend type checking and the build also run. The broader suite exposed a stale matcher-version expectation and pre-existing Steam fixture expectations; the unmodified commit reproduces the extra app `40` cache failure.

The controlled `game_company_cold` and `game_person_cold` benchmark cases retain all 60 identities, result fingerprints, payload bytes and provider requests. These fixtures do not contain legacy Steam bridges and do not measure the added compact external-reference bytes in real IGDB replies. Normal provisional batches add at most one indexed legacy-app lookup; per-game reconciliation work is restricted to actual legacy matches. These backend fixtures do not establish VPS/browser latency.

## Applied beta verification

Commit `e12714d4` deployed successfully after retrying a CI Tailscale connectivity failure. A private server-local PostgreSQL backup preceded repair. The applied command reconciled 72 legacy Steam roots and merged 19 duplicate catalogue roots, including Hades, Payday 2, Battleborn and Phasmophobia. Before/after private-fact fingerprints matched. Two excluded applications and six unmatched applications remain unchanged.

Post-commit read-only audit found eight remaining Steam-only roots, 19 permanent aliases and zero remaining candidate splits involving a legacy Steam-only root. Authenticated requests to the four originally reported duplicate URLs returned HTTP 301 to their retained roots. The canonical Hades page returned HTTP 200 and rendered Hades.

Controlled 50ms-provider samples, baseline versus implementation:

| Case | Time | SQL reads/writes | Provider requests | Response bytes |
|---|---|---|---|---|
| Company cold, 60 games | 1144 → 1156 ms | 290/316 → 290/316 | 3 → 3 | 21033 → 21033 |
| Person cold, 60 games | 13814 → 13675 ms | 1820/1038 → 1818/1037 | 94 → 94 | 24124 → 24124 |

Both result fingerprints and complete membership match; warm reads remain 10 and 11 respectively. Small scheduler/SQL-count and timing variations are not evidence of a latency improvement.

The 11 new reconciliation tests, 35 source/normalization tests, 12 focused metadata/bulk tests, lint, frontend type check and build pass. A final three-case graph/bulk/repair smoke test also passes. The broader 88-test run was not clean: stale matcher-version and timing-dependent cooldown checks were corrected and passed focused reruns. Nine Steam-test failures remain; representative cache, initial-import and component-progress failures reproduce on the unmodified commit. Those older fixture expectations were not changed by this repair.
