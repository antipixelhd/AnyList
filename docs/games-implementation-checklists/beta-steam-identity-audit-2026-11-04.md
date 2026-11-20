# Beta Steam identity audit — 4 November 2026

Read-only inspection of beta confirms an ongoing reconciliation gap involving legacy Steam imports. No beta data was modified by this audit.

Hades Steam root `112833` was created on 2 November at 14:22:47 UTC and owns Steam app `1145360`, imported progress and achievements. IGDB root `124615` was created on 4 November at 05:04:09 UTC, after the previous day's manual repair. It owns IGDB game `113112` and IMDb title `tt9459718`, and explicitly records Steam app `1145360` in `attributes.source_identity_candidates`. Its IGDB game refresh and the Steam root's IGDB identity refresh both fail with `identity_conflict`.

The contributor discovery paths call `game_company_works.primary_game`, which keeps the IGDB identity and demotes secondary identities to candidates. Both `GameCompanyWorks` ingestion and `GameImdbDiscovery.ingest` use this projection. This permits a new IGDB root even when its Steam candidate already belongs to a legacy Steam-only root. The company path skips binding a secondary identity owned elsewhere. Later, `GameMetadata.resolve_steam` uses both identities, and `catalogue.upsert_entity` rejects the two existing roots. That guard prevents silently moving private data, but cannot reconcile this legitimate split. Hades's recorded candidate shape matches this creation path; the audit did not establish which contributor request initiated it.

Current Steam import admission already requires a recent verified IGDB classification, reuses an existing IGDB root, and binds both identities when creating a root. This protects new imports, but does not repair legacy Steam-only roots or prevent contributor discovery from creating their counterparts.

The audit found these four explicit candidate splits:

| Game | Steam root | IGDB root | Steam app | IGDB game |
|---|---:|---:|---:|---:|
| Hades | 112833 | 124615 | 1145360 | 113112 |
| Payday 2 | 112758 | 118010 | 218620 | 2058 |
| Battleborn | 112789 | 117594 | 394230 | 7687 |
| Phasmophobia | 112815 | 115769 | 739630 | 132516 |

There are also 80 Steam identities on roots with no IGDB identity. They are not all established duplicates: missing, excluded or ambiguous provider evidence needs separate treatment. Name matching is insufficient for repair.

A durable fix needs exact, verified Steam/IGDB reconciliation before contributor-root creation, plus safe reconciliation of existing splits. It must account for private entries/children, achievement definitions and unlocks, favorites, activities, Steam checkpoints, protected fields, credits/appearances/performances, snapshots, and old detail URLs. Ambiguous mappings and conflicting private values must remain explicit conflicts. Another one-off merge alone would leave the creation gap open.

This task adds account list deletion independently. No retrieval, ranking, provider loading, or identity-reconciliation implementation was changed, so search benchmarks were not needed. Deletion is verified against disposable PostgreSQL and the rendered account page with a fake local API; beta/production lists were not erased.
