# AnyList public-ready gates

This is the current Stage Two release checklist. `STAGE-TWO-PLAN.md` is the accepted product contract; `STATUS.md` records implementation and verification details. Do not treat a local pass as evidence of a public deployment or a real-device pass.

| Gate | Current evidence | Remaining proof |
| --- | --- | --- |
| Standalone public source and licensing | [antipixelhd/AnyList](https://github.com/antipixelhd/AnyList) retains Scrob history, GPLv3, attribution and publication audit. | Keep later commits and notices in the public repository. |
| Automated backend and frontend checks | 1,137 local backend tests passed; the Astro production build passed. [Public CI on deployed `504c687`](https://github.com/antipixelhd/AnyList/actions/runs/35531529981) passed both jobs. | Keep CI green on the final reviewed head; [issue #1](https://github.com/antipixelhd/AnyList/issues/1). |
| Movie/series core workflows | The completed slices and their focused tests/browser checks are recorded in `STATUS.md`. | Repeat acceptance paths on the final deployed build, including empty/error/loading, large lists, privacy, dates, progress, ratings, activity, statistics and attention prompts. |
| Owner-discovered Phase Two backlog | The mandatory findings are recorded in `STAGE-TWO-PLAN.md`: editor/list density, Home/app-bar defects, grouped delivery state, connected completion notifications/activity, desktop Fast-search composition, AniList spacing/Stats, Library action, legacy-page removal, login redesign, and rating-value integrity. | Implement every item, add focused regressions and browser evidence, update `STATUS.md`, deploy the revised public head, and repeat acceptance. The current `504c687` deployment is baseline evidence, not an approvable final build. |
| Isolated test deployment | Public code head `504c687` is on the isolated test instance after a validated private database backup. The app and retained database are healthy at `mt014`; login, PWA assets and OIDC entry passed HTTPS checks, and read-only SQL confirms disposable Stremio/Nuvio push flags remain off. | Complete final Google-authenticated interaction checks on this deployed build and record them in [issue #2](https://github.com/antipixelhd/AnyList/issues/2). |
| Desktop and phone | Chromium walkthrough covered compact lists at 2560×1440, 3840×2160 and 390×844, plus Browse at 390×844 and 320×720; tested routes had no horizontal overflow. Mobile detail, stats, fuzzy search and public profile paths were also exercised. | Verify keyboard and touch flows, Android Chrome, and real iPhone Safari. Emulation alone cannot close the phone gate; [issue #3](https://github.com/antipixelhd/AnyList/issues/3). |
| GitHub progress tracking | The repository and canonical in-tree plan/status exist. [Public-ready core milestone](https://github.com/antipixelhd/AnyList/milestone/1) and four gate issues now exist. | Link evidence and close issues only after proof. |
| Owner acceptance | Review of deployed build `504c687` produced a new mandatory backlog on 2026-09-20. | Complete and redeploy the owner-discovered backlog before requesting another Google-authenticated/physical-phone review or explicit core approval; [issue #4](https://github.com/antipixelhd/AnyList/issues/4). |

## Owner findings handoff (2026-09-20)

The full behavior contract and acceptance tests are in the **Owner acceptance findings** section of `STAGE-TWO-PLAN.md`. The checklist below is a handoff index, not a substitute for those details. **Locally implemented** means a committed local slice has focused evidence in `STATUS.md`; it does not close the release gate until the final public head is deployed and the owner verifies it. An in-progress or uncommitted visual change does not count as complete.

| Finding | Current state | What remains before acceptance |
| --- | --- | --- |
| One-viewport quick editor; no Show score control; calculated season average and confirmed override | Locally implemented | Recheck on final desktop build and with real saved season scores. |
| Remove overall list total while keeping filter status counts | Locally implemented | Recheck personal and public lists on final build. |
| Home CTA hover legibility and combined/separate Your lists links | Locally implemented | Recheck hover, keyboard focus, and both preference modes. |
| Square profile action, hover/focus-only outline, unfilled blue Fast-search icon, first-scroll hide, Settings alignment | Locally implemented | Recheck desktop and Settings routes on final build; retain keyboard and reduced-motion behavior. |
| Group pending connection updates by title and remove only after every applicable provider succeeds | Code and pure projection tests locally implemented; PostgreSQL/browser proof open | Run the new multi-provider endpoint regression and inspect success, partial failure, retry, and final removal in a working preview. |
| Newly synced Completed title creates rating request, attention prompt, and activity | Locally implemented regression | Confirm the reported Stremio manual-sync scenario with a live established connection; preserve initial-import silence. |
| AniList-like desktop Fast search with input-only opening and simultaneous nonempty category cards; unsupported on phone | Locally implemented for Movies/Series | Recheck combined/separate, empty/error, keyboard and phone boundary on final build; Games/Books await those catalogue types. |
| Denser compact rows, AniList-measured first-viewport profile/list geometry and consistent filters/type/spacing | In progress | Record reproducible reference measurements and complete desktop, 1440p/4K, and phone checks. |
| Stats visual hierarchy using icons, spacing and typography instead of repeated generic cards | Locally implemented | Recheck media/year filters, charts, summaries and accessibility on final deployed desktop and phone builds. |
| Separate Favorite from streaming Library add/remove on title details | Open | Implement honest per-provider pending/error feedback and verify remote delivery semantics. |
| Remove obsolete Scrob UI pages and redesign login in AnyList style | Login and numeric profile/Stats retirement locally implemented; other legacy-page families open | Complete `LEGACY-UI-INVENTORY.md` retirement without breaking integrations; verify deployed Google, configured password fallback, 2FA, recovery and accessibility. |
| Rated activity displays its score; legacy missing-score rows do not claim a rating | Locally implemented | Recheck the reported Interstellar-style row and scored activity on final build. |

No Phase Two approval request is due while any row above remains open or final deployment, CI, physical-device verification, and owner review remain outstanding.

After core approval, ask separately whether to implement the deferred season features. Level one would prompt for a season rating on Stremio/Nuvio completion while shows remain combined and a manual whole-show score takes priority over the rated-season average. Level two would add an optional setting for separate season rows in lists. Neither is authorized for the core build.
