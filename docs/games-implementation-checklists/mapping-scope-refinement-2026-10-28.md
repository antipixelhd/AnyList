# Mapping scope refinement — 28 October 2026

Verified and deployed to beta as `6a3614f6`. Preview Deploy 40621297143 succeeded. Main was not merged. No schema change or migration was required.

## Root causes and universal rules

Comanche shared its title with a cancelled console port whose missing or retained scheduled year made title/year ranking ambiguous. An independent past launch day must now agree across IMDb, Steam and IGDB, with exact storefront title and developer agreement. Only an explicitly cancelled port with a distinct parent and known disjoint platforms can be removed as a competing identity. Scheduled dates do not defeat this proof. Unknown evidence remains unknown.

Roger & James uses a decorated IMDb subtitle while IGDB stores its shorter child title. A bounded fallback searches the subtitle under its exact parent. Acceptance requires an explicit parent identity, exact parent title, matching substantial subtitle and a narrowly recognized character-pair decoration. Ordinary DLC resolves to an exclusion; it never becomes a tracked game or actor portfolio work. No game or person IDs are special-cased.

Normal title retrieval retains two branches; the observed decorated-title fallback uses four. Transient per-title provider failures retain completed outcomes and request retries. Shared metadata, reviewed bindings and private tracking remain separate. Review rows expose fresh exclusions without changing unbound pagination membership. Optional corroboration retains its four-second title/eight-second page budget.

## Tests and review

- 79 backend integration/regression tests passed against a separate disposable PostgreSQL instance. 57 local focused tests also passed, including cancelled-port scope, wrong platform/parent/status/date rejection, parent/subtitle identity, optional field entitlement and private-state preservation.
- Frontend review data test passed; Astro check reported zero errors and the production build succeeded.
- Read-only live provider probes resolved Comanche to IGDB 121758 and the decorated credit to IGDB 116682, excluded as DLC, without manual mapping seeds.
- Sol-loop: two rounds with the existing gpt-6.1-sol reviewer on high effort; approved. One should-fix finding resolved: apply cancellation proof before tie selection in both matching and central binding validation. A regression fixture was strengthened to retain a known scheduled year. No declined findings. Usage not exposed. Diff before/after review: 215/222 additions, 31 deletions in both.

## Controlled benchmark

Existing `scripts/benchmark_search.py`: four affected cases, three rounds, three curation profiles, 48 samples before and 48 after, simulated 50 ms provider delay. The final after run was sequential after tests; an overlapping diagnostic run was discarded. These are synthetic route/SQL timings on the shared VPS, not browser or live-provider latency.

| Case | Cold median before/after (ms) | Warm median before/after (ms) | Reads cold/warm | Provider requests cold/warm | Payload bytes |
| --- | --- | --- | --- | --- | --- |
| Ordinary title mapping | 124.84 / 131.54 | 6.42 / 7.09 | 6 / 3 | 2 / 0 | 120 unchanged |
| Deep title mapping | 256.11 / 273.83 | 10.00 / 17.05 | 14 / 5 | 4 / 0 | 125 unchanged |
| Person status | 9.49 / 15.67 | 8.62 / 11.94 | 4 / 4 | 0 / 0 | 446 unchanged |
| Admin title review | 20.88 / 36.88 | 20.90 / 33.07 | 5 / 5 | 0 / 0 | 9558 unchanged |

All result identity fingerprints, payload sizes and statement/provider counts match the baseline. Mapping writes remain 6 cold/0 warm (ordinary), 18 cold/0 warm (deep); status/review write zero. Review joins fresh classification leaves in its existing page/count query, adding measurable admin-only cost without transferring source filmographies. Status code is unchanged; shared-host timing variation also affects that control.

## Authenticated beta acceptance

Verified using the existing dedicated temporary QA account in real Chromium at desktop 1365x900 and mobile 390x844. No manual mappings, title-specific seeds or individual game visits primed the actor credits.

- Amelia Tyler (`catalogue:37799`): automatic discovery bound Comanche IMDb `tt21203286` to IGDB `121758` (work `81696`). The actor timeline displays **Comanche**, **Baroness**, **August 26** under **2021**, with correct artwork. Character text appeared without visiting the game. Desktop/mobile screenshots were inspected.
- Roger & James IMDb `tt10140898`: the real admin endpoint returns **excluded / dlc**, IGDB `116682`, title **We Happy Few: They Came From Below**, and no work binding. Desktop/mobile review pages render **Excluded · DLC** without a binding action on the row. It never appears in the actor portfolio.
- Amelia: **55 source games = 52 mapped + 3 excluded + 0 unresolved**. Actor pagination verified **24 + 24 + 4 = 52**; no horizontal overflow at either viewport. Abandon Ship and Emberville remain present.
- Neil Newbon (`catalogue:37800`): **35 source games = 32 mapped + 3 excluded + 0 unresolved**; mapped roles comprise **26 actor + 6 staff works**. Mobile actor pagination verified **24 + 2 = 26**. Evolutis: Duality and Starfinder: Afterlight remain present with character captions.
- The one-time matcher-version refresh encountered a provider timeout. The durable queue retried automatically, published completed batches and finished with no pending job. No provider errors became verified negatives. Completion took several minutes on the shared worker; warm timings above do not describe that live-provider refresh.

These totals cover the retrieved IMDb connections, not a claim that either provider's entire official filmography is complete. Future ambiguous records still require corroborated evidence.

Temporary QA admin privileges were removed, its password randomized, and its old token verified to return 401. Private browser state was deleted and the owned browser closed. The disposable container, volume and exact test-source directory were removed after archiving synthetic benchmark records locally. No credentials or raw private data are included.
