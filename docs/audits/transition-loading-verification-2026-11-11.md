# Transition and loading verification

The baseline and the follow-up use a 10-point rating scale. The first baseline is recorded at `9e0dc85d`. The target is 9.5 in each sampled category on desktop and mobile.

## Implementation

Detail tabs expose the selected content immediately and use a 120 ms, near-opaque finish. Browse cards keep visible content readable as it arrives. The existing silhouettes retain page-specific layouts. A slow featured artwork slot starts a single 180 ms pulse after a 120 ms visible wait. Ready artwork can paint while that pulse finishes. Fast cached artwork skips the pulse.

Destination hints preserve actual activity and upcoming-season names and the selected source poster. Own game-list progress appears in the detail frame with the same mobile spacing and child eligibility as the destination. Pending navigation links acknowledge the action, and routes without a destination frame show progress after 90 ms. CSS and profile module loading overlap while staged panels still wait for fonts and layout.

## Verification before deployment

- Production build succeeds. Astro check has 0 errors and 0 warnings, with 42 existing hints.
- Full frontend suite has 976 tests, 969 passing. Seven contributor and Steam source-extraction failures were reproduced against the task-start commit.
- Existing `scripts/benchmark_search.py` ran movie_exact, series_exact, default_sections, timeline_text, game_browse_partial_search, screen_list_rows and game_list_rows. All 63 before and 63 after samples match result identity/order, payload bytes, rows, totals, paging, pending state, SQL reads/writes and provider calls.
- A local authenticated production-build smoke check covers held movie, series and game navigation, profile switching, warm Back/Forward and immediate Seasons. First cache-bypassed and repeated warm reloads preserve actual nonzero scroll positions on movie, game and profile pages at 1440 and 390 px. Heading dimensions and native reload restoration remain stable.

## Sol-loop ledger

Four review rounds used native GPT-6.1 Sol on high. The reviewer session ended during a user interruption after round 2 and was replaced for rounds 3 and 4. One reviewer was active at a time. Round 4 approved the behavior diff. Review token usage was unavailable.

| Finding | Outcome |
| --- | --- |
| S1 pending navigation color overridden by hover | Fixed with precedence in the app-bar interaction stylesheet. |
| S2 image-only Favorites/Library lost destination title | Fixed by retaining literal title/ARIA and the clicked poster fallback. |
| S3 ready scene/viewer artwork faded from zero | Fixed by painting selected artwork immediately and retaining a near-opaque finish for other thumbnails. |
| S4 offscreen slow scene permanently missed its pulse | Fixed by starting its delay when the pending slot becomes visible. |
| S5 staged profile fonts could swap after handoff | Fixed by retaining font readiness while overlapping CSS and module loads. |
| S6 desktop/child game progress geometry | Fixed by mirroring destination eligibility and showing known progress on both viewports. |
| S7 rejected module import left a late staged root | Fixed by settling the module and layout branches before cleanup, with a regression. |
| S8 upcoming-season text link used the season label as title | Fixed by sharing canonical title/kind hints on the card. |
| S9 rapid return reused initialization without the new layout barrier | Fixed by explicitly joining both operations at the handoff, with a regression. |

The behavior diff grew from 229 additions/141 removals in 25 files at first review to 328 additions/150 removals in 28 files at approval. Findings were addressed within the requested transitions and loading scope.

## Deployment comparison

Authenticated development-1 measurements and final category ratings are recorded after deployment.
