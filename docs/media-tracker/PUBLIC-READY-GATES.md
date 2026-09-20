# AnyList public-ready gates

This is the current Stage Two release checklist. `STAGE-TWO-PLAN.md` is the accepted product contract; `STATUS.md` records implementation and verification details. Do not treat a local pass as evidence of a public deployment or a real-device pass.

| Gate | Current evidence | Remaining proof |
| --- | --- | --- |
| Standalone public source and licensing | [antipixelhd/AnyList](https://github.com/antipixelhd/AnyList) retains Scrob history, GPLv3, attribution and publication audit. | Keep later commits and notices in the public repository. |
| Automated backend and frontend checks | 1,137 local backend tests passed; the Astro production build passed. [Public CI on `169ab46`](https://github.com/antipixelhd/AnyList/actions/runs/35518952525) passed both jobs. | Keep CI green on the final reviewed head; [issue #1](https://github.com/antipixelhd/AnyList/issues/1). |
| Movie/series core workflows | The completed slices and their focused tests/browser checks are recorded in `STATUS.md`. | Repeat acceptance paths on the final deployed build, including empty/error/loading, large lists, privacy, dates, progress, ratings, activity, statistics and attention prompts. |
| Isolated test deployment | Public code head `f3d26dc` is on the isolated test instance. The app and retained database are healthy at `mt014`; login, PWA assets and OIDC entry passed HTTPS checks, and disposable Stremio/Nuvio push flags remain off. | Complete final authenticated interaction checks on this deployed build and record them in [issue #2](https://github.com/antipixelhd/AnyList/issues/2). |
| Desktop and phone | Chromium walkthrough covered compact lists at 2560×1440, 3840×2160 and 390×844, plus Browse at 390×844 and 320×720; tested routes had no horizontal overflow. Mobile detail, stats, fuzzy search and public profile paths were also exercised. | Verify keyboard and touch flows, Android Chrome, and real iPhone Safari. Emulation alone cannot close the phone gate; [issue #3](https://github.com/antipixelhd/AnyList/issues/3). |
| GitHub progress tracking | The repository and canonical in-tree plan/status exist. [Public-ready core milestone](https://github.com/antipixelhd/AnyList/milestone/1) and four gate issues now exist. | Link evidence and close issues only after proof. |
| Owner acceptance | The accepted contract requires a verified build presented to the owner. | Present the deployed build, address feedback, and obtain explicit core approval; [issue #4](https://github.com/antipixelhd/AnyList/issues/4). |

After core approval, ask separately whether to implement the deferred season features. Level one would prompt for a season rating on Stremio/Nuvio completion while shows remain combined and a manual whole-show score takes priority over the rated-season average. Level two would add an optional setting for separate season rows in lists. Neither is authorized for the core build.
