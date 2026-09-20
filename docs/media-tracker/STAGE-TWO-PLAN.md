# Stage two: movie/TV UI and UX maturity

Status: active implementation, 2026-09-20. The user authorized Stage Two after confirming the consolidated contract. See STAGE-TWO-REVIEW.md for the initial evidence and the implementation progress below for completed slices.

## Implementation progress

### Correctness foundation — quick rating and editor

- Quick rating no longer silently switches a series from rated-season averaging to a manual whole-show rating. Choosing a score while averaging now presents the exact consequence and requires a separate confirmation; cancelling restores the saved score without writing.
- Manual quick ratings preserve the current rating mode, update row sort metadata immediately, refresh visible score/mode labels, and notify the active list sorter.
- Replaced the malformed text close control with the shared icon, fixed undefined design tokens, and corrected half-star clipping at every half-point.
- The list editor now aborts superseded loads and ignores stale responses, hides series-only progress/help for movies, and calculates its season-average preview from explicitly rated regular seasons rather than a potentially stale effective show score.
- Headless UI was evaluated from its current official documentation. Its supported component packages are React and Vue (Vue 3); adding either runtime solely for primitives would work against the established Astro-native architecture. Stage Two will keep framework-neutral Astro components and native browser semantics, styled through the shared Tailwind/design-token layer. Reconsider only if a later feature already justifies React or Vue islands.
- Verification: Astro production build passed; authenticated local page/API verification passed for both test accounts; headless browser confirmed the normal quick-rating dialog and the calculated-score override/cancel path. No provider writes were performed.

### Privacy and public-route consistency

- Anonymous navigation now recognizes the current combined list, Social, future Stats route, and combined profile API alongside separate movie/series lists. The backend still makes the final privacy decision for every response.
- Legacy `friends_only` profiles are consistently treated as private. Mutual follows no longer expose inherited profile endpoints while the current settings UI offers only Public and Private.
- Verification: the focused legacy-privacy regression passed and the Astro production build passed. A broader tracking test run against the populated preview database was intentionally discarded because scheduled-catalogue assertions observed four real preview series outside the fixture; this was an environment isolation issue, not a privacy failure.

### List defaults and status dates

- Profile settings now expose a saved default sort (Title, Score, Progress, or Recently updated). It initializes every personal or public movie/series list viewed by that user without changing the profile owner's combined/separate presentation.
- Explicit transitions to Watching default a missing start date; explicit transitions to Completed default a missing finish date. Paused, Dropped, and Planning do not acquire a finish date. Explicit clearing and same-status edits still preserve the existing safeguard against recreating dates.
- Per the owner's clarification, these defaults and daily activity aggregation use the UTC calendar date. No per-user timezone preference or timezone-data dependency is required.
- Migration `mt012` adds the default-sort preference. Focused preference/date tests, tracking-rule tests, the production build, and authenticated local page/API verification passed.

### Scroll-direction app bar

- The shared tracker app bar now stays at the top of the viewport, slides away after a meaningful downward scroll, and returns after an upward scroll from anywhere on the page.
- It remains visible near the top of a page and whenever one of its controls owns keyboard focus. Scroll work is coalesced through `requestAnimationFrame`; reduced-motion users get the same behavior without animation.
- Verification: the Astro production build passed with the existing upstream warnings.

### Avatar-only profile action

- The app bar profile action is now the user's circular avatar on both desktop and phone. Accounts without an uploaded avatar receive an intentional initial fallback.
- The action retains a descriptive accessible name, active-state treatment, and visible keyboard focus. Avatar data comes from the existing private profile response.
- Verification: the production build and authenticated local page/API verifier passed.

### Typo-tolerant catalog matching

- Movie and series catalog queries now combine exact substring matching with PostgreSQL `pg_trgm` similarity, ranking closer title matches first. Small errors such as `Fxtur Flm` still find `Fixture Film`.
- Migration `mt013` installs the trusted trigram extension and a GIN title index. Remote TMDB retrieval remains a supplemental source and retains its existing failure fallback.
- This engine is shared by Browse and the upcoming fast-search modal; personal-list filtering remains an immediate client-side filter over already loaded entries.
- Verification: the focused typo-search regression passed against migrated PostgreSQL. Implementation follows the PostgreSQL 16 `pg_trgm` similarity/index guidance retrieved through Context7.

### Categorized fast search

- The app bar now opens a reusable fast-search dialog (also available with Ctrl/Cmd+K). It starts searching movies and series after two characters, debounces input, cancels stale requests, and requires no submit button.
- The searching user's combined-list preference produces one interleaved Movies & Series column; separate-list mode produces distinct desktop columns. Phone uses a prominent Browse category selector and renders one category at a time.
- Results show compact artwork, title, year, and media type; local and remote copies with the same type/title/year are deduplicated. Existing catalogue entries open their detail page and remote candidates continue through the existing discovery/import flow.
- Native dialog semantics preserve Escape and backdrop dismissal, focus placement, accessible labels, loading/error/empty feedback, and reduced-motion behavior without introducing a React/Vue runtime.
- Verification: authenticated desktop browser checks covered button and Ctrl+K opening, live typo search (`severence` → `Severance`), deduplication, and closing. A 390×844 browser check confirmed the single-column category selector. The production build passed.

### Detail score and section cleanup

- Removed the Overview/Social/Seasons pseudo-tab strip. The short detail page now presents its real sections directly, avoiding a permanently highlighted Overview state and duplicate navigation with little value.
- Personal scores are explicitly formatted as `9 / 10` while provider percentages retain their own units. In-place quick rating preserves this format after saving.
- Mobile grid placement was tightened after removing the tab row so facts, external links, ratings, overview, following, and seasons remain in intentional order.
- Verification: authenticated browser inspection confirmed the missing pseudo-tabs and the `9 / 10` personal score on a tracked series; the production build passed.

### Canonical season/episode list progress

- Series rows with a complete TMDB or TVDB-native episode catalogue now show the latest completed regular-episode coordinate, such as `S1E4`, instead of only a cumulative whole-show count. The value is derived from canonical completed watch events and remains consistent on personal and public lists.
- An owned Watching row shows a compact plus action when another released episode is available. It appears on pointer hover/focus, remains visible on touch layouts, has an explicit accessible name, and advances through the existing tracking endpoint so a real watch event remains the source of truth.
- Rollback detection now uses watched events after the requested position rather than a possibly stale aggregate progress value. This allows a legitimate next-episode increment after catalogue repair while still requiring confirmation before actual later history is cleared.
- The accepted UTC decision applies to daily activity grouping as well as automatic status dates.
- Verification: all 54 tracking API tests passed against disposable PostgreSQL, including both TMDB and TVDB-native catalogues, `S1E1`, later-season catalogue growth, a stale aggregate followed by a valid increment to `S2E2`, and TVDB completion at `S1E2`. The production build passed. Authenticated browser QA confirmed `S1E1`, the accessible increment control, successful advancement to `S1E2`, touch visibility, and no horizontal overflow at 390×844; temporary QA data was removed.

### Compact list density and status headings

- Status section headings now show only Watching, Completed, Paused, Dropped, or Plan to Watch. Counts remain in the filter rail as requested and still update with the underlying list.
- Compact rows use square cropped artwork and a shorter row height. The existing pointer/keyboard portrait preview remains full-size, while cover-grid, detail, and season artwork retain their portrait geometry.
- Verification: the production build passed. Authenticated browser checks measured 42×42 artwork in 62px desktop rows and 38×38 artwork in 60px phone rows, with a 144×210 portrait retained in the 390px cover grid. Status headings contained no counts, filter counts remained, and 390×844 had no horizontal overflow.

### List filter control maturity

- Filter selects now have persistent visible labels instead of relying only on selected option text. Status choices expose their pressed state, the desktop rail stays available while scrolling, and Reset is disabled until the viewer changes something.
- The phone disclosure has a clear heading, close action, initial focus, active-control count, contained scrolling, and a short reduced-motion-aware entrance. It keeps automatic filtering and does not add an Apply step.
- Reset now restores the viewing user's saved default sort rather than always forcing Title, while clearing temporary query, status, genre, year, and favorite choices.
- Verification: the production build passed. Authenticated 390×844 browser QA confirmed all labels, disclosure state/focus, retained sidebar counts, two active controls producing a `2` badge and three matching results, reset restoring the saved Title default and seven results, and no horizontal overflow.

### Profile Overview and feed ownership

- Profile Overview no longer repeats Social connections or an Edit profile action. It now shows compact Movie, Series, current Completed, and average-score highlights, followed by that profile owner's recent activity and favorites.
- Home now contains followed public profiles only. The viewer's own updates live on their profile, and following someone remains one-way; being followed does not add a person to the feed.
- Activity cards give the action/status more visual weight, reduce cover dominance, link through the title itself, and omit the redundant View title text action. Rating chips remain on the same card when present.
- This completes feed ownership and the initial Overview composition. The later `mt014` activity-model slice now merges progress and ratings into one per-person/title/UTC-day card, raises interleaved cards by their latest update, reports episode progress, and reserves Finished for fully released and fully watched seasons.
- Verification: all 55 tracking API tests passed against disposable PostgreSQL, including explicit followed-public-only Home results and owner activity on Profile. The production build passed. Authenticated browser QA confirmed four Overview highlights, owner activity, favorites, no Social/Edit profile blocks, an empty Home feed despite owner activity when no profiles are followed, and no horizontal overflow at 390×844.

## Accepted direction

- Preserve AniList's recognizable structure and density. Permit improved mobile hierarchy, controls, typography, feedback, and justified visual refinements; avoid a wholesale visual reinvention. This refines ADR-017 for stage two.
- Personal tracking is first priority, closely followed by comparing with friends; discovery is third.
- Manual tracking and connected tracking are equally important. Stremio/Nuvio users receive progress through sync and can rate watched titles. Users of unsupported services search the catalog, add titles, and maintain their own entries. The app must be fully usable without a connection.
- Desktop and phone are equally important. Desktop includes 16:9 1440p/4K displays at realistic browser/OS scaling. Phone quality means usable interactions and task hierarchy, not merely fitting a 390px viewport. Actual phone platforms/browser coverage remains to be specified.
- Complete existing movie/TV workflows and fix defects before richer statistics, expanded detail content, games, books, or other feature expansion.
- Final release acceptance is the owner's explicit approval. After every agreed gate, relevant test, and verification passes, present the build for review; record and resolve requested improvements before asking again. A separate friend trial is not a mandatory gate unless later requested.

## Accepted UI work

- Topbar stays attached to the viewport, animates out when scrolling down, and returns when scrolling up from any point, like the requested AniList behavior. Exact threshold/focus/menu rules are implementation design to specify.
- Add a fast search action in the topbar. Search updates while typing and requires no confirmation button.
- Make search tolerant of small spelling errors; distinguish catalog-wide candidate retrieval from filtering a loaded personal list when choosing the implementation.
- Profile action includes the avatar, either beside the name or by itself; responsive treatment remains to choose.
- Correct the title-page Your score formatting. Rework or remove the Overview/Social pseudo-tabs: on the current short page they add little value and Overview stays highlighted incorrectly.
- Use intentional shared text sizes, weights, spacing, colors, and gradients throughout the app, checked against AniList references.
- Evaluate reusable components for dropdowns, dialogs, filters/search, and related interactions. Investigate Tailwind-compatible options instead of reimplementing each behavior on every page. No framework migration or paid library is accepted merely by this requirement.
- Add restrained, smooth opening/closing and transition animation, informed by AniList, with accessible reduced-motion behavior.
- Add a default-sort preference in Profile settings. Scope across personal/visited lists and temporary overrides remains to choose.
- Redesign the immature filter area; exact filter set and desktop/phone behavior remain to choose.
- Remove numbers next to Watching/Completed/etc. Scope across sidebar labels and group headings needs clarification; avoid silently retaining one when the user intends both.
- Use square cropped thumbnails in compact lists to save row space. Hover shows the full poster; keep equivalent access and discoverable editing for keyboard/touch. Full poster/grid/detail artwork should not be distorted into squares.
- Investigate easy access to textless series posters. Preference/fallback and application beyond series remain to choose based on provider support.

## GitHub and name work

- Establish the project on GitHub for progress tracking after deciding the repository identity/visibility and completing publication preparation.
- Check upstream Scrob licensing and whether a GitHub fork is required or an independently named repository is appropriate.
- Check source-availability and attribution obligations, including browser-delivered code; retain upstream history and notices unless there is a specific reviewed reason not to.
- Suggest short, memorable names suitable for movies, TV, games, books, and potentially anime. Media Tracker remains the temporary name until a new one is selected.
- Repository name, visibility, and final publication contents are unresolved. No repository was created or pushed during the interview.

## Next decision frontier

Independent decisions now ready: search scope and result actions; list preferences/filter behavior; daily manual progress affordance; friend comparison depth; detail section structure; device/browser support; textless-artwork preference/fallback; repository visibility/identity and naming direction. Home composition depends on the selected manual/friend workflows. Component-library selection depends on the required controls and compatibility research.

Keep proposals distinct from the accepted requirements above. Record each accepted follow-up as it arrives.

## Round two — accepted answers and scope additions

This section supersedes unresolved or conflicting statements above, based on the user's next answers.

- Fast search searches movies and series together. Results use separate category columns like the supplied AniList image; the searching user's combine preference merges Movies/Series into one column. Future books and games get their own categories when implemented, not empty placeholders now. Responsive presentation remains to settle.
- Manual users search and add a title first. TV tracking needs distinct season and episode progress. Watching rows gain a plus action next to progress, revealed on hover on desktop; accessible keyboard/touch behavior is required. Exact meaning of season progress and increment remains unresolved.
- Public profiles share the owner's presentation: the profile owner's combined/separate preference determines how everyone sees that profile. The viewer's saved sort preference applies to every list they view. These settings have different owners and must not be conflated.
- No dedicated friend comparison page. Friends means the existing one-way following of public profiles. Title detail followed-user ratings already satisfy that particular gate; preserve them.
- **Scope addition: a Stats page on every profile is now a release gate.** It follows profile privacy. Exact metrics and calculation semantics need agreement; the earlier blanket deferral of richer statistics does not exclude this requested page.
- Remove status counts only beside group headings in list content. Keep counts in the list-filter sidebar. The user's text resolves this even though the two supplied images show half-star and search references rather than counts.
- Remove the short detail page's redundant Overview/Social pseudo-tabs. Fix Your score formatting.
- Prefer suitable textless posters everywhere, with normal-poster fallback. Square crops only in compact rows; full portrait for hover/keyboard preview and grid/detail. Topbar profile action is avatar-only on both desktop and phone.
- Include iPhone Safari in supported real-device verification alongside Android and desktop.
- Public standalone GitHub repository after publication checks, preserving Scrob credit/history, is accepted. The user selected **AnyList** and requested updating branding everywhere, including the logo. Research subsequently found the exact name already used by an established app at https://www.anylist.com/; disclose this concrete collision before finalizing public branding. No silent substitute name and no publication yet.
- Profile Overview: remove redundant Social section and Edit profile button; show visually pleasing small statistics and the profile owner's recent activity, using AniList as inspiration.
- Home: activity centers on followed people, with own activity on Profile. User said “followers” in this paragraph after explicitly defining friends as following; clarify feed membership before implementation.
- Activity focuses on state changes and season completions, with episode finishes aggregated by show/user/day. Example: eight episodes on one day and four on the next produce two activities. The additional “most recent activity” condition needs clarification for interleaved shows. Future chapters/volumes should follow analogous grouping when supported.
- Activity cards emphasize what happened/state; title remains a link; remove redundant View title button. Rating-only activity is not explicitly retained by the new restricted event list and needs confirmation.
- Quick rating must fix its close control and malformed half-star display. User evidence preserved at references/stage-two-half-star-feedback.png.
- Attention prompts: present actionable unresolved notifications on page entry using proper toast-style surfaces, including Rate for newly completed unrated titles, dismissal, and potentially swipe dismissal on mobile. Prevent clutter with a defined backlog policy. Persistence, timing, grouping, dismissal-versus-resolution, and repeat behavior remain to settle.
- AniList multi-category quick-search reference preserved at references/stage-two-search-reference.png. Treat visible unrelated categories/shortcut text as reference content, not requirements to add characters/staff or override browser shortcuts.

Next frontier: season/episode semantics; activity membership/grouping/timezone/event types; stats metrics and meaning; toast priority/backlog/dismissal; responsive search/result actions; name collision and logo direction. Architectural implementation and visual mockups follow the resolved behavior.

## Round three — accepted answers

These answers supersede earlier proposals where different.

- TV progress represents completed seasons plus progress within the current season. The compact position is `S1E4`, not a cumulative whole-show episode number. Underlying episode history remains canonical.
- Home includes only people the viewer follows. A follower is not automatically included. Own activity belongs on the profile.
- Aggregate progress into one card per person/show/UTC-day even when other shows interleave. Updated cards rise to the top.
- Rating posts are activity. A rating with no other activity for that title/day gets its own card; when a related card already exists that day, add the rating to it. Reverse-order merging (rating first, progress later), historical/unknown dates and exact ordering timestamps remain to settle.
- “Finished season” requires a fully released AND fully watched season. For an airing season, describe the episode(s) watched; do not substitute “Caught up.” This wording does not by itself change the existing whole-show Completed status behavior.
- Stats: movie/show totals by status, watched episodes/seasons, estimated watch time, score distribution/average, genres, and activity over time. Movies/Series/All and all-time/year controls. Overview shows compact highlights. Label runtime-derived watch time as estimated; exclude unknown-dated history from date charts. Use a chart library. Exact metric/year semantics remain to specify.
- Attention prompts: one at a time, conflicts ahead of rating requests. Backlogs get a summary such as “4 titles ready to rate · 2 changes need review,” opening the inbox or rating queue. Closing/swiping hides the prompt without resolving the underlying item. Repeat timing remains unresolved; the earlier Remind me tomorrow suggestion was not explicitly accepted.
- Phone search uses a prominent Browse category dropdown like AniList; show the selected category rather than all categories at once. Respect combined movie/series preference. Browse with no search defaults to Trending. Mixed trending order/time window is an implementation choice to document.
- The user explicitly retains **AnyList** after disclosure of the existing app-name collision. Naming is settled; do not repeatedly request reconsideration. Use original branding/logo, retain Scrob attribution and technical compatibility identifiers where necessary, and do not imply affiliation with the other AnyList app.

## Explicit post-approval stop: optional season features

**These are NOT part of the core build or its acceptance gates.** After the user initially approves the completed core build, stop and ask again whether to implement the following feature(s), restating the details. Initial approval of the core build does not authorize either level. Do not implement either as incidental refactoring or silently include it in this release.

1. **Level one — season completion rating prompts:** shows remain combined in search and lists. A season completion detected through Stremio/Nuvio creates a prompt to rate that season. The show receives an average of rated seasons, while an explicit manual whole-show override takes priority. Before implementing, revisit existing per-show rating-mode consent, manual-score precedence, clearing an override, incomplete/airing seasons, initial-import suppression, and integration with the notification queue. This is a proposed change to the current explicit choice on first season rating, not a reason to alter that policy during core work.
2. **Level two — separate season list presentation:** add a setting that displays seasons independently in lists. The owner's/viewer's control, statuses, ratings, numbering, sorting, statistics, activity, links and sync behavior need a dedicated design round before implementation. No assumption that separate rows require separate catalog identities or duplicated progress.

The final core handoff must visibly list this deferred feature and ask whether the user wants to proceed. Do not let normal completion/archive steps erase this checkpoint.

## Round four — accepted answers

- Suppress hidden attention prompts for the rest of the browsing session, across navigation. Unresolved items can prompt on the next visit; genuinely new urgent conflicts may appear sooner. The inbox remains available throughout. Hiding a prompt never resolves the item.
- Merge rating and other activity for the same person/title/day in either order. Any update card can display the rating. Rating changes update the related card rather than requiring a duplicate progress card.
- User requests that adding a show, season, or movie without a distinct end date defaults its end date to the same day. Activity cards display only the time/date the activity was published/displayed, not start/finish dates. **Clarification required:** whether finish-date default applies only when recording watched/completed media (consistent with existing Planning/Watching rules), and whether “without” excludes explicit clearing and initial imports. Do not assign finish dates to unfinished entries or rewrite unknown imported history on an assumption.
- Current status totals remain explicitly current. Year filters use known viewing dates for viewing activity and estimated watch time. Count unique titles/episodes/seasons separately from documented repeat viewing. A manual rewatch count alone does not create dated watch events or estimated watch time.

## Final unresolved product boundary

Finish-date default: use today's UTC date only for explicit manual watched/completed recording without a supplied date. Keep unfinished entries without automatic finish dates, retain explicit clear-date behavior, and preserve unknown initial-import dates. A cumulative season/episode update distinguishes an explicit present-day completion from historical backfill.

Activity display timestamps represent publication/update time, not the inferred start/finish date and not the time a viewer happened to load the page. Updated UTC-day cards rise to the top as agreed, while viewing dates remain separate for statistics.

## Round five — date defaults resolved

- Adding/marking media Completed defaults its finish date to today's UTC date when no finish date is supplied.
- Adding/marking media Watching defaults its start date to today when no start date is supplied.
- Paused and Dropped do not receive an automatic finish date. Planning receives neither automatic date.
- These are defaults for explicit recording/transitions, not a instruction to overwrite supplied dates, reset dates on unrelated edits, invent initial-import history, or prevent explicit date clearing. Existing explicit-date and initial-import safeguards remain.
- Activity cards display publication/update timestamps, not personal start/end dates.

## Consolidated implementation contract — authorized

The rounds above form the accepted requirements. The following sequence makes the implementation reviewable; it does not add new product features.

1. **Repository and baseline:** prepare the public standalone AnyList repository, preserve Scrob history/GPL/credit, review publication contents and historical secrets, carry canonical docs into version control without competing copies, and organize the agreed work into issues/milestones. Resolve destination from authenticated GitHub context; ask only if ownership is ambiguous. Publish no deployment credentials or private reference data.
2. **Correctness and shared UI foundation:** repair rating-mode/sort/editor races, privacy and legacy-route inconsistencies; define consistent typography, density, spacing, colors/gradients and motion; evaluate reusable controls; create original AnyList logo and update user-facing identity while retaining technical compatibility names and attribution.
3. **Navigation/search/lists:** scroll-direction topbar, avatar-only profile action, live fuzzy categorized search, desktop columns and phone category dropdown, Trending Browse, owner-controlled list presentation, viewer default sorting, mature filters, square compact thumbnails/full previews, textless preference with fallback, group-heading count removal only, S/E progress and accessible Watching increment.
4. **Details and editing:** remove redundant detail pseudo-tabs, correct personal score and quick-rate rendering/close control, unify save/loading/error/focus behavior, preserve manual-versus-season-average rules, apply accepted date defaults, retain followed-user scores, and finish missing favorite actions.
5. **Profiles/activity/statistics:** Overview highlights and own activity; following-only Home; per-person/title/UTC-day aggregation, ratings merged in either order, latest update first, finished-season rules and episode descriptions; profile Stats with chart library and the agreed metric/date semantics. Enforce profile privacy throughout.
6. **Attention workflow:** one actionable prompt at a time, conflict priority, backlog summary and rating queue/inbox access, swipe/close hides without resolving, session suppression and next-visit recurrence. Preserve persistent notifications independently of presentation.
7. **Verification and owner review:** relevant automated tests/builds plus desktop/phone interaction, accessibility, large-list, empty/error/loading, privacy, dates, progress, rating, activity, statistics and prompt lifecycle checks. Cover 16:9 desktop at realistic 1440p/4K scaling, Android and iPhone Safari; clearly report any real-device check requiring the user's device. Present the verified build for the owner's review, address feedback, and obtain explicit core approval.
8. **Mandatory deferred-feature checkpoint:** only after core approval, ask whether to proceed with season completion rating prompts (level one) and optional separate season rows (level two), restating their details. Do not implement either without that fresh answer.

Engineering defaults may be chosen without another product interview: keyboard-visible/touch-accessible equivalents, reduced-motion behavior, no header hiding while its controls own focus, stale-request protection, loading/error recovery, responsive bounded widths, and lightweight chart loading. Select exact animation timing, component/chart libraries and implementation internals based on compatibility and verification rather than asking the user to design code.

Implementation authorization was received after the consolidated contract. Continue in reviewable, committed slices; update this document and STATUS.md as each slice completes.

## AnyList identity and original mark

- Release-facing identity now uses **AnyList** throughout the application, authentication and recovery screens, document/PWA metadata, email subjects and bodies, TOTP issuer, health and import/export messages, startup tooling, local instructions, and launcher names.
- A new original rounded blue-gradient `A` and check mark replaces the temporary `Mt` artwork in the shared app bars, authentication hero, About page, favicon, Apple touch icon, PWA icons, and compatibility `scrob.png` asset. The unused legacy ICO was removed so browsers cannot fall back to the old mark.
- The About page and README retain explicit Scrob upstream credit and GPLv3 licensing. Protocol, migration, export-compatibility, cache/event, Unix-user, and existing deployment identifiers keep `scrob` or `media-tracker` where changing them could break stored state or integrations.
- Verification: all three focused branding regressions passed, the production Astro build passed, and the restarted local preview passed authenticated core-route/API checks for both seeded accounts. Browser QA confirmed the AnyList title and mark on Home and About at 1440×900 and Home at 390×844; the phone layout measured 390px content width with no horizontal overflow.

### Detail score and quick rating polish

- Title details have no Overview/Social pseudo-tab strip; Overview and followed-user scores remain ordinary sections in the short page flow.
- The personal score consistently keeps one decimal on the half-point scale (`9.5 / 10`) and the quick-rate trigger announces the title and current score to assistive technology.
- Quick rating now renders stars with aligned SVG layers, producing a clean half-star at every 0.5 step. Its close action is a purpose-styled circular icon control with visible hover and keyboard focus feedback.
- Verification: the production Astro build passed. Authenticated desktop and 390×844 browser QA confirmed all 20 score targets, clean 7.5 and 9.5 half-star states, the corrected close control, the formatted title-page score, and absence of the pseudo-tab strip.

### Session-aware attention prompt

- Every authenticated tracker page can present one floating actionable prompt from the persistent Notifications inbox. High-priority unresolved conflicts are selected before rating requests; multiple items collapse into a count summary that opens Notifications.
- A single rating request links directly to the title's quick-rate flow. Dismissal, including a horizontal phone swipe, hides the visible item(s) for the rest of the browsing session without changing their server state. Navigation does not repeat dismissed items, while a genuinely new urgent conflict can still appear in the same session.
- The app-bar Notifications action remains permanently accessible. Prompt failure is silent and never blocks the page.
- Verification: the production Astro build passed. Browser interception verified mixed backlog copy/actions, dismissal persistence across navigation, and a newly arrived high-priority conflict appearing after the prior backlog was hidden.

### Profile statistics

- Every profile now has a Stats tab inside the shared profile shell. The profile owner's privacy rules protect the endpoint and page, and visitors see the owner's public statistics with the same presentation as the owner.
- Movies / Series / All and All time / individual-year controls cover current status totals, unique viewed titles, episodes and seasons, documented repeat views, estimated watch time, score average/distribution, genres, and viewing activity over time.
- Current list totals, score and genre summaries remain clearly current when a viewing year is selected. Year filtering applies to documented viewing; date charts and estimated watch time exclude unknown-dated history. Manual rewatch counters never synthesize views or runtime.
- Chart.js is loaded only by the Stats route and renders score, activity and genre charts with responsive containers and reduced-motion support. Text summaries and status bars retain the information without relying solely on canvas output.
- Verification: all 56 tracking API tests passed against disposable PostgreSQL, including calculation semantics and private-profile denial. The production Astro build passed. Authenticated browser QA confirmed three charts, live media filtering, public-profile access, stable current totals across a year change, changed dated watch time, and no horizontal overflow at 390×844.

### Daily activity aggregation

- Migration `mt014` adds structured activity details. Manual edits, Stremio/Nuvio observations, cloud-history reconciliation, accepted conflicts and rating decisions all write through one per-person/title/UTC-day activity record. Interleaved updates move that card to the newest position instead of creating duplicates, and API reads also consolidate older duplicate rows already stored before this migration.
- Series cards accumulate the number of newly watched episodes and show the latest cumulative coordinate, such as `S1E4`. A season is labeled `Finished Season N` only when its complete known episode count has been released and watched; an airing season continues to report watched episodes. Progress and rating updates merge in either order, and the card retains its rating pill.
- Home remains limited to followed public profiles while Profile contains the owner's activity. Cards show their activity publication date and time in UTC rather than tracking start or finish dates.
- Verification: migration `mt014` passed upgrade, downgrade and re-upgrade on disposable PostgreSQL. All 57 tracking API tests passed, including interleaved title updates, reverse-order rating merging, fully released versus airing season semantics, followed-public-only Home results and legacy duplicate consolidation. The production Astro build passed. Authenticated browser QA confirmed one card for two legacy same-day rows, `Watched 2 episodes · S1E4`, UTC date/time presentation, four cards after consolidation, and no page overflow at 390×844 or 1440×900; temporary visual-QA data was restored.

### Mobile primary navigation and Browse

- At phone widths, the app bar now presents the current primary section as one prominent disclosure label instead of showing Home, list and Browse simultaneously. The keyboard-operable menu contains the same destinations, reflects the current page, closes when focus or pointer interaction leaves it, and retains the existing scroll-direction header behavior. Desktop keeps the centered primary links.
- Browse now opens on TMDB Trending movies by default and switches to Trending series immediately when its media selector changes. An unavailable or unauthorized trending feed falls back to the local catalogue with an explicit status instead of failing the page.
- Browse title search is debounced and runs as the user types, cancels stale requests, updates the URL without navigation, keeps the existing fuzzy catalogue matching, and removes duplicate local/remote title-year results. There is no visible confirmation button.
- Verification: the production Astro build passed. Authenticated browser QA confirmed 20 live Trending movie and series results, `severence` resolving once to `Severance`, keyboard disclosure opening and first-link focus, and correct mobile/desktop navigation switching. Anonymous QA confirmed the local fallback without an error. The page had no horizontal overflow at 390×844 or 1440×900.

### Favorite tracking actions

- Title details now expose a direct, accessible Favorite/Favorited action beside the status control. The pressed state is announced through `aria-pressed`, persists through the tracking API, and reports a save error in place.
- The full tracking editor includes the same Favorite field and initializes it from the saved entry, so status, dates, score, notes, and favorite state can be edited together.
- Verification: the production Astro build passed. Authenticated browser QA changed an existing title from Favorite to Favorited through the detail action, confirmed the editor checkbox reflected the saved state, restored the original state through the editor save path, and found no horizontal overflow at 390×844. The phone detail controls remain fully visible and touch-sized.

### Textless poster preference

- Full TMDB movie and series lookups now include image metadata and explicitly request language-neutral candidates. AnyList prefers the highest-rated poster whose `iso_639_1` is null, using vote count and resolution as tie-breakers, then falls back to TMDB's ordinary localized poster.
- The choice happens in the shared TMDB detail wrapper, so catalogue imports, metadata refreshes, show creation and tracker enrichment receive the same poster. Live search/trending cards retain their normal poster until a title receives a full lookup; they never incur a per-card image request.
- A bounded `scripts/backfill_textless_posters.py` maintenance command upgrades already tracked movie/series rows without adding request fan-out to profiles and lists. It supports one-media and limited-batch runs.
- Verification: 68 focused TMDB/enrichment tests and the complete 1,137-test backend suite passed; the production Astro build passed. A real TMDB backfill changed Project Hail Mary from its localized poster to a language-neutral candidate, and authenticated browser QA confirmed the 500px image loaded successfully on the title page.

### Standalone public repository

- AnyList is published as the standalone public repository [antipixelhd/AnyList](https://github.com/antipixelhd/AnyList), with the complete Scrob history, GPLv3 license, upstream attribution, modification notice, and canonical product/verification documents retained in-tree.
- The publication audit scanned 2,754 historical text blobs across 604 commits. No private keys or account/provider-token formats were found; the four JWT-shaped matches are the upstream ARVIO public anonymous application key and historical rotations. Only `.env.example` appears in sensitive-path history, while runtime credentials and local state remain ignored.
- Inherited release and container workflows are manual and target only this repository's GHCR namespace. Read-only PostgreSQL backend and Astro frontend CI runs on `main` and pull requests and supports manual dispatch. The local-reference `origin` remains untouched; the public repository is a separate `github` remote.

## Owner acceptance findings — mandatory before Phase Two completion (2026-09-20)

Status: accepted core requirements. These findings reopen the Phase Two implementation gate and supersede any earlier statement that the deployed build is ready for final approval. They are not part of the optional post-approval season work. This checkpoint records requirements only; no application implementation was performed while adding it.

### Quick editor and list density

- Condense the list quick editor so its normal desktop workflow fits within one viewport without scrolling. Remove the separate **Show score** setting from this editor while retaining the existing effective-rating behavior.
- With no rated seasons, the score behaves as an ordinary whole-show score. With rated seasons in calculated mode, the current season average appears automatically in the score control. Editing that calculated value must invoke the existing explicit manual-override confirmation; it may not silently replace the season average or expose a second mode control merely to make the layout fit.
- Remove the overall entry total rendered above the first status group, to the right of the filter/list-title area. Status counts in the filter controls remain unless a later requirement explicitly removes them.
- Reduce compact-row height further to match the live AniList list density while keeping required pointer, keyboard, and touch targets usable. Apply the resulting spacing, type scale, weight, and vertical rhythm consistently to filters and related tracker pages instead of tuning one list in isolation.

Implementation progress: the desktop quick editor is condensed to one 1440×900 viewport and the visible **Show score** selector is removed. Its Score field displays the calculated rated-season average when that mode is active, preserves calculated mode when untouched, and asks before changing to a separate manual whole-show score. Canceling the override restores the calculated value. The overall result total above the first list group is now visually removed while its filtered-count announcement remains available to assistive technology. The broader AniList density pass remains open.

### Home, app bar, and settings-shell corrections

- Fix the **Find people** and **Find your next title** hover/focus states so their text remains legible.
- Home's **Your list** links follow the profile owner's combined-list preference: combined mode shows one Movie/Series List link; separate mode shows distinct Movie List and Series List links.
- Use a square container for the app-bar profile avatar. Its blue outline appears on hover or keyboard focus rather than as a permanent ring.
- Restyle Fast search as the same unfilled icon action language used by Settings and Notifications. Its icon uses the shared blue accent instead of white.
- On desktop, one ordinary wheel/trackpad scroll action is enough to start hiding the app bar. Preserve the existing upward reveal, top-of-page, keyboard-focus, and reduced-motion rules.
- Correct the app-bar/Fast-search alignment on Settings pages so the control occupies the same area and coordinates as on the tracker pages.

Implementation complete: Home's **Your lists** shortcuts now read the signed-in user's combined-list preference and render either one Movie/Series List link or distinct Movies and Series links. Primary buttons retain deliberate dark-on-light contrast on hover and keyboard focus, fixing both reported Home CTA failures. The app-bar avatar uses a square container with a transparent resting border and blue hover/focus outline; Fast search is an unfilled blue icon action, and one ordinary desktop scroll hides the bar while upward scroll reveals it. Fast search is absent and keyboard-inert on phone widths. Settings and tracker pages use the same shared app bar, with browser measurements confirming identical control coordinates.

### Pending connection delivery state

- Pending connection updates are grouped once per movie/show, not duplicated once per provider. A single card reports every intended connected-service delivery and embeds any provider-specific error within that title's card.
- The card disappears only after the change has been delivered successfully to every applicable connected service. Partial success remains visible with the remaining pending or failed provider outcomes; retries must not recreate duplicate title cards.

### Connected completion, rating prompt, and activity regression

- After an established Stremio/Nuvio connection sync newly adds or marks an unrated title Completed, AnyList must create the persistent `rating_needed` notification, make the attention prompt eligible, and publish the completion in recent activity. The existing initial-import flood suppression remains in force; this requirement covers new changes after an established/approved baseline.
- Add a regression for the reported path: mark **The Death of Robin Hood** watched in Stremio, manually sync AnyList, and verify the movie is Completed in the personal list, appears in recent activity, and has one unresolved rating request. Provider polling and manual sync must share these outcomes.
- A `Rated` activity must include the actual rating. Never render the Rated action without a score, including legacy/inconsistent rows such as the observed local Interstellar activity; repair or safely reinterpret missing-score records rather than presenting a false rating event.

Implementation complete: the established-connection completion path now treats a title first imported after baseline as a new transition even when history import has already created its Completed row. It publishes one completion activity and queues one unresolved `rating_needed` record, while preserving initial-import silence and repeated-snapshot idempotence. A PostgreSQL regression covers the reported Stremio movie flow with **The Death of Robin Hood**. Rating-only activity labels now contain the actual score; legacy rows that claim a rating change without storing a score are safely rendered as their truthful status/update rather than `Rated`.

### Desktop Fast search composition

- Fast search is desktop-only for Phase Two. Do not expose the app-bar Fast-search control or its modal on phone layouts. This supersedes the earlier requirement for a phone category selector inside Fast search; the ordinary Browse page remains the supported phone discovery/search path and still defaults to Trending.
- Opening Fast search animates in the input/search bar by itself. Result surfaces are not rendered before the user provides input and a search response is ready.
- After a response, reveal the available category cards together. Render a card only when that category has results. Movies and Series use separate cards or one combined Movies & Series card according to the searching user's preference; future Games and Books get their own cards when those media types exist.
- Make the dialog/search surface smaller and position the input higher so category cards have deliberate space and separation. Preserve live fuzzy search, stale-request cancellation, loading/error/empty semantics, keyboard focus, Escape/backdrop dismissal, reduced motion, and no submit button.

Implementation complete for available media types: Fast search opens as a compact animated input with no results surface. A settled query fetches Movies and Series together, then reveals only populated cards in the signed-in user's combined or separate presentation. New input hides stale cards immediately; empty/error feedback remains explicit and a screen-reader result count announces success. The phone control and shortcut are disabled; future Games/Books cards remain tied to adding those catalogue types in a later release.

### AniList-guided visual pass

- Before changing layout constants, use `agent-browser` to measure the live references at [AniList list](https://anilist.co/user/antipixel/animelist) and [AniList stats overview](https://anilist.co/user/antipixel/stats/anime/overview). Record viewport, browser scaling, and measured geometry so the comparison is reproducible.
- For the profile/list shell, study only the top desktop 16:9 viewport rather than copying the entire page. Match its relationships for logo placement, app-bar height and typography, the gradient between app bar and profile navigation, avatar position aligned with the filter rail, narrower profile-navigation height, and a filter rail shifted left to give the list more width. Preserve AnyList identity and the already accepted mobile adaptations.
- Rework Stats toward the reference's visual hierarchy. Repeated generic cards should not carry the layout; use icons, whitespace, alignment, type size/weight, restrained separators, and charts to make groups feel intentional while retaining every accepted statistic and accessible text alternative.

Visual-pass progress: a measured first-viewport pass now aligns the avatar with the navigation edge and filter rail, narrows the profile navigation, widens the list column, and condenses desktop rows to 52px while retaining 44px square artwork in 54px phone rows. Stats now presents its six headline metrics as an icon-led ledger with separators and keeps charts in open sections rather than repeated cards. `UI-REFERENCE.md` records the reproducible geometry and the live AniList row/Stats rendering limitation. Final cross-page typography/density review, public deployment, real-device checks and owner acceptance remain open.

### Detail, legacy UI, and authentication surfaces

- Split the title-detail Favorite action from a new Library action. Favorite continues to mean personal preference. Library quickly adds/removes streaming-library membership through the existing Stremio/Nuvio delivery workflow. The UI must identify pending/failed delivery and cannot claim remote success before the applicable connection writes succeed; implementation must make the affected connected libraries clear.
- Inventory and remove obsolete Scrob-era UI pages and navigation that are no longer part of AnyList. Preserve required APIs, migrations, stored-state compatibility, upstream attribution, and deliberate redirects; do not leave two competing user interfaces or break external integration routes merely because their technical identifier still contains `scrob`.
- Rework login and authentication-facing layout to use the AnyList visual system rather than the legacy Scrob page style, while preserving Google sign-in, password fallback where configured, recovery, accessibility, and error behavior.

Login progress: the sign-in route now has a full-width AnyList navy/blue shell, quiet public navigation, restrained hero, matching form surfaces and responsive layout. Its existing password, conditional OIDC, 2FA, recovery and bootstrap-restore paths remain wired; local invalid-password handling and desktop/phone rendering were checked. Public Google and 2FA interaction on the final deployed build still need verification. The obsolete Scrob-page inventory/removal is separate and remains open.

### Required verification for this backlog

- Each behavior above needs focused regression or browser evidence before it can be marked complete. The connected-completion path must be tested against an established disposable provider baseline, and pending delivery must cover multiple providers with success, partial failure, retry, and final removal.
- Browser evidence must include the measured AniList comparison, 1440p/4K-class desktop layouts, keyboard behavior, the desktop-only Fast-search boundary, Settings-shell alignment, dense large lists, Stats, login, title Library delivery state, and relevant loading/error/empty states.
- Phase Two cannot return to owner-approval status until this entire section is implemented, documented in `STATUS.md`, committed, deployed, and reverified. Physical Android Chrome and iPhone Safari checks remain required for the phone surfaces that continue to be supported.
