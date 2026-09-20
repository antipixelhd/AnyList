# AnyList public-ready gates

This is the current Stage Two release checklist. `STAGE-TWO-PLAN.md` is the accepted product contract; `STATUS.md` records implementation and verification details. Do not treat a local pass as evidence of a public deployment or a real-device pass.

| Gate | Current evidence | Remaining proof |
| --- | --- | --- |
| Standalone public source and licensing | [antipixelhd/AnyList](https://github.com/antipixelhd/AnyList) retains Scrob history, GPLv3, attribution and publication audit. | Keep later commits and notices in the public repository. |
| Automated backend and frontend checks | 1,137 local backend tests passed; the Astro production build passed. [Public CI on `169ab46`](https://github.com/antipixelhd/AnyList/actions/runs/35518952525) passed both jobs. | Keep CI green on the final reviewed head; [issue #1](https://github.com/antipixelhd/AnyList/issues/1). |
| Movie/series core workflows | The completed slices and their focused tests/browser checks are recorded in `STATUS.md`. | Repeat acceptance paths on the final deployed build, including empty/error/loading, large lists, privacy, dates, progress, ratings, activity, statistics and attention prompts. |
| Isolated test deployment | The existing public test host was verified through the earlier `03a8277`/`02ffd65` checkpoints. | Deploy the final reviewed head to the isolated test instance, run migrations, verify source marker, auth/OIDC and both browser routes, and confirm other VPS services stay healthy; [issue #2](https://github.com/antipixelhd/AnyList/issues/2). |
| Desktop and phone | Prior Chromium checks covered 1440×900 and 390×844 with no overflow for named slices. | Verify 16:9 1440p/4K scaling, keyboard and touch flows, Android Chrome, and real iPhone Safari. Emulation alone cannot close the phone gate; [issue #3](https://github.com/antipixelhd/AnyList/issues/3). |
| GitHub progress tracking | The repository and canonical in-tree plan/status exist. [Public-ready core milestone](https://github.com/antipixelhd/AnyList/milestone/1) and four gate issues now exist. | Link evidence and close issues only after proof. |
| Owner acceptance | The accepted contract requires a verified build presented to the owner. | Present the deployed build, address feedback, and obtain explicit core approval; [issue #4](https://github.com/antipixelhd/AnyList/issues/4). |

After core approval, ask separately whether to implement the deferred season features. Level one would prompt for a season rating on Stremio/Nuvio completion while shows remain combined and a manual whole-show score takes priority over the rated-season average. Level two would add an optional setting for separate season rows in lists. Neither is authorized for the core build.
