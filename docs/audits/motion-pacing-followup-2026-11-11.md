# Motion pacing follow-up — 2026-11-11

This supersedes the instant-first motion choices in the earlier transition verification. The design target is immediate acknowledgement followed by a deliberate, interruptible visual transition. Shorter time to replace a panel is not itself a higher design score.

## Motion policy

- Detail tabs use the original shared `createContentTransition`: 120 ms accelerating exit, then 180 ms entrance with the original cubic-bezier(.2,.7,.2,1), 6 px translation and .99→1 scale. This is the same implementation used before `1d51093c`.
- Statistics sections use that same original transition again, including media-filter swaps outside Overview. Overview retains its existing chart animation. Cold game/domain reads keep the outgoing data visible until the correct snapshot is available; the exit/entrance then connects the two.
- New detail visits, including cached clicks, enter the body over 225 ms with cubic-bezier(0,0,.2,1). The heading, source poster and hero are outside that animation. History traversal and native reload do not replay this entrance.
- Profile list/statistics panel entrances use the same 225 ms treatment, including retained panels. Existing fonts/layout readiness, shared initialization and cancellation stay intact.
- Asynchronous content that has actually waited at least 100 ms uses the 225 ms entrance. Fast asynchronous sections skip that loading treatment. This threshold is not a minimum network or page-load delay.
- The existing single slow-artwork pulse and direct selected-image display remain intact. No additional skeleton dwell or timer delays content delivery. The editor popup and exit remain unchanged.
- New requests, navigation and reduced motion cancel obsolete motion. Statistics reads also cancel pending section commits before taking ownership of the outgoing controls.

[MUI's current transition defaults](https://mui.com/material-ui/customization/transitions/) recommend 225 ms entering-screen and 195 ms leaving-screen, with a decelerating entrance and accelerating exit. These are motion defaults, not minimum page-load times. The explicitly requested historical tab timings take precedence over replacing them with these defaults. There is no new full-page exit wait.

## Real-page verification

Authenticated production frontend connected to existing development-1 data through the local read-only bridge. Chrome: 1440×960 desktop and 390×844 mobile emulation. This is a preview lab comparison, not a production latency claim.

- Tenet, Arcane and Elden Ring tabs: the outgoing Overview remained selected/inert during exit; Social then became selected and usable. Captured Web Animations show the original 120/180 ms timings, transforms and curves on both viewports.
- Rapid People→Overview switches settled on Overview without leaving an inert panel.
- Statistics Overview→Genres, rapid Actors→Studios, screen→game Overview/Genres, and rapid game Publishers→screen Staff all committed the correct last destination. Original 120/180 ms animation pairs were captured.
- Reduced-motion detail selection committed directly with zero tab animations.
- Seven navigation flows per viewport covered held movie/series/game detail opens, warm Back/Forward and cold profile Games/Stats. No captured page errors or missing triggers. Detail/profile entrances were 225 ms; history did not replay the detail entrance or show a skeleton.
- Destination frames appeared at 4–13 ms desktop and 15–70 ms mobile in the held detail samples. Ready detail bodies arrived at 536–762 ms desktop and 569–728 ms mobile, then animated. Responses were held for at least 480 ms and could exceed that. Warm Back/Forward took 26–31 ms without a skeleton.
- Screenshots confirm preserved headings and a restrained body entrance; mobile stats retained its layout and chart presentation.
- Cached detail clicks showed a 225 ms body entrance with zero observed skeleton frames. Returning to the retained Movie/Series list also animated without a loading frame on both viewports.
- Delayed profile responses followed by navigation back did not replace the selected source. Controlled Stats read failures retained usable navigation; retry completed without a stranded staged panel on either viewport.
- First cache-bypassed and repeated warm reloads from nonzero scroll preserved heading dimensions and native scroll restoration. Detail positions were exact on both viewports; the desktop list rounded forward by 1 px per reload (650→651→652), while mobile remained at 650.

## Assessment

For the sampled desktop/mobile flows: tab fidelity 10/10, visual continuity 9.5/10, perceived responsiveness 9.5/10, loading handoff 9.5/10, interruption correctness 9.5/10 and reduced motion 10/10. These are design judgments informed by captured behavior, not automated proof of a universal score. This focused pass does not rescore every browse, contributor or editor flow from the earlier broad audit. Physical handset/Safari testing and deployment rollout remain separate checks.

## Checks

Production build and Astro check pass (0 errors, 0 warnings; 42 existing hints). The focused motion/profile/statistics suite passes 26 tests. Final full suite: 980 tests, 973 passing, the same seven previously reproduced contributor/Steam extraction failures. The new regression covers section-transition scheduling, ordinary refreshes, preserved Overview chart behavior and stale response rejection. No requests, cache keys, retrieval or result composition changed; backend search benchmarks are unaffected.

## Sol-loop

One round with native GPT-6.1 Sol on high. Verdict: APPROVE, no blocker/should/nit findings. Sol independently ran 14 focused tests and checked deferred statistics selection, request/inert ownership and outgoing profile entrance cancellation. No findings required changes or were declined. Review token usage was unavailable. The behavior diff stayed at 69 additions/39 removals in seven files from first review through approval; this audit is additional documentation.
