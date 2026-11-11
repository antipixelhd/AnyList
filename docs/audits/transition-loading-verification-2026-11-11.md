# Transition and loading verification

The baseline and the follow-up use a 10-point rating scale. The first baseline is recorded at `9e0dc85d`. The target is 9.5 in each sampled category on desktop and mobile.

## Implementation

Detail tabs expose the selected content immediately and use a 120 ms, near-opaque finish. Browse cards keep visible content readable as it arrives. The existing silhouettes retain page-specific layouts. A slow featured artwork slot starts a single 180 ms pulse after a 120 ms visible wait. Ready artwork can paint while that pulse finishes. Fast cached artwork skips the pulse.

Destination hints preserve actual activity and upcoming-season names and the selected source poster. Own game-list progress appears in the detail frame with the same mobile spacing and child eligibility as the destination. Pending navigation links acknowledge the action, and routes without a destination frame show progress after 90 ms. CSS and profile module loading overlap while staged panels still wait for fonts and layout.

## Verification before deployment

- Production build succeeds. Astro check has 0 errors and 0 warnings, with 42 existing hints.
- Full frontend suite has 977 tests, 970 passing. Seven contributor and Steam source-extraction failures were reproduced against the task-start commit.
- Existing `scripts/benchmark_search.py` ran movie_exact, series_exact, default_sections, timeline_text, game_browse_partial_search, screen_list_rows and game_list_rows. All 63 before and 63 after samples match result identity/order, payload bytes, rows, totals, paging, pending state, SQL reads/writes and provider calls.
- A local authenticated production-build smoke check covers held movie, series and game navigation, profile switching, warm Back/Forward and immediate Seasons. First cache-bypassed and repeated warm reloads preserve actual nonzero scroll positions on movie, game and profile pages at 1440 and 390 px. Heading dimensions and native reload restoration remain stable.

## Sol-loop ledger

Five review rounds used native GPT-6.1 Sol on high. The reviewer session ended during a user interruption after round 2 and was replaced for rounds 3 through 5. One reviewer was active at a time. Round 4 approved the initial behavior diff and round 5 approved the final geometry handoff. Review token usage was unavailable.

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

## Verified frontend comparison

The reviewed production frontend was run locally against the existing authenticated development-1 account and catalogue through a localhost API bridge. The bridge forwards ordinary reads and the existing read-only projection POSTs. It rejects account/tracking writes. These measurements use existing data. The extra API hop affects request latency. GitHub queued the preview deployment without assigning a runner, so this environment provided the animation and interaction comparison while rollout waited.

Chrome used 1440 × 960 desktop and 390 × 844 touch emulation at 2× pixel density. The broad comparison captured 34 interactions on each viewport and 14 reduced-motion interactions on each viewport. There were no captured page errors or missing test entry points. The final layout adjustment then received a focused desktop/mobile replay and reduced-motion replay.

## Follow-up ratings out of 10

These are design-review judgments for the sampled Chrome flows. A 9.5 means the baseline issue is resolved in those flows and the corresponding cache, loading and interruption controls passed. Physical handset/Safari coverage and the queued VPS rollout remain separate verification gaps.

| Category | Baseline desktop / mobile | Follow-up desktop | Follow-up mobile | Observed basis |
| --- | --- | ---: | ---: | --- |
| Navigation acknowledgment | 8 / 8 | 9.5 | 9.5 | Detail frames paint promptly. Visible navigation links acquire the pending color immediately, which wins over hover. Routes retaining usable source content show the progress bar after 90 ms. Supported profile frames appear immediately and keep navigation usable. |
| Visual continuity | 8 / 6 | 9.5 | 9.5 | Tenet, Arcane and Elden Ring retain their heading bounds. Known game progress keeps the mobile tab boundary at 507 px before and after the handoff. Unknown Arcane tagline/genre content now settles the body from 444 to 469 px over the shared 120 ms finish, starting at the previous position with ready content available. |
| Skeleton fidelity and motion load | 8 / 8 | 9.5 | 9.5 | Layout-specific detail, list, grid and timeline silhouettes stay quiet. Infinite detail shimmer was removed. Only the exposed featured scene or selected viewer slide receives the brief slow-artwork pulse. Bulk optional galleries retain lazy admission. |
| Skeleton timing and cache behavior | 6 / 6 | 9.5 | 9.5 | Cached history and retained panels skip the placeholder. Fast artwork cancels its pulse before display. A visible slow slot waits 120 ms before its single 180 ms pulse. The pulse may finish over ready artwork, with no content wait. |
| Progressive content and interaction | 8 / 8 | 9.5 | 9.5 | Titles and selected source posters arrive independently. Selected detail panels expose content immediately. Delayed replacement, cancellation and profile retry tests keep navigation responsive and reject late destination commits. |
| Component entrances | 6 / 6 | 9.5 | 9.5 | Detail panel samples improved from 139–154 ms to 6–16 ms on desktop and from 174–187 ms to 36–50 ms after mobile touch dispatch. Editors and Quick Search expose their shell immediately and begin their finish near full opacity. Ready selected viewer artwork paints at full opacity. |
| Reduced motion | Unscored / 10 | 10 | 10 | All 28 broad desktop/mobile reduced-motion interactions recorded zero running viewport animations. The final geometry adjustment also passed its focused reduced-motion replay. |

## Timing and state evidence

- Broad comparison held selected real responses for at least 480 ms from request start. Actual slow network responses could exceed this gate. Warm paths used the app's caches. Search timings include the existing debounce.
- In the broad runs, Tenet detail frames appeared in 4 ms desktop and 51 ms after mobile touch dispatch. Elden Ring appeared in 11 and 54 ms. Warm Back/Forward took 24–29 ms desktop and 26 ms mobile, with no skeleton.
- The final Arcane handoff painted usable content with tabs at the old 444 px position. Samples then measured 460, 466, 468 and 469 px while the 120 ms animation completed. Its heading bounds stayed 146/210/226/34 throughout.
- The slow featured scene request was held at the browser response boundary. At 154 ms the decoded image was visible at opacity 1 while the pulse continued. At 337 ms the pulse had cleared and the image remained at opacity 1. The animation duration was 180 ms.
- A delayed Games-list response followed by a return to Movie/Series left that source selected on desktop and mobile. Late responses did not replace it. A controlled Stats read failure showed retry while leaving profile navigation usable. Retrying loaded Stats and left no hidden staged root behind.
- Authenticated Tenet, Elden Ring and profile-list first cold-font reloads and repeated warm reloads preserved nonzero scroll positions on both viewports. Heading dimensions stayed stable. One desktop profile reload differed by 1 px, consistent with pixel rounding. Native reload restoration stayed auto, and mobile document width stayed 390 px.

## Final review and checks

A fifth focused Sol round approved the metadata-to-ready layout finish found during the browser comparison. All nine Sol findings are resolved. No findings were declined. The final behavior diff is 350 additions/153 removals in 28 frontend files, compared with 229/141 in 25 files at the first review. Sol review token usage was unavailable.

The final full frontend suite has 977 tests, 970 passing and the same seven failures reproduced on the baseline. Production build and Astro check pass with 0 errors, 0 warnings and 42 existing hints. The existing seven-case benchmark comparison retains the 63 identical result/cost samples per arm described above.

The implementation was pushed in commit `1d51093c`. The final layout finish and this verification are committed separately. GitHub Actions deployment is checked after that push. Deployment status is independent of the ratings measured in the real-data frontend environment.
