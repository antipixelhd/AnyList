# Mobile game exclusion — 3 November 2026

Beta policy: keep PC/console games, including mixed mobile releases and dedicated handheld consoles. Exclude positively identified mobile-only games and mobile-specific port records. Missing platform evidence remains unknown. No private tracking is deleted.

Exact IGDB phone/PDA platform IDs verified against the current public provider response: Android 34, iOS 39, N-Gage 42, Tapwave Zodiac 44, Legacy Mobile Device 55, BlackBerry OS 73, Windows Phone 74, Windows Mobile 405, Palm OS 417. Platform names are supporting evidence; unrelated platforms containing words such as Phone are not excluded by substring.

Shared policy covers search/browse candidate normalization, cached browse eligibility, facets, title matching before support-provider fan-out, explicit IMDb discovery, company acquisition, related-card publication, tracking/detail capabilities, contributor work projections and statistics. Full platform evidence is retained separately from non-mobile presentation. Matcher version 10 records the changed candidate eligibility; older supported positive proofs remain readable. Migration mt077_mobile_game_policy aligns contributor search summaries and refresh triggers with the same stored eligibility flag.

Beta backfill classified 6,001 existing records with platform evidence; 145 were marked mobile-only. A private server-local database backup preceded the change. Existing sparse records without platform evidence were not guessed or deleted.

Reported Saints Row cases verified directly against IGDB:

- Catalogue 114856 / IGDB 825 is the 2006 Xbox 360 game. The phone tag had been aggregated from a port; primary evidence now shows Xbox 360 only.
- Catalogue 115229 / IGDB 198113 is an explicit Legacy Mobile Device port with parent IGDB 825. It is marked mobile-only and excluded as an independent game.
- Catalogue 115332 / IGDB 198115 is a standalone mobile-only Saints Row 2 entry. It is excluded.
- Catalogue 113971 / IGDB 165346 is the 2022 PC/console game and remains eligible.
- Catalogue 115311 / IGDB 126151 is a separate sparse Saints Row V provider record. IGDB supplies cover cobn65 but no platform or release date in the inspected response. Saints Row 2022 uses cover co5pxl. Similar cover art is not sufficient identity evidence; this record was not merged, guessed as mobile, or deleted.

Validation: 203 focused backend tests passed. Disposable PostgreSQL regression tests (including migration roundtrip) were added but could not run locally. Existing benchmark_search.py baseline and current runs were attempted for game_search and game_person_cold; both require an unavailable disposable PostgreSQL database. No latency, SQL-cost or provider-count improvement is claimed from those blocked benchmarks. Focused coverage verifies zero support-provider calls for mobile-only candidate sets. Beta is not used as a test database.

Sol validation also covers phone-origin games with verified PC/console ports. Discovery requests port type/platform evidence, identity observations retain it, and candidate retrieval version6 invalidates older scopes lacking that evidence. Canonical and company-port resolution preserve the explicit traversed PC-port availability without mutating shared provider data. A timing-sensitive candidate-batching test failed once and passed on a focused rerun; no batching code was changed.

Sol-loop:3 rounds with gpt-6.1-sol; final verdict APPROVE. S1 (phone-origin games with PC/console ports incorrectly excluded) was agreed and resolved across canonicalization, discovery acquisition, cached identity observations and company resolution. No findings declined. Review diff grew from17files192insertions/19deletions to18files266insertions/23deletions, principally focused regressions and the missing acquisition evidence. Reviewer effort and usage were not exposed. Migration mt077_mobile_game_policy was confirmed applied in beta; contributor index and refresh-trigger definitions both include mobile eligibility.
