# Movie/TV maturity review

Date: 2026-09-20. Baseline: local commit `02ffd65`, clean working tree at inspection. Status: discovery and interview; recommendations below are not accepted requirements or implementation authorization for a redesign.

## User direction

Build on the first release toward a mature service for the owner and invited friends. Focus on movie/TV UI, UX, and reliability, using AniList and the owner's profile as a reference. Games and books remain deferred until the frontend is ready for further features.

The existing invite-only access model remains the baseline. “Public ready” does not, by itself, request open registration or a change to anonymous browsing. Proposed milestone name: **friends-ready release**; confirm its acceptance criteria in the interview.

ADR-017 currently prioritizes close AniList fidelity. “A more modern version” could change that constraint, so the degree of visual and interaction freedom needs an explicit decision.

## Inspection and limits

- Read the project handoff, product plan, decisions, glossary, implementation/follow-up records, release audit, UI references, and latest status checkpoints.
- Inspected frontend code with a read-only explorer and examined the running local preview at desktop and 390×844 phone dimensions.
- Reviewed list, quick rating, full editor, TV detail/seasons, Home, Browse, Notifications empty state, and the inherited profile reached through the new profile's Edit profile link.
- Started the existing isolated local preview. Its normal launcher prepares the preview accounts; no list/rating/provider edits were saved during this audit. Existing two-account login and read-only route/API checks passed: provider-test has 35 movie and 3 series entries; preview has 7 series entries.
- Opened the public AniList list and profile without signing in. The list reported 316 entries: 4 Watching, 289 Completed, 15 Dropped, 8 Planning. This is a useful realistic collection size, not a request to import anime.
- The live AniList DOM exposed filters, scores, progress, and profile navigation, but its list-specific styling did not render correctly in this browser session. Do not use `stage-two-anilist-desktop.png` as a visual target. Visual comparison uses the existing supplied/reference screenshots, including `anilist-list-desktop-2026.png` and editor references, plus the prior recorded live inspection.
- Did not rerun the complete backend suite, perform new provider writes, audit production runtime parity, validate real-device push, or claim a comprehensive accessibility/security audit. Historical passing tests are baseline evidence, not proof that the frontend has no bugs.

## Assessment

The application has the right broad structure: dark list surfaces, status grouping, shared owner/visitor lists, title artwork, separate personal and external scores, following, and private sync review. The remaining work is more than applying a new visual theme. Several actions are disconnected, state is lost between interactions, and older routes expose a second interface.

| Area | Reference/value to preserve | Current gap | Proposed direction |
| --- | --- | --- | --- |
| Lists | Dense, scannable title/score/progress rows and persistent profile context | Larger row spacing, hidden editing affordance, no combined-list type filter, filters/view lost on editor save | Make the list the strongest daily-use screen; persistent state and visible touch actions |
| Profile | Personal identity, favorites, activity, useful overview | Generic gradient, no banner binding, sparse totals/favorites/social page, Edit profile detour into old UI | One profile system and a deliberate identity/activity hierarchy |
| Title details | Banner/cover hierarchy, easy personal actions, useful metadata | Phone metadata precedes ratings/content; season tools and user score controls feel unrelated | Put tracking and rating ahead of secondary metadata; coherent TV progress controls |
| Ratings/editor | Fast edit with clear save behavior and stable meaning | Immediate stars, numeric editor input, and per-season numeric Save all differ; quick rating can change score mode | Shared interaction rules, explicit calculated/manual mode, usable phone and keyboard controls |
| Home | Useful overview of self/followed activity | Large greeting and caught-up sync panel before activity on phone; no watching shortcut | Decide whether daily tracking, friends, or discovery leads before choosing layout |
| Browse | Clear search and recognizable results | Primarily a search form and existing-catalog grid, limited discovery/filtering | Finish find-and-add flow first; deeper discovery only if chosen as release scope |
| Notifications/settings | Private review with understandable decisions | Technical concepts and legacy navigation remain; only empty inbox reviewed live | Clear actions and consequences, consistent shell, deeper populated/error-state audit |

## Findings requiring correction or focused verification

Paths below are relative to `media-tracker/`. Code-confirmed findings were inspected statically; they were not all reproduced through saved mutations.

### Correctness and trust

1. **Quick rating silently selects manual show scoring.** `frontend/src/components/QuickRating.astro:66` sends `rating_mode: 'manual'` unconditionally. A season-average show can therefore stop averaging merely through a quick-rate action. The title mode explanation is not updated with the score. This contradicts the existing explicit-mode-change requirement. Include clearing a calculated score in the design discussion.
2. **Score sorting retains the old value after quick rating.** `frontend/src/pages/user/[username]/[section].astro:36,87` sorts from row `data-score`; QuickRating lines 72–77 update only button data and visible text. Re-sorting can use an obsolete score until reload.
3. **Editor requests can overwrite a later editor session.** `frontend/src/components/TrackingEditor.astro:55–62,87` has no cancellation/request-identity guard. Opening A, closing while loading, then opening B allows A's late response to replace B. Reproduce with deterministic delayed responses before implementation verification; do not claim observed data corruption.
4. **Calculated preview can display the original manual score.** `TrackingEditor.astro:81` uses `current.score` after changing the selector to average. That value is the original effective score, not necessarily the average of seasons being selected.
5. **Privacy semantics differ on inherited routes for historical accounts.** Read-only code inspection found the old `friends_only` value admitted for mutual followers in the legacy profile API, whereas the new tracker treats non-public profiles as private. Settings maps that historical value to Private. Impact is conditional on stored `friends_only` profiles and use of old endpoints; audit and migrate/normalize semantics before claiming privacy consistency. This is not a live exploit finding.

### Disconnected or confusing workflows

6. **Edit profile leads to the old profile first.** The new overview links to `/profile` (`frontend/src/pages/user/[username]/index.astro:12`); `frontend/src/pages/profile/index.astro:8` redirects to the numeric route, `/profile/334` for the local account. Its shell exposes Search, Toggle Theme, legacy history/statistics, Movies/Shows/Lists navigation, and another Edit Profile link to `/user-settings` (`frontend/src/pages/profile/[id].astro:158`). Browser-confirmed; screenshot `stage-two-tracker-legacy-profile-phone.png`. Connections also generates legacy show/movie View links at `frontend/src/pages/connections.astro:5386–5399,5412–5427`.
7. **Favorites cannot be set through the new core UI.** Backend support and list/profile read surfaces exist, but no mutation control exists in the inspected title/list/editor UI. The editor omission is intentional; title actions need the missing affordance.
8. **Editor saves discard list context.** Filter, status, sort, and grid state live only in the page; `TrackingEditor.astro:116` reloads it. This breaks repeated editing from a filtered collection.
9. **Movie editor contains TV-only content.** Browser shows a disabled Episode progress field and the series-completion explanation for a movie. Hide irrelevant fields/help rather than requiring interpretation.
10. **Profile personalization is incomplete.** `frontend/src/components/ProfileHeader.astro:5` has no banner binding; the new overview contains totals, favorites, and social connections rather than a personal activity view.
11. **Anonymous rating needs sign-in intent.** The title's active Your score button is rendered independently of the authentication guards used by other title actions. Relevant when anonymous browsing is enabled; do not let a visitor discover the restriction only after attempting a save.

An additional conditional route defect: `frontend/src/middleware.ts:12` permits anonymous separate movie/series lists but omits combined `/user/{name}/list` and `tracking/profile/{name}/all`. Combined public lists therefore do not follow the same browsing contract when anonymous access is enabled.

### Visual, phone, and accessibility follow-up

12. **Quick rating uses undefined style tokens.** `frontend/src/styles/tracker.css:154–157` refers to `--line`, `--surface`, `--surface-raised`, while the shared system defines other tokens. The screenshot shows a weakly separated panel and a different close-button treatment. Correct through the shared primitives, not another special-case theme.
13. **Phone information priority needs design work.** At 390×844, search is inside the list's collapsed filters, profile/header consumes much of the first screen, and title metadata appears before ratings. The UI fits, but responsiveness should also preserve task priority.
14. **Half-star input is a phone/keyboard concern.** Its individual half targets are roughly 13.5px wide on phone and there are twenty sequential score buttons. Validate actual touch selection and keyboard flow; viewport emulation alone does not prove usability.
15. **Selected navigation state lacks semantics.** List status choices use CSS selection without pressed/current state; profile tabs lack `aria-current`. Include keyboard and screen-reader checks in acceptance.
16. **Season feedback and controls need local context.** Each season has numeric score+Save and whole-season watch/unwatch; “Refresh episode metadata” leads the section. Feedback is placed after the entire season list. Decide the intended everyday episode workflow before expanding tools.

## Proposed release gate — pending user decision

Replace “bug free” with an observable acceptance standard:

- No known data-loss, wrong-title edit, privacy, or silent rating-mode bugs.
- An invited friend can sign in, find a title, add it, update status/progress, rate it, revisit it, and follow a friend without coaching.
- Both manual tracking and any advertised supported sync workflow have understandable outcomes and recovery paths.
- Representative desktop and real phone browser checks; keyboard coverage for core flows; no hidden essential hover-only actions.
- Small/empty accounts, 300+ entries, long titles, missing artwork, many seasons, unrated/calculated ratings, loading, failures, and repeated edits are explicitly exercised.
- Filters, sorting, view, scroll, and focus behave predictably after saves and Back navigation.
- Real-device notification permission/display remains a separate check if push is a release promise.
- Short invited-friend trial, with reproducible findings triaged before declaring the milestone complete. Duration and participant count remain to be chosen.

## Interview decision tree

Settled from the user's request: movie/TV scope now, build on first release, UI/UX/reliability lead, games/books later.

Round-one frontier (historical questions; answered 2026-09-20, accepted answers in STAGE-TWO-PLAN.md):

1. **Design freedom:** preserve AniList structure with modernized controls/mobile hierarchy, or continue very close visual fidelity? Recommendation: preserve recognizable structure and density; allow justified interaction and visual improvements.
2. **Primary daily value:** personal tracking, friends' activity/taste, or discovery? Recommendation: personal tracking first; social context supports it. This governs Home and navigation decisions.
3. **Usage pattern:** predominantly automatic sync followed by rating/review, manual tracking, or both equally? Recommendation: effortless sync-assisted daily use plus a complete manual route; no requirement to connect services merely to use the app.
4. **Device contract:** which phone platforms and desktop browsers must be excellent for the invited group? Recommendation: equal task coverage on desktop and phone, with explicit real-device verification for the actual devices.
5. **Finish-line scope:** perfect existing movie/TV workflows, or require a particular missing capability before inviting friends? Recommendation: repair defects and complete broken workflows first; list any truly essential new capabilities explicitly.
6. **Acceptance standard:** adopt a measurable core-flow/reliability gate and short friend trial? Recommendation: yes; no claim of universal absence of bugs.

Downstream questions, deliberately deferred: final Home hierarchy; combined/separate list default and filters; exact row density; progress/caught-up semantics; manual/calculated rating override UX; onboarding and connections; profile/activity depth; discovery scope; branding/theme; notifications and recoverability; component boundaries and incremental implementation order. Their prerequisites are the round-one answers.

No redesign code was made in the initial review. The subsequent accepted direction is recorded in STAGE-TWO-PLAN.md. The proposed friend trial above was not adopted as a mandatory gate: the user chose explicit owner approval after agreed checks and build verification. Record accepted decisions and resolved domain terms as the interview progresses; preserve the distinction between first-release facts and new proposals.
