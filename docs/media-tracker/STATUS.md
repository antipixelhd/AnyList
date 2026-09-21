# AnyList implementation status

Updated 2026-09-20. Release one is complete, validated locally, and deployed to the isolated test instance. Stage Two implementation is active. Production rollout is intentionally separate.

Owner review of deployed build `504c687` produced a new mandatory Phase Two backlog. The current deployment remains a verified baseline but is no longer eligible for final approval. `STAGE-TWO-PLAN.md` now requires a condensed rating-aware quick editor, denser lists and corrected shell styling, grouped multi-provider delivery state, repaired connected-completion notifications/activity, desktop-only AniList-style Fast search, an AniList-measured layout/Stats pass, distinct Favorite and streaming-Library actions, legacy Scrob-UI removal, an AnyList login redesign, and ratings attached to Rated activity. No application code was changed in the documentation checkpoint that recorded these findings.

## Grouped pending connection updates (2026-09-21)

The Notifications queue now returns one unresolved card per title, with a delivery row for each outstanding Stremio/Nuvio/cloud service. Per-service errors and conflicts remain visible inside that title card. Applied/cancelled actions disappear from the projection; a confirmed/corrected deletion review can no longer create a false pending card. Deletion markers fill in any outstanding service not present among the currently selected action rows, so partial success keeps the title visible until its remaining services are resolved.

Two database-independent projection tests pass, covering multiple providers, duplicate actions for one provider, error/conflict detail, marker fallback and resolved-review removal. Python compilation and the Astro production build pass. A PostgreSQL-backed endpoint regression covering three providers, partial success, retry and final removal is added but **not yet run**: Docker Desktop's engine is unavailable on this host, its Windows service is stopped, and this session cannot start that service. The local preview is also down, so rendered-card browser verification remains outstanding. This slice is not yet accepted as a completed Phase Two gate.

## Numeric profile and Stats UI retirement (2026-09-20)

The inherited `/profile/{id}` and `/stats/{id}` pages are now small compatibility routes instead of competing Scrob-style profile and statistics interfaces. Each resolves the accessible public profile through the existing backend permission check, then redirects to `/user/{username}/` or `/user/{username}/stats`. Missing/private IDs keep a neutral error instead of revealing a username. The old `/profile` shortcut and inherited Base-menu profile/statistics links now point directly to the canonical username routes. Anonymous access to the old Stats bookmark is permitted only through the same public-profile gate; backend privacy still decides whether it redirects.

The Astro production build passed. A disposable account's numeric profile and Stats paths redirected to the correct AnyList routes both signed in and anonymously; a nonexistent numeric profile stayed on a neutral unavailable page. `LEGACY-UI-INVENTORY.md` separates these retired presentations from remaining Scrob-era UI families and technical routes that must be preserved. Broad legacy-page retirement and final deployment verification remain open.

## AnyList sign-in surface (2026-09-20)

The login route now uses a full-width AnyList shell: restrained navy gradient, shared blue accent, compact branding/public navigation, and a focused sign-in panel. The legacy poster wall, old zinc card treatment, duplicate auth brand and phone marketing tiles are gone. The layout's new `fullBleed` option affects this route only. Native reading/tab order replaces positive `tabindex` values, and error/success messages have live semantics. Conditional Google/OIDC, password fallback, 2FA, forgot-password, registration and first-run restore logic remain in place; first-run restore surfaces use the same panel styling.

The Astro production build passed. Anonymous local browser QA reviewed 1440×900 and 390×844 screenshots, verified no horizontal overflow at 390×844 or 320×720, and submitted deliberately invalid credentials to confirm an error appears while the form remains usable. The local instance has OIDC disabled and no 2FA fixture, so those interactions require final deployed verification before the authentication gate closes.

## Stats visual hierarchy pass (2026-09-20)

The six Stats highlights now form a flat, icon-led ledger with type size, weight and fine separators carrying the grouping. Current-list status, score distribution, viewing activity and genre charts sit in open sections rather than repeated bordered cards. The underlying values, media/year controls, Chart.js canvases, accessible headings and adjacent chart summaries remain intact; no statistics calculation changed.

The Astro production build passed. Authenticated local browser checks at 1440×900 and 390×844 reviewed the rendered hierarchy; the Movies filter updated the visible totals without error. At 390×844 and 320×720, all six metrics and three chart canvases remained in the page with no horizontal overflow. The live AniList Stats body did not render usable reference geometry in this browser session, so this pass follows the owner's visual direction and the profile-shell relationships rather than claiming pixel parity. Final public-head and physical-device verification remain open.

## Measured profile shell and compact list pass (2026-09-20)

The first-viewport AniList comparison measured its 1440×900 CSS-pixel top shell at 100% zoom and device scale factor 1. The live profile navigation was 46px high and its 160px avatar ended at that navigation boundary. AniList's list and Stats content did not render usable geometry in this browser session, so their oversized artwork/rows were excluded as density evidence; `UI-REFERENCE.md` records the limitation and the measured shell coordinates.

AnyList now uses a coordinated profile gradient, avatar/filter alignment, 46px desktop profile navigation, a wider list column, denser filter controls, 52px desktop rows with 38px square artwork, and 54px phone rows with 44px artwork. The filter-search icon retains its 34px input inset. Browser inspection of the authenticated local preview confirmed no horizontal overflow at 390×844, 2560×1440, or 3840×2160; at 4K the centered shell remains 1340px wide. The phone screenshot and desktop screenshot were visually reviewed. This is local evidence for the profile/list slice only; Stats redesign, final deployment, physical-device checks and owner acceptance remain open.

## Stage Two implementation started

The user authorized the consolidated Stage Two contract in STAGE-TWO-PLAN.md. The first correctness slice fixes quick-rating mode safety, half-star rendering, close-control consistency, live list sort metadata, stale editor responses, series-only fields on movie editors, and calculated season-average previews. Current Headless UI packages support React and Vue rather than Astro directly, so the project retains Astro-native shared components instead of adding a UI runtime solely for primitives.

Final local walkthrough checked compact lists at 2560×1440, 3840×2160 and 390×844, Browse at 390×844 and 320×720, mobile detail/stats, misspelled as-you-type search, and an unauthenticated public profile with stats. No horizontal overflow appeared on those routes. It found and fixed detail-rating accessibility, anonymous rating, fast-search control, phone navigation/scroll, Browse recovery, phone filter focus, touch-target, and quick-rating defects. The Astro build and local authenticated route verifier passed; disposable score changes were restored. Commit `504c687` is public and deployed to the isolated test instance after a private validated database backup, and its exact public CI run passed both jobs. The app and retained database are healthy at `mt014`, the running app reports `504c687`, login/PWA/OIDC-entry HTTPS checks passed, and read-only SQL confirms both disposable Stremio/Nuvio connections still have playback and watched pushing disabled. The VPS has 23 running containers with none unhealthy or restarting. The local disposable account password is not valid on the isolated instance; Google OIDC remains the intended isolated sign-in path for the final authenticated check. These browser checks do not substitute for real Android Chrome or iPhone Safari acceptance.

Fast search now suppresses Chromium's native search-cancel glyph inside the full-screen dialog so it does not sit beside and duplicate the explicit Close search control. Browse retains its useful native field clear action.

Phone primary navigation now sends keyboard-opened focus into its first destination and closes on Escape while restoring focus to the disclosure. This corrects the previous native-details behavior, which left focus on an open trigger and ignored Escape.

The scroll-direction app bar now distinguishes keyboard focus from pointer/touch interaction. Keyboard users keep a focused header control visible, while a previously tapped control no longer pins the header during later touch or wheel scrolling.

Browse live-search recovery no longer leaves stale Trending copy and cards visible after a failed query or exposes the browser's raw network error. The current request owns the result region's busy state; superseded requests cannot clear a newer loading state. Failure now presents a stable Search/Browse unavailable heading, retry guidance, and a consistent user-facing error.

Opening phone Filters & sorting now focuses the title filter instead of the panel's Close action. Escape closes the panel and returns focus to its disclosure, matching the explicit close path.

Phone icon controls now use practical touch targets without enlarging their glyphs: app-bar actions, avatar, list/grid switch, compact-row edit and rating actions, linked titles, next-episode progress, and attention-prompt actions are 40–44 CSS pixels. The compact list retains square artwork and its dense row rhythm.

Quick rating retains its ten-star half-fill display on phones but no longer asks touch users to hit 13.5-pixel half-star regions. A native 0.5-step score selector provides an accurate 44-pixel touch control, while desktop keeps the direct 20-target star interaction. The phone close action is also 44×44.

Authenticated browser verification covered the phone dialog at 390 and 320 CSS pixels with no horizontal overflow, a temporary 8.5 save and removal, and live row-label updates. At desktop width the phone selector is hidden and all 20 direct half-star targets remain available.

Verification for this slice: `npm run build` passed with the existing upstream deprecation, empty-chunk, and bundle-size warnings; `scripts/verify_local_preview.py` passed for both local accounts; authenticated headless browser checks covered the ordinary quick-rating dialog and the calculated-average override/cancel interaction. No score or provider state was changed during the browser check.

The next correctness slice aligns anonymous routing with the current public profile surface (`list`, `social`, future `stats`, and the combined-list API) and makes inherited `friends_only` profiles private across both tracker and legacy profile endpoints, even for mutual follows. The focused regression passed and the frontend build remained green. The full tracking file must run only against a clean disposable database: using the populated preview database makes its scheduled catalogue tests count real preview titles outside their fixture.

Migration `mt012` adds the viewer's default list sort and Profile settings exposes it. All personal/public tracking lists initialize from the viewing user's preference. Manual Watching/Completed transitions now use the UTC calendar date for their accepted missing start/finish defaults; Paused, Dropped, and Planning remain without automatic finish dates, and explicit clearing is preserved. Focused API and rule tests, the frontend build, and the authenticated local page verifier passed after migration.

The shared app bar now uses the requested AniList-style scroll direction behavior: sticky at the viewport top, hidden on sustained downward movement, restored on upward movement, and forced visible while its controls own focus. Reduced-motion removes the transition. The production build passed.

The app bar profile action is avatar-only on desktop and phone, with an initial fallback for accounts without artwork and an accessible profile label. The production build and authenticated local verifier passed.

Migration `mt013` enables PostgreSQL trigram similarity and indexes catalogue titles. Movie/series searches now tolerate small spelling errors while retaining substring matching, relevance ordering, remote TMDB supplementation, and the existing local fallback. The focused fuzzy-search regression passed.

The app bar now includes categorized fast search with debounced as-you-type requests, stale-request cancellation, combined/separate presentation from the searching user's preference, mobile category selection, local/remote deduplication, and existing detail/import links. Authenticated browser checks passed on desktop and a 390×844 viewport, including the deliberate `severence` typo; the production build passed.

Title details no longer show the redundant pseudo-tab strip. Personal rating values now include `/ 10` and keep that format after quick-rate updates; the compact phone content order was adjusted for the removed row. Authenticated detail-page inspection and the production build passed.

Series list progress now renders TMDB and TVDB-native canonical completed episode events as a season/episode coordinate such as `S1E4`. Owned Watching rows expose an accessible next-episode plus action on hover/focus and keep it visible on touch layouts. The increment uses the existing tracking endpoint and canonical history; rollback confirmation now depends on later watched events rather than a stale cached aggregate. All 54 tracking API tests passed against disposable PostgreSQL and the production build passed. Authenticated desktop/390×844 browser QA advanced a temporary fixture from `S1E1` to `S1E2` with no phone overflow, then removed the fixture changes. UTC is the accepted boundary for automatic dates and daily activity grouping.

Compact list status headings no longer repeat counts; the filter rail retains them. Compact artwork is square and rows are shorter, while portrait hover previews, cover grids, and detail artwork retain their intended ratios. The production build passed. Authenticated browser measurements confirmed 42×42 desktop and 38×38 phone crops, a 144×210 phone cover-grid poster, retained filter counts, and no horizontal overflow at 390×844.

List filters now have visible field labels, semantic status pressed states, a sticky desktop rail, and a phone disclosure with heading, close control, focus placement, active-control count, contained scrolling, and reduced-motion handling. Reset is inactive until needed and returns sorting to the viewing user's saved default instead of hard-coding Title. The production build passed; authenticated 390×844 QA confirmed two active controls, three matching results, successful reset to seven results, retained sidebar counts, and no horizontal overflow.

Profile Overview now uses compact movie, series, current-completed, and average-score highlights plus that profile owner's recent activity and favorites. The redundant Social block and Edit profile link are gone. Home activity now includes followed public profiles only; a user's own updates live on their profile, and followers do not enter the feed. Activity presentation emphasizes the state, reduces cover width, keeps title navigation, and removes the redundant View title action. All 55 tracking API tests and the production build passed. Authenticated desktop/390×844 checks confirmed the ownership split, Overview content, and no horizontal overflow.

Migration `mt014` completes the daily activity model. Manual changes, Stremio/Nuvio playback, cloud-history imports and accepted review decisions now merge into one record per person, title and UTC day. Interleaved progress and rating updates merge in either order and rise to the newest feed position. Series cards accumulate watched episodes, retain the latest `SxEy` position, and use `Finished Season N` only when every episode in a fully released season is watched. Read-time consolidation prevents older duplicate records from producing duplicate cards. Activity surfaces show the activity publication date and time in UTC rather than tracking dates.

Migration upgrade/downgrade/re-upgrade passed. All 57 tracking API tests and the production Astro build passed. Authenticated browser QA confirmed duplicate consolidation, `Watched 2 episodes · S1E4`, UTC timestamps, four resulting cards, and no horizontal overflow at 390×844 or 1440×900. The temporary local QA payload was restored.

Phone navigation now uses one prominent current-section disclosure for Home, lists and Browse; desktop retains the centered primary links. The menu works by keyboard, closes when interaction leaves it, and preserves the scroll-aware header. Browse defaults to live Trending movies, switches immediately to Trending series, and falls back cleanly to the local catalogue when trending is unavailable. Its fuzzy search runs while typing with stale-request cancellation, URL state and local/remote deduplication.

The production Astro build passed. Authenticated browser QA confirmed 20 Trending results for each media type, the fuzzy typo `severence` producing one `Severance` result, keyboard focus entering the opened menu, and no overflow at 390×844 or 1440×900. Anonymous Browse showed the explicit local-catalogue fallback without an error.

## Working environment

Working copy: `../..`. Both external reference repositories are preserved. Do not push to the local-reference origin. Local PostgreSQL uses `compose.local.yaml`, project `media-tracker-local`, loopback port 55438. Backend port 7341; frontend port 7340. Read-only personal-provider testing remains separate. Disposable Stremio and Nuvio accounts completed controlled outbound verification on the isolated VPS project. Commit `4a7f69a` is deployed there with migrations through `mt010`; the Windows preview recovery fix is commit `94e2517`.

## Implemented locally

- Independent tracked entries, manual/season-average ratings, favorites, private notes, dates, rewatch count, activity, deletion markers, review records, and connection baselines; migrations mt001, mt002, and mt003.
- AniList-inspired shared profile movie/series lists, compact/grid views, filters, quick editor, title pages, following, Home, Notifications, and owner-only Library.
- Zero as unrated, half-point inputs, explicit rating-mode choice, manual score restoration, private-profile exclusions, anonymous-access gate, and invite-only OIDC default.
- Confirmed local deletion clears personal tracking but retains streaming library membership and shared catalog. Remote deletion remains pending rather than falsely acknowledged.
- Additive existing-history import honors deletion markers and ignores library-only membership.
- Season watched/unwatched API and controls: cumulative released regular episodes, confirmation before clearing later watched seasons, no future episodes, Completed preserved on history correction.
- Snapshot reconciliation helper rejects incomplete baselines, records initial review, and supports ordinary-removal auto-confirm after initial approval. Successful full Nuvio/Stremio pulls now invoke it when watch/playback import is enabled. Verified incremental Stremio rows now merge into a saved complete baseline; missing requested records and malformed responses fail closed. Full-push HTTP/background entry points require an approved baseline and no unresolved source conflicts. Inherited streaming history and fanout entry points also enforce this gate.

## Verification

- Original backend baseline: 1,034 tests passed before feature changes (previous work session).
- Prior feature checks: 20 focused tests passed against local PostgreSQL (previous work session).
- Latest frontend production build passed. Existing upstream chunk-size/empty-chunk and Node deprecation warnings remain.
- Latest focused run: 29 tests passed against PostgreSQL, including metadata refresh and partial-response rejection. Full suite after initial provider integration: 1,061 tests passed; subsequent changes validated with focused tests.
- Browser screenshots in verification/ cover desktop/mobile lists, quick editor, and season controls; missing-key recovery was exercised in the current session; authenticated score edit was exercised in the previous work session.
- Season, metadata, and snapshot regression tests are in `backend/tests/test_tracking_api.py`. Docker startup initially failed; restarting after waking its WSL backend recovered the database.

| Requirement | Exact regression evidence |
| --- | --- |
| Cumulative seasons, exclude specials/future, confirm rollback, retain Completed | `TrackingApiTests.test_season_progress_is_cumulative_and_rollback_preserves_completed` |
| First empty/incomplete account cannot remove tracking | `TrackingApiTests.test_snapshot_first_empty_and_partial_pull_cannot_remove_tracking` |
| Complete episode refresh excludes future watch history | `TrackingApiTests.test_metadata_refresh_enables_released_progress_without_future_history` |
| Partial episode metadata cannot enable progress | `TrackingApiTests.test_incomplete_metadata_does_not_enable_progress` |
| Ordinary removal auto-confirms only with approved baseline | `TrackingApiTests.test_verified_removal_auto_confirms_only_after_initial_approval` |

## Remaining release work

1. Complete episode refresh exists for discovered series, the title-page button, and the scheduled metadata sweep for both TMDB and TVDB-native catalogues, with atomic failure handling and protection against provider renumbering. Never renumber existing history.
2. Full and incremental streaming snapshots, cumulative TMDB/TVDB-native series history, conflict-aware status/date updates, and unchanged-observation idempotence are implemented. Controlled provider verification remains part of the outbound test gate.
3. Full streaming pushes and cross-account streaming-library mirroring are gated. Cloud tracker first-import approval, rating and field-level status/history conflict handling, precise score projection echoes, and independent streaming-action retries are implemented. Finish isolated outbound verification.
4. Profile customization, unified settings integration, and the inherited-route privacy audit are complete.
5. Free-key IMDb and Rotten Tomatoes enrichment is implemented and live-verified through MDBList; unavailable scores remain unavailable.
6. Local regression/browser checks and the isolated VPS test deployment are current. Google OAuth with a real client and controlled outbound provider behavior remain unverified.

## Local commands

From working-copy root: `docker compose -p media-tracker-local -f compose.local.yaml up -d --wait`.

From backend with `TRACKING_TEST_DATABASE_URL` set to the disposable PostgreSQL URL: `../.venv/Scripts/python -m unittest tests.test_tracking_api tests.test_tracking_rules -q`.

From frontend: `npm run build`.

## Live metadata checks (2026-09-18)

User-supplied credentials are stored in local preview-account settings only; values are not recorded here. TMDB search and TVDB authentication/series search succeeded. Live Severance catalogue (local ID 195) loaded 19 released episodes; Fight Club (local ID 234) loaded movie metadata. Browser season-1 watched action created Watching with granular history for the synthetic preview user. No streaming account writes were performed; see the read-only verification below.

Additional regression evidence: `test_stream_push_requires_review_and_blocks_unresolved_conflicts`, `test_completion_threshold_does_not_infer_removal`, and `test_first_import_preserves_conflicting_local_status` in `TrackingApiTests`. The episode-refresh test also covers a new release increasing the unwatched count while retaining Completed.

## Provider work and verification (latest)

- Real Stremio authentication and datastore reads succeeded. Two full read-only imports each produced 36 tracked entries and one initial review, with zero datastore writes. The account is stored under private local `provider-test`, with all push flags and schedules disabled and baseline approval false. No credentials or viewing titles are recorded here.
- mt003 adds durable per-connection playback actions. Verified unfinished removal queues dismissal for other streaming connections that have playback pushing enabled. Approved targets dispatch after pulls; failed actions retry later, changed remote playback becomes a conflict, and corrected/deleted local entries cancel obsolete removals. First-import approval still gates writes.
- Stremio dismissal clears only playback offset, retaining library flags, watched bits, and other metadata. Nuvio dismissal uses the official `sync_delete_watch_progress` RPC and exact returned progress key. Rotated Nuvio tokens commit independently before the next network request, including on later failure.
- Queue state/attempts/errors are visible privately in Notifications. Account deletion clears queued payloads. Malformed provider collections never become empty snapshots; Nuvio responses at the 200-progress limit are not accepted as complete removal evidence.
- Local migration to mt003 passed. Full suite: 1,067 tests passed; an additional focused Nuvio refresh-token regression passed afterwards. Frontend build passed. No live Nuvio or remote write testing has been performed.

| Requirement | Regression evidence |
| --- | --- |
| Dismissal preserves library and watched history | `StreamActionAdapterTests.test_dismiss_preserves_library_history_and_unknown_fields` |
| New remote playback wins; repeated dismissal writes nothing | `StreamActionAdapterTests.test_newer_playback_is_not_overwritten_and_already_dismissed_is_idempotent` |
| Rotating Nuvio token survives failed deletion | `StreamActionAdapterTests.test_nuvio_rotated_token_commits_before_progress_delete_and_survives_failure` |
| Queued writes require approval, retry and acknowledge once | `TrackingApiTests.test_queued_dismissal_waits_for_approval_retries_and_acknowledges` |
| Incremental updates preserve untouched titles | `TrackingApiTests.test_incremental_snapshot_preserves_untouched_titles` |
| Missing/malformed Stremio records cannot imply empty state | `StremioClientTests.test_invalid_or_missing_datastore_records_are_not_empty_snapshots` |

At the `mt003` checkpoint, playback restoration, acknowledged deletion, Nuvio library mirroring, cumulative observations, cloud-account reconciliation, rating echoes, and independent retries were still open. The later milestones in this document implement each path and add field-level review. The remaining provider requirement is controlled live verification with disposable accounts; do not enable real-account outbound sync before that gate passes.

Provider references inspected: [Stremio playback state](https://stremio.github.io/stremio-core/src/stremio_core/types/library/library_item.rs.html), [Stremio rewind action](https://github.com/Stremio/stremio-core/blob/development/src/models/ctx/update_library.rs), [Nuvio watch-progress sync](https://github.com/NuvioMedia/NuvioTV/blob/main/app/src/main/java/com/nuvio/tv/core/sync/WatchProgressSyncService.kt). Official Nuvio source is cloned into ignored `.venv/provider-reference-nuvio` for inspection only.

## Local user-testing milestone (2026-09-18, latest)

The app is running at http://localhost:7340. `media-tracker/LOCAL-TESTING.md` is the user guide. Start/Stop Media Tracker.cmd wrap a loopback-only launcher with database migrations, service readiness checks, hidden processes, and process identity checks. Local passwords for private provider-test and synthetic preview are stored in ignored `.venv/LOCAL-LOGIN.txt`; no provider credentials are printed or checked in. Deliberately changed passwords are preserved.

Third real Stremio full import succeeded: 36 tracked entries, one initial review, zero outbound calls, baseline still unapproved. Actual password login and authenticated Home, both lists, Browse, Library and Notifications pages passed for both accounts. Desktop quick editor and phone list were checked in the browser; screenshots are `verification/local-test-editor.png` and `verification/local-test-mobile-list.png`. Stop/restart was exercised and the latest application left running.

Implemented since the prior provider handoff:

- Correcting back to Watching queues saved playback restoration; acknowledgment updates the baseline so the next pull recognizes the write. Different remote episodes/positions are not overwritten.
- Confirmed tracked deletion queues minimal streaming reset payloads, retries failures, preserves library membership and clears pending acknowledgments only after successful writes. Newer remote activity produces a deletion-specific review with explicit retry or keep-remote-history actions.
- Stremio reset clears watched/playback fields, retaining the item and library flags. Nuvio reset uses separate progress and watched-item deletion RPCs; restore uses its progress RPC. Truncated progress responses block destructive reset.
- Changed series playback advances preceding released regular episodes using the show's canonical TMDB or TVDB-native positions. Completion sets finish date; Completed remains Completed. New local corrections take precedence.
- Tombstones filter viewing data out of saved snapshots; additive imports remove reappearing stale watch/progress/rating rows rather than recreating tracked entries.

| Requirement | Exact evidence |
| --- | --- |
| Restore without clobbering a different episode or library flags | `StreamActionAdapterTests.test_restore_preserves_library_and_rejects_a_different_episode` |
| Stremio reset idempotence and newer-activity protection | `StreamActionAdapterTests.test_reset_clears_history_preserves_library_and_rejects_newer_activity` |
| Nuvio restore/reset RPCs and truncated-response guard | `StreamActionAdapterTests.test_nuvio_restore_and_reset_use_separate_progress_history_rpcs` |
| Correction dispatches restoration once | `TrackingApiTests.test_correcting_to_watching_restores_saved_progress_once` |
| Deletion conflict review, retry and acknowledgment | `TrackingApiTests.test_deletion_reset_retries_conflict_and_acknowledges_without_library_removal` |
| Cumulative streaming progress, no specials/future episodes, Completed priority | `TrackingApiTests.test_new_series_observation_advances_cumulatively_and_completed_stays_completed` |
| Local login and page access | `scripts/verify_local_preview.py` passed after restart |
| Build | `npm run build` passed; existing chunk-size/deprecation warnings remain |
| Final backend regression run | `python -m unittest discover -s tests -q`: 1,074 tests passed, with transactional PostgreSQL tracking fixtures enabled |

Provider integration and isolated disposable-account outbound verification are complete for release-one Stremio and Nuvio behavior. Canonical TVDB catalogue and observation support, live Nuvio import, and IMDb/RT enrichment are verified in later sections. Google OIDC behavior is implemented, locally tested, deployed behind the dedicated HTTPS test host, and interactively verified against the existing `provider-test` account. Keep personal-account outbound flags and schedules disabled and first reconciliation unapproved.

## Deferred user feedback

See [FOLLOW-UP.md](FOLLOW-UP.md) for the next-session checklist from local user verification: Git checkpoints, concrete AniList analysis, UI library selection, visual activity/review cards, list hover behavior, series metadata, unified settings, and detail-page quick actions. Work is deferred at the user's request.

## Release goal resumed

Created local checkpoints 0a342ab (backend tracking/sync), 4554025 (interface), d9263f2 (local tooling). Working tree clean immediately after commits; no push performed. A credential-pattern scan of pending files returned no matches; runtime credentials remain ignored. Started live public AniList inspection, recorded observations and limitations in ANILIST-ANALYSIS.md and screenshots in references/. FOLLOW-UP.md is now active, with Git checkpoints checked off. The full first-release goal remains incomplete; continue the analysis and implementation checklist.

## List hover controls completed

Added enlarged cover preview to the shared compact movie/series list, ellipsis overlay within each thumbnail, keyboard focus/Escape behavior, touch-visible controls and reduced-motion handling. Preview is constrained to the viewport, has pointer-events:none, hides on scrolling/filtering/view changes, and is omitted on phones and cover grids. Production build passed; local login/page verifier passed for both accounts. Browser verified hover visibility, click-through styling, thumbnail editor opening, Escape dismissal and no mobile horizontal overflow. Desktop evidence: verification/cover-hover-desktop.png. The real provider account currently has 31 movie and one series entries; do not restore previously reported counts over the user’s edits. No provider writes performed. Remaining FOLLOW-UP.md items and the full provider/release backlog stay open.

## Detail status actions

Commit cded12e adds a reusable split status control with Completed, Watching, Planning and Open List Editor actions; outside-click/Escape dismissal, keyboard access, touch-sized buttons and reduced-motion handling. Series completion offers a checked-by-default released-episode option; unchecking performs status-only completion. Browser tested on synthetic preview Severance: status Completed, progress remained 9, season 1 watched 9, later seasons watched 0. Screenshot verification/status-control-mobile.png; production build and git diff check passed. Detail information expansion remains open. ADR-017 records the user clarification: AniList is the design foundation, not a required 1:1 copy. Full release/provider backlog remains active.

## Activity and review interface

Redesigned Home and Notifications using reusable cover/connection artwork, card headers, rating chips, status transitions and contextual buttons. Home has a prominent private review-count card and list shortcuts; failed review fetch no longer hides activity or falsely reports zero pending. Review cards retain existing endpoints and action semantics. Pending outbound updates are in a collapsible section; mobile prioritizes reviews over preferences. Production build, diff check and local login/page verifier passed. Browser checked desktop activity and mobile real-account reviews read-only, with no horizontal overflow. Screenshots: verification/home-cards-desktop.png and review-cards-mobile.png (the latter precedes collapsing the outbound section). Current local account contains pending deletion reviews from user testing; these were not changed or exported. Full release/provider backlog remains open.

## Series catalogue metadata repair

Provider sync now enriches newly created whole-series `Media` rows and repairs existing rows from their canonical `Show` metadata. Posters, backdrops, synopsis, genres, seasons and external IDs are copied while tracker-only catalogue markers remain intact. The imported provider-test series renders a valid 500×750 TMDB poster and full synopsis in both list/detail flows; evidence is `verification/series-metadata-detail.png`. `tests.test_enrichment` passed 33 tests, the local account/page verifier passed, and the full backend suite passed 1,076 tests. No provider writes were performed.

## Unified settings and expanded title details

Account, Profile, Connections and Administration now use one Media Tracker settings shell with the tracker navigation, design tokens, responsive settings tabs and shared navigation. Release-one profile privacy presents only Private/Public; an inherited friends-only value remains private until explicitly changed. Browser verified Account → Profile navigation and controls, plus Profile/Connections at 390 px without overflow (`verification/settings-mobile.png`). ADR-018 keeps the UI Astro-native rather than adding Element Plus's Vue runtime.

Movie/series details now follow the AniList baseline more closely: banner/cover composition, split status action, compact facts, public community/following/TMDB/personal score blocks, synopsis and genres, external links, and image-led seasons with progress and ratings. Public community averages exclude private profiles. Metadata enrichment retains provider facts such as networks, studios, creators, runtime and original language when available. Desktop and phone evidence: `verification/detail-expanded-desktop.png`, `verification/detail-expanded-mobile.png`; production build passed and the full backend suite passed 1,078 tests. No provider writes were performed.

## AniList fidelity analysis completed

Live public inspection now covers the antipixel profile, immediate list filtering, compact list/cover-hover behavior, desktop title composition, and responsive title order. Five current captures are stored in `references/anilist-*-2026.png`; authenticated editor/status behavior remains sourced from the user's screenshots. `ANILIST-ANALYSIS.md` records the element-by-element adaptation and accessibility improvements. The title page now adds the compact Overview/Seasons/Social tab row and follows AniList's cover/action → title → tabs → facts → content order on phones. The production frontend build passed after this refinement; a new post-refinement browser capture remains for the final release audit because the browser approval quota was exhausted.

## Streaming library reconciliation and mirroring

Complete Nuvio snapshots now remove only source rows proven absent for that exact connection; unresolved mappings, limited pulls, and incomplete responses cannot imply removal. A shared library entry remains while any other connection still owns it. Verified Stremio/Nuvio additions and final-source removals mirror to other streaming connections with collection push enabled only after both source and destination reconciliations are approved. Tracked entries remain independent.

Nuvio records the IDs successfully managed by Media Tracker. Real-time failures therefore leave durable state for a later scheduled/manual full push to retry removals, while remote-only Nuvio library entries remain untouched. Focused provider/tracking tests passed, followed by the full 1,081-test backend suite. No real connection flags were enabled and no provider writes were performed.

| Requirement | Exact regression evidence |
| --- | --- |
| Removing one source cannot erase another source's membership | `TrackingApiTests.test_stream_library_removal_is_scoped_to_the_observed_connection` |
| Source and destination first-import gates both block mirroring | `TrackingApiTests.test_stream_library_fanout_requires_approved_source_and_target` |
| First Nuvio push preserves remote-only rows | `NuvioFullPushTests.test_full_push_merges_instead_of_replacing_remote_library` |
| Failed/delayed Nuvio removal retries only managed IDs | `NuvioFullPushTests.test_full_push_retries_only_previously_managed_removals` |

## Invite-only Google sign-in

OIDC remains generic but now has a secure Google configuration: enabled flows fail closed when required values are missing, optional verified-email enforcement rejects unverified claims, matching is case-insensitive against an existing administrator-provisioned email, and unknown identities are not auto-created. The administration form can create an SSO-only user without inventing a password; supplied passwords continue to work as a fallback. `GOOGLE-SSO.md`, `.env.example`, and `docker-compose.yaml` contain Google endpoint and callback guidance. The isolated deployment requests only `openid email`; its exact callback remains in private operational configuration.

Six OIDC tests and five admin-provisioning tests passed, including verified mixed-case matching, unknown/unverified rejection, incomplete configuration, password hashing, and passwordless provisioning. The production frontend build passed. A real Google client, registered callback, public DNS, and TLS certificate are configured on the isolated instance. Its authorization request reaches Google with the exact callback, and an interactive administrator sign-in returned successfully to the existing Media Tracker account.

## External catalog scores

Migration mt004 adds shared cached IMDb, Rotten Tomatoes critic, and Rotten Tomatoes audience scores. Media Tracker uses MDBList's documented free-key rating endpoint, prefers each user's MDBList key over the administrator fallback, and refreshes a title at most once every 24 hours. Missing values remain unavailable and failed refreshes retain the previous cache. Signed-out browsing only reads cached values and never spends API quota.

Title pages display IMDb on its 0–10 scale and the two Rotten Tomatoes values as distinct percentages. The Administration screen accepts the server fallback key; personal keys remain in Connections. The production frontend build and the full 1,088-test backend suite passed after mt004. The endpoint contract is covered by `MDBListClientTests.test_catalog_rating_uses_documented_tmdb_batch_request`.

Live free-key verification subsequently confirmed MDBList's actual nested rating payload for Fight Club: IMDb 8.8, RT critics 81%, and RT audience 96%. The local fallback key is stored only in the database. The parser regression now matches that verified response; no credential is present in Git or these docs.

## Cloud tracker reconciliation gate

Migration mt005 adds per-user baselines for Trakt, Simkl, and MDBList. The first successful pull creates one private Notifications summary and leaves outbound sync unapproved. Confirming that summary approves the provider. Manual push endpoints, automatic schedules, and cross-provider fanout all enforce the gate; unresolved provider conflicts block outbound work. Later pulls update the baseline without creating duplicate first-import prompts.

Regression evidence: `TrackingApiTests.test_cloud_first_import_requires_explicit_approval` and the existing season-rating fanout suite, which now explicitly models an approved provider. The full backend suite passes 1,089 tests. No real-account outbound operation was enabled or performed.

## Rating projection and echo recognition

Integer-only providers now use decimal half-up conversion, so 7.5 exports as 8 and 8.5 as 9. Trakt and Simkl share this conversion in every single and batch rating writer. Trakt and MDBList imports recognize a returned converted value as an echo of the more precise local value and retain the local rating and rating mode. `RatingProjectionTests` covers midpoint behavior and echo recognition; the full suite passes 1,091 tests.

## Live Nuvio read-only integration

The supplied Nuvio account authenticated against the production API and exposed four profiles. Profile 1 was selected as the default/main profile for the local `provider-test` account. Its live snapshot contained one library row, 74 watched rows, and 18 progress rows. Two consecutive full pulls completed successfully, including refresh-token rotation. The combined local profile now contains 34 movies and two series.

Stremio and Nuvio remain connected simultaneously with collection, watched, and playback pulls enabled. Every outbound flag and both schedules remain disabled. The Nuvio baseline is unapproved and has exactly one pending first-import review after repeated pulls. Source ownership remains distinct: one Nuvio library row and eight Stremio library rows. The backend log contains no Nuvio push/delete RPC from this verification. No provider credential or viewing title is recorded in Git or these docs.

Live validation exposed and fixed a response-model defect that returned Nuvio refresh tokens from the Connections API. Stremio, Nuvio, and ARVIO tokens are now all redacted; a provider regression test and `scripts/verify_local_preview.py` enforce this. The local stop/start launcher now ignores stale process records instead of aborting before stopping a live sibling process. A real stop/restart loaded the fix successfully.

Evidence: two live pull jobs completed with zero errors; the second retained one initial review and an unapproved baseline; `scripts/verify_local_preview.py` passed for both local accounts; 52 focused Stremio/Nuvio tests passed. Outbound behavior has not been exercised against either real account and stays reserved for an isolated test instance.

## Field-level cloud rating reconciliation

Migration mt006 adds rating values and season scope to private sync reviews. Trakt, Simkl, and MDBList pulls now compare imported ratings with the tracked-list score before changing local state. A reliably newer remote rating applies normally; a newer local value remains authoritative. Missing or equal ordering creates a Notifications decision that keeps the local value until the user explicitly chooses the provider score. A provider whole-show score cannot silently replace a calculated season average. Accepting it switches that title to manual show-rating mode while retaining its explicit season ratings.

Integer projections returned by a provider are treated as acknowledgments of the original half-step local rating. The provider-facing legacy row is restored to the precise local value, preventing a later push from degrading 7.5 to 8.0. First cloud imports now populate tracked lists from imported history before presenting their reconciliation summary. The push endpoints, schedules, cross-provider fanout, and the Trakt/Simkl/MDBList background push runners all enforce the same approval barrier; direct task invocation cannot bypass it.

Notifications displays the local and provider scores, optional season scope, **Use provider score**, **Keep local score**, and **Rate / edit** actions. Migration downgrade/upgrade passed. The focused provider/tracking set passed 148 tests, the complete backend suite passed 1,097 tests with 30 expected skips, the production frontend build passed, and the restarted local application passed `scripts/verify_local_preview.py` for both accounts. Real-account outbound flags and schedules remain disabled; no provider write was performed.

Regression evidence: `TrackingApiTests.test_ambiguous_cloud_rating_stays_local_until_resolved`, `TrackingApiTests.test_newer_cloud_rating_applies_but_calculated_show_requires_review`, and `CloudPushRunnerGateTests`.

## Independent action retries and scheduled episode catalogues

Pending Stremio/Nuvio playback dismissals, restorations, and confirmed deletion resets now retry every minute in a dedicated worker rather than waiting for another provider pull. The dispatcher still locks by user, skips locked action rows, requires an approved connection baseline, and enforces the destination push flags. Remote failures retain a sanitized exception class only; one user's failure does not block another user's retry. Regression evidence: `StreamActionRetryTests` plus the existing queue/retry/acknowledgment tracking tests.

The daily show-metadata sweep refreshes complete TMDB and TVDB-native episode catalogues for shared series that at least one user tracks. Active shows become stale after 20 hours; ended/canceled shows refresh after 30 days. If the show-metadata pass detects a revival, its new active status makes its catalogue immediately eligible. Each title refresh remains atomic, so a partial provider response cannot replace the last complete catalogue or alter watch history.

The complete backend suite passed 1,103 tests against the disposable PostgreSQL fixture with no tracking tests skipped. Two consecutive local Stop/Start cycles passed after the launcher was taught to identify a virtualenv child server by its exact workspace/loopback command line when a stored PID has gone stale. The restarted application passed both-account login and page verification and remains available at http://localhost:7340.

## Cloud watch-history reconciliation

Subsequent new watch events imported from Trakt, Simkl, or MDBList now reconcile into existing Media Tracker entries. A reliably newer movie watch marks the movie Completed and supplies a missing finish date. Newer series episodes advance cumulative regular-episode progress, infer preceding episodes, resume Watching from Paused/Dropped when the released catalogue is not caught up, and complete the title when it is. A title already Completed stays Completed while its episode history/progress can still advance, matching the first-release rewatch rule.

Older provider evidence preserves the local status. If the provider omitted the event timestamp or ordering is otherwise indeterminate, Media Tracker preserves the local status and creates a `cloud_conflict` Notifications decision; that unresolved decision blocks provider exports. Initial imports apply their merged state without adding activity-feed noise. The provider pull summaries now report applied tracking updates and tracking conflicts.

Regression evidence: `TrackingApiTests.test_newer_cloud_watch_completion_updates_existing_entry` and `TrackingApiTests.test_unordered_cloud_watch_preserves_local_status_and_creates_conflict`. The full PostgreSQL-backed suite now passes 1,105 tests with no tracking skips. The local stack was restarted, authenticated page checks passed for both accounts, and no real provider write was enabled or performed.

## Isolated VPS test deployment

The isolated test Compose project is running on the VPS. The app image rebuild normalized Windows line endings in `/entrypoint.sh`, resolving the previous container restart-127 failure. The existing database volume was preserved; migrations completed successfully through `mt006`, the app container reports healthy, and the bound `/health` endpoint responds successfully.

The VPS-wide verification showed unrelated services still running. Only the isolated test app service was rebuilt/recreated; its PostgreSQL container remained healthy and was not recreated. Temporary transfer archives were removed from both the VPS and the local workspace. This deployment remains a test instance: real outbound provider writes and Google OAuth client verification are still disabled.

## Pre-deployment product backlog completed locally (2026-09-19)

Commits `5c091de` and `1b02479` complete the newly authorized work that must precede another VPS update. The tracker now defaults to a combined Movie/Series List with a Type column, hides detected anime at display time while retaining storage/sync, provides public profile discovery and one-way following, and uses a functional Social tab. Notifications is a notification-style private inbox with priority, configurable low-priority visibility/retention, seen/dismissed lifecycle, pinned decisions, pending connection actions, and reversible per-provider ignores.

Trakt, Simkl, and MDBList now create deduplicated unmatched-import reviews. Catalogue search can save a durable manual provider mapping; ignore suppresses later prompts for the same external identity. An unresolved unmatched item joins rating/history conflicts in the provider outbound gate. The title rating layout separates personal/following scores from TMDB/IMDb/Rotten Tomatoes and omits the old community score. Account Settings omits inherited player/history, maintenance and legacy data-clearing panels. List focus no longer changes the viewport and the compact list content width is reduced.

Migration `mt008` adds durable positive provider matches. A disposable PostgreSQL migration through `mt008` passed. The focused tracking/provider set passed 150 tests, the production frontend build passed, and local browser checks covered the combined list, title quick-status menu, rating groups, unified settings, phone overflow, the private inbox, and unmatched catalogue search. Temporary QA data was removed afterwards. The local stack remains available at `http://localhost:7340`; real-account outbound flags and schedules remain disabled.

At this checkpoint the isolated VPS still ran `mt006`; it was intentionally left unchanged while the local gate completed. The later “Current isolated VPS deployment” section records the successful rebuild through `mt008`. Real accounts remain excluded from outbound verification.

## Final local checkpoint and newly planned UI gate (2026-09-19)

Commit `09a611e` finishes the pending pre-deployment cleanup: nullable preference patches now fail cleanly, list search focus no longer moves the viewport, title ratings separate personal/following values from external providers, unrelated inherited Account panels remain hidden, and provider-ignore lookup retains MDBList season-rating import compatibility. The `mt008 → mt007 → mt008` migration cycle passed. The complete backend suite passed 1,110 tests with 38 expected skips, the production frontend build passed, and the restarted local preview passed authenticated page checks for both local accounts. A credential-pattern scan of pending source changes found no supplied provider secrets.

The user then added another required local UI/UX pass based on current screenshots. It is documented in `FOLLOW-UP.md`, `PLAN.md`, and `IMPLEMENTATION.md`; the completed result is recorded below. At that point the VPS remained on `mt006`; the later deployment section supersedes that historical state.

## UI foundation and pre-deployment consistency gate completed (2026-09-19)

ADR-018 now retains Astro 6 and Tailwind 4 based on a representative Settings, notice-card, editor, desktop, and phone pass. A single shared Astro `Icon` component renders selectively imported Font Awesome free-solid icons on the server, avoiding a Vue/Element Plus runtime and a browser icon kit. Primary tracker navigation, list controls, detail actions, Settings controls, cards, and the editor now share that icon language. Reusable button, field, Settings surface, `NoticeCard`, severity, dialog, responsive, focus, and reduced-motion rules replace page-specific variants.

Account/Profile/Connections/Notifications now share the same surface and spacing language as lists and details. The combined Movie/Series List preference moved to Profile. Notifications and pending connection updates use the shared card component; direct local `outbound_pending` changes are excluded from the provider-review inbox and remain visible only as operational connection delivery state. `TrackingApiTests.test_local_delivery_state_stays_out_of_provider_review_inbox` protects that behavior.

The list editor now follows the supplied AniList reference with banner/poster composition, a responsive field grid, status, score, episode progress, dates, rewatch count, show-score mode, private notes, save, and delete. It omits custom lists, favorite, and per-entry privacy. Outside-click and Escape use dirty-form protection and restore focus; the completion, season-mode, video-player, and tracker popover surfaces follow the same dismissal rule where applicable.

Browser review covered Account/Profile Settings, the combined list, Notifications, and the editor at desktop size, plus Account Settings and the editor at 390×844 with no horizontal overflow. The production frontend build passed. The `mt008 → mt007 → mt008` migration cycle passed, all 1,111 backend tests passed, and `scripts/verify_local_preview.py` authenticated and checked both local accounts. The isolated VPS and all real-account outbound flags remain unchanged pending user review.

Follow-up commit `9a672b2` fixes a display regression from the shared button rules: the mobile filter trigger can no longer occupy a desktop list-grid column. Desktop lists again use the compact filter rail and full-width grouped tables; the combined-list Type header is hidden with its column on phones. Pending connection updates are collapsed by default again. Notifications uses the full central width, while Inbox preferences, ignored imports, and existing-history import are grouped in a separate collapsed Inbox tools section below the stream. A final type-scale pass reduces oversized headings and heavy text while retaining clear emphasis for titles, selected states, scores, and actions. Desktop and 390×844 browser checks plus the production frontend build passed after the correction.

Commit `96863cb` removes the permanently visible mobile thumbnail action overlay. The full thumbnail remains the edit target; its ellipsis treatment appears only while focused/selected or actively pressed. A 390×844 browser check verified clean idle covers, thumbnail-to-editor opening, and selected-state restoration after closing the editor. The production frontend build passed.

## Shared app bar and Notifications (2026-09-19)

The tracker and settings shells now render one shared app bar with fixed desktop and phone geometry, consistent active states, and the same profile, Notifications, and Settings controls on every page. Notifications moved out of the Settings sidebar into a bell in the top bar. The bell shows subdued counts for routine updates and a distinct rose count for high-priority unresolved conflicts, first reconciliation, or unmatched imports. The existing `/recent-events` URL remains as an internal compatibility route, while the product-facing page, title, Home shortcut, and planning documents call it Notifications.

The Notifications page now uses the main tracker shell and full content width rather than the Settings frame; pending connection updates and Inbox tools retain their collapsed presentation. The settings-only Astro transition wrapper was removed so all pages use the same navigation lifecycle. Production build and authenticated preview checks passed. Browser measurements confirmed identical app-bar coordinates between combined list and Profile Settings at desktop and 390x844; the phone layout has no horizontal overflow. The isolated VPS and provider outbound settings remain unchanged.

## Inherited-route privacy audit (2026-09-19)

Anonymous comment reads now honor the instance-wide logged-out navigation switch. Legacy profile discovery and follow routes expose only public profiles; inherited `friends_only` values continue to behave as private for release one. Public profile previews no longer name private followers or followed accounts, and avatar responses use private, non-cacheable delivery so shared caches cannot outlive a privacy change. Nullable legacy genre fields also serialize safely.

Regression coverage verifies the anonymous comment gate, private social previews, and private-profile search/follow behavior. The complete PostgreSQL-backed backend suite passes 1,116 tests. No VPS or provider connection was changed.

## TVDB-native manual catalogue refresh (2026-09-19)

The title-page episode refresh action now selects the canonical provider. TVDB-native shows load their complete official episode catalogue using the effective user/admin TVDB key, retain TVDB episode identities and positions, and store the same completeness marker used by list progress. Incomplete or duplicate catalogues fail atomically. If a known TVDB episode returns at another position, refresh stops and preserves existing episode rows and watch history.

Regression coverage verifies successful TVDB-native hydration and rejection of renumbering. The complete backend suite passes 1,117 tests. No VPS or provider connection was changed.

## Scheduled TVDB-native catalogue refresh (2026-09-19)

The shared metadata scheduler now discovers tracked series by either TMDB or TVDB identity and refreshes each stale catalogue through its canonical provider. TVDB credentials follow global, administrator, then user precedence and retain the paired subscriber PIN. An installation with only TVDB credentials can refresh TVDB-native catalogues; missing credentials skip only unsupported titles, and one provider failure remains isolated to its title.

Regression coverage verifies that a stale TVDB-native title is scheduled once with the effective TVDB key while the existing TMDB scheduling behavior remains intact. The complete backend suite passes 1,118 tests. No VPS or provider connection was changed.

## Current isolated VPS deployment (2026-09-19)

Committed build `83a4fca` was archived without local credentials and deployed over the existing isolated test source. Only its app container was rebuilt and recreated; the existing PostgreSQL container and both named volumes were preserved. The initial update migrated the retained database from `mt006` through `mt007` and `mt008`; the current build adds TVDB-native streaming observations, the Media Tracker authentication shell, completed release-facing product identity, and Media Tracker export/backup/webhook labels. The app and database report healthy, the database remains at `mt008`, and the served manifest/source marker identify Media Tracker.

At this deployment checkpoint, the VPS Compose inventory confirmed unrelated projects stayed running. Temporary local and VPS transfer archives were removed. The isolated database initially contained zero users and zero media-server connections, so no provider state could be touched accidentally. Later checkpoints below record disposable provider provisioning, controlled outbound verification, and the dedicated Google OIDC test host.

## TVDB-native streaming observations (2026-09-19)

Incremental streaming observations now resolve a series through either TMDB or TVDB identity and advance history against the show's stored canonical episode rows. TVDB-native shows therefore receive the same cumulative released-episode inference, completion, and Completed-priority behavior without translating or renumbering their history. Regression coverage observes TVDB-native S1E3, marks canonical S1E1–E3 watched, and completes the tracked title. The complete backend suite passes 1,119 tests.

## Media Tracker authentication shell (2026-09-19)

The signed-out shell no longer displays upstream Scrob branding or advertises release-excluded reviews, community lists, and unrelated discovery claims. Login and registration now use the same Media Tracker mark, restrained blue type/icon language, and accurate watch-journal, half-point rating, friend-score, and optional reconciliation copy as the main AniList-derived interface. The legacy base document title, install label, and manual-source label also use Media Tracker. Desktop browser inspection confirmed icon rendering and balanced hero/form composition; the production Astro build passed.

## Release-facing identity completion (2026-09-19)

Media Tracker now owns every release-facing identity surface: About, activation and password-reset email, TOTP issuer, application/PWA metadata, offline copy and cache name, health responses, backup/export filenames and import status, webhook API labels/user agent, startup logging, browser/install artwork, and the repository README. The About page uses the shared tracker app bar and explicitly credits the upstream Scrob project and GPLv3. Protocol terms, migration-compatible names, compatible Scrob-import support, the Unix container account, and the Plex client identifier remain unchanged where renaming could affect integrations or stored state.

Three focused branding regressions pass. The complete unittest run executes 1,122 tests successfully with 47 expected skips; the equivalent pytest run reports 1,075 passed, 47 skipped, and 32 parameterized subtests. The production Astro build passes. The restarted local preview passes authenticated route checks for both prepared accounts. Browser inspection confirms the refreshed About page uses the shared app bar and Media Tracker artwork, while live manifest and health responses identify Media Tracker. Later checkpoints below complete controlled disposable-account outbound verification and the interactive Google OIDC callback.





## Field-level cloud history reconciliation (2026-09-19)

Trakt, Simkl, and MDBList watch-history reconciliation now treats status, start date, finish date, and progress as one reviewable change set. A reliably newer provider event applies all supported fields together and creates a low-priority Notification showing the before/after values. An ambiguous import preserves the complete local value set, including on the first connection import, and creates a high-priority conflict. Confirming that conflict applies its reviewed fields atomically; keeping it preserves the local entry; choosing another status applies that status with the accepted history evidence. Completed remains authoritative and is not reopened by repeat viewing.

Regression coverage: `test_newer_cloud_watch_completion_updates_existing_entry`, `test_unordered_cloud_watch_preserves_local_status_and_creates_conflict`, `test_first_cloud_import_preserves_ambiguous_existing_history`, and `test_cloud_history_resolution_applies_reviewed_fields_together`. The focused provider/tracking set passed 163 tests, the full backend suite passed 1,113 tests, and the frontend production build passed. Existing dependency and `datetime.utcnow` warnings remain. Isolated outbound provider verification is still required; no real connection or VPS state was changed.

## Controlled Stremio and Nuvio outbound verification (2026-09-19)

Disposable accounts were provisioned only in the isolated VPS test project. For both Stremio and Nuvio, the first pull created the reconciliation summary, an outbound request before approval returned the expected conflict response, and approval released the test workflow. A temporary title was added through Media Tracker and observed remotely, then removed through Media Tracker and observed remotely. The disposable remote state was restored, ordinary push flags were disabled again, and a second pull completed without pending conflicts. No personal production account or neighboring VPS service was changed. Sanitized verification summaries remain in the isolated application volume; temporary credential/helper files were removed.

## Local status precedence and durable deletion delivery (2026-09-19)

Tracked statuses now carry their own source and change time. Streaming and cloud reconciliation compare provider evidence against that status timestamp, so a later note or rating edit cannot distort precedence. Explicit local changes away from Watching queue playback dismissal across eligible Stremio/Nuvio accounts. A first empty account creates an unapproved baseline and cannot change local state or fan out a removal.

Confirmed local deletion now has executable, durable delivery state for every supported destination instead of merely naming cloud providers as pending. Stremio/Nuvio reset jobs wait for approved reconciliation, ignore ordinary mirror toggles for this explicit destructive decision, clear playback/watch state, and retain library membership. Trakt, Simkl, and MDBList reset jobs also wait for their approved first-import baseline, retry independently, and clear supported watched/rating/watchlist/dropped state without touching collection/library membership. Simkl has no matching watchlist-removal helper in the current integration, so only its supported history and rating state is cleared and no unsupported success is claimed.

Migrations `mt009` and `mt010` add status provenance and the durable cloud action queue; the `mt010 → mt009 → mt010` cycle passed. The focused tracking suite passes 51 tests, the scheduler/provider set passes 120 tests, and the complete PostgreSQL-backed backend suite passes 1,126 tests with 32 parameterized subtests. Existing dependency and `datetime.utcnow` warnings remain.

Commit `a5939f9` is deployed to the isolated test project. Only the app container was rebuilt; its database and application volumes were preserved and migrated from `mt008` through `mt009` to `mt010`. The app and database are healthy, the source marker reports `a5939f9`, and both disposable Stremio and Nuvio connections retain `push_playback=false` and `push_watched=false`. The VPS container inventory confirmed unrelated projects remained running.

The isolated instance is also available through a dedicated HTTPS route and certificate. OIDC is enabled with the exact registered callback, verified-email enforcement, disabled self-registration and account auto-creation, and password login retained. The disposable administrator has a confirmed email match and a password hash, preserving recovery. Both the new and existing application login return HTTP 200, and the real Google authorization page receives the exact callback with `openid email`. Interactive Google sign-in successfully returned to the test instance and selected the existing administrator account, closing the OIDC deployment gate.

The local preview is also migrated to `mt010` and available at `http://localhost:7340`. Both prepared accounts pass authenticated route checks. Commit `94e2517` repairs recovery from a partial Windows launcher start by adopting only exact Media Tracker listeners on ports 7340/7341; this avoids manual cleanup while preserving the port-ownership safeguard.

## Completion progress and notification polish (2026-09-19)

The shared app bar now exposes a POST-backed sign-out action on every authenticated tracker page. Notification dismissal removes its card immediately, restores the all-caught-up state when the last card is gone, and refreshes the quiet/attention badges from the server without a page reload.

Transitioning a series to Completed now marks every currently released regular episode watched at the API boundary, regardless of which UI or client initiated the change. Later catalogue additions remain unwatched without reopening the title. Completed list entries summarize that mismatch as the number of distinct new seasons; the label is absent for Watching and every other status. The complete 1,126-test backend suite and production frontend build pass. Browser inspection confirms the sign-out action, the `1 new season` list treatment, and a notification dismissal changing the app-bar badge from one to none while revealing the all-caught-up state without navigation.

Commit `4a7f69a` is deployed to the isolated public test instance. Only its app container was rebuilt and recreated; the retained PostgreSQL container remains healthy at `mt010`. The public login returns HTTP 200, Google authorization still emits the exact registered callback with `openid email`, and the source marker reports `4a7f69a`. Unrelated VPS projects remained running. The transfer archive was removed after deployment and a pre-update source backup remains on the VPS. `RELEASE-AUDIT.md` maps the accepted first-release scope to its implementation and verification evidence.



## PWA completion notifications and quick rating (2026-09-19)

The AniList-style tracker and settings shells now register the existing Media Tracker service worker, restoring installable PWA behavior on the rebuilt frontend. The manifest, 192/512 icons, Apple touch icon, offline fallback, and service-worker scope were verified locally. The service worker now handles Web Push and notification clicks on Windows and Android-capable browsers.

A subsequent provider sync that newly moves an unrated title to Completed creates one private `rating_needed` Notification. Initial reconciliation and direct local completion do not generate it. A per-device opt-in in Profile Settings requests browser permission and stores the Push API subscription. Delivery uses a persistent instance VAPID key, the media title and available cover art, and opens `/title/{id}?rate=1`; expired subscriptions are removed automatically. Rating the title resolves the prompt. Push delivery remains best-effort because final presentation of large cover images is controlled by the operating system and browser.

Lists, title details, in-app Notifications, and native notification deep links now share one immediate half-point quick-rate dialog. Ten stars expose 0.5–10 through left/right hover, focus, and click targets; the selected score saves immediately, updates visible score controls without navigation, and can be removed. The Notifications card supplies a Rate now action, and a notification click opens the same dialog on the title page.

Migration `mt011` adds user-scoped device subscriptions. The `mt011 → mt010 → mt011` cycle passed. The complete PostgreSQL-backed backend suite passes 1,128 tests, the production Astro build passes, authenticated local preview checks pass for both prepared accounts, and browser inspection confirms an activated controlling service worker, valid standalone manifest and assets, the full half-point control range, and live score updates. Headless Chromium intentionally denied native notification permission, so the public test instance remains the real-device permission and OS-display gate.

Commit `03a8277` is deployed to the isolated public test instance. Only its app container was rebuilt and recreated; its database and application-data volumes were preserved and migrated from `mt010` to `mt011`. The app and database are healthy, the source marker reports `03a8277`, login, manifest, and service-worker routes return successfully, the deployed worker contains the rating push handler, and the protected push route redirects anonymous callers to authentication. Google OIDC still enters through the public callback flow. Unrelated VPS projects remained running. Transfer artifacts were removed; the pre-update source backup remains on the VPS.

## Notification badge delivery-count correction (2026-09-19)

Pending connection updates remain available in the collapsed operational queue on the Notifications page, but no longer contribute to either app-bar notification badge. Both server-rendered counts and live badge refreshes count only actual notification results. The production frontend build passes.

Commit `02ffd65` is deployed to the isolated public test instance. Only the app container was rebuilt; it is healthy, the source marker matches, and the public login returns HTTP 200.

## AnyList release identity (2026-09-20)

The stage-two product identity is now AnyList across every release-facing surface: shared navigation, authentication and recovery, About, settings and connection copy, emails, TOTP, health and integration labels, browser/PWA metadata, offline and push copy, local tooling, documentation, and launcher names. The replacement artwork is an original blue-gradient `A` with a check mark, rendered through one shared component and regenerated for favicon, Apple touch, PWA, and compatibility image assets. The obsolete `favicon.ico` carrying the old `Mt` mark was removed.

Scrob credit and the GPLv3 notice remain prominent in About and README. Stable protocol, migration, export-compatibility, cache/event, Unix-account, and deployment identifiers retain `scrob` or `media-tracker` where renaming would risk compatibility. This slice does not publish or deploy the renamed repository.

All three focused branding tests pass. The production Astro build passes. After restarting the local preview, `verify_local_preview.py` passed authentication, core routes, profile APIs, and connection-secret checks for both seeded accounts. Authenticated browser QA at 1440×900 and 390×844 confirmed the AnyList page title and mark on Home and About; the 390px layout has no horizontal overflow.

## Detail score and quick-rating correction (2026-09-20)

The title page presents Overview and followed-user ratings as ordinary sections without the obsolete pseudo-tab strip. Personal scores now retain one decimal on the 0.5–10 scale and expose the title/current value through the quick-rate button's accessible name.

Quick rating replaces overlapping font glyphs with aligned SVG base/fill layers, so half stars clip cleanly at every step. The close action now has deliberate circular sizing, color, hover, focus, and pressed feedback instead of inheriting an incomplete generic button style.

The production Astro build passes. Authenticated browser QA verified all 20 half-point controls, visually inspected 7.5 and 9.5 fills at desktop and 390×844, confirmed `9.5 / 10` on the title page, exercised the close action, and confirmed the pseudo-tab strip is absent.

## Session-aware attention prompts (2026-09-20)

Authenticated tracker pages now surface one floating prompt for actionable persistent Notifications. High-priority conflicts take precedence over rating requests; a backlog becomes a concise count summary leading to Notifications, while a lone rating request links to the title quick-rate flow. The app-bar inbox remains available throughout.

Closing or horizontally swiping the prompt records only session-local suppression for the displayed IDs. It does not resolve or dismiss server records, does not repeat during navigation, resets on the next browsing visit, and still permits a newly arrived urgent conflict to appear sooner.

The production Astro build passes. Browser interception verified a mixed conflict/rating backlog, the summary action, suppression after navigation, and presentation of a new high-priority conflict in the same session without database mutation.

## Profile statistics (2026-09-20)

The new profile shell now includes a Stats tab for owners and public-profile visitors. Movies, Series and All filters combine with All time or a known viewing year. The page reports current status totals, unique viewed titles, episodes and seasons, documented repeat views, estimated watch time, score average/distribution, genres, and activity over time.

Current status, score and genre figures remain current when a viewing year is selected. Viewing totals are derived from documented completed watch events; date charts and estimated runtime require a known `watched_at`, and the existing manual rewatch count cannot invent views or time. Unknown-dated history can still contribute to unique all-time viewing counts while staying out of dated charts and estimated time.

Chart.js is route-local and provides responsive score, activity and genre charts with reduced-motion support and adjacent text summaries. All 56 PostgreSQL-backed tracking API tests pass, including current-versus-dated calculations and private-profile denial. The production Astro build passes. Browser QA on seeded data confirmed another public profile's Stats page, three chart canvases, media filters, current titles remaining `39` while the 2026 filter changed estimated time from `461h 1m` to `213h 34m`, and a 390px content width with no overflow.

## Daily activity aggregation (2026-09-20)

Migration `mt014` adds structured activity details. Manual changes, streaming observations, cloud reconciliation, accepted conflicts and ratings now merge into one per-person/title/UTC-day card. Interleaved updates raise the existing card, and API reads consolidate legacy duplicates. Series activity reports accumulated episode progress such as `S1E4`; only a fully released and watched season is labeled finished. Ratings merge with progress in either order. Home remains following-only and Profile remains owner activity.

Migration upgrade/downgrade/re-upgrade, all 57 tracking API tests, and the production Astro build pass. Browser QA confirmed legacy consolidation, episode wording, UTC publication timestamps, and no overflow at desktop or 390×844.

## Mobile primary navigation and Browse (2026-09-20)

Phone layouts now present the current main section as one prominent keyboard-operable dropdown, while desktop retains centered links and the scroll-aware app bar. Browse defaults to live Trending movies, switches immediately to Trending series, and falls back to the local catalogue when trending is unavailable. Debounced search runs while typing, aborts stale requests, updates the URL, uses fuzzy matching, and removes duplicate local/remote results.

The production Astro build passes. Authenticated browser QA confirmed live movie/series results, `severence` resolving once to `Severance`, disclosure keyboard behavior, and correct responsive navigation. Anonymous fallback also passed, with no overflow at desktop or 390×844.

## Favorite tracking actions (2026-09-20)

Title details now provide a direct Favorite/Favorited action beside the tracking status. It exposes its state through `aria-pressed`, persists through the existing tracking endpoint, and displays a local save error if the request fails. The full tracking editor carries the same Favorite checkbox and loads it from the saved entry, completing the previously unreachable profile-highlight and favorites-filter workflow.

The production Astro build passes. Authenticated browser QA toggled an existing title through the detail action, confirmed the tracking editor reflected the persisted state, then restored the account's original state through the editor save path. At 390×844 the detail controls remain within the viewport with no horizontal overflow.

## Textless poster preference (2026-09-20)

Full TMDB movie and series lookups now append image metadata and explicitly include language-neutral images. The shared client chooses the highest-rated `iso_639_1 = null` poster, with vote count and resolution tie-breakers, and retains the ordinary localized `poster_path` as the fallback. Because selection lives in the common detail wrapper, new catalogue imports, metadata refreshes, show creation and tracker enrichment share the behavior. Search and Trending avoid expensive per-card image lookups and receive the preferred image once a title is fully imported.

The new bounded `scripts/backfill_textless_posters.py` command upgrades existing tracked rows outside request handling and supports a single media ID or a limited batch. The 68 focused TMDB/enrichment tests and all 1,137 backend tests pass, as does the production Astro build. A live TMDB backfill changed Project Hail Mary to a language-neutral poster, and browser QA confirmed the returned 500px image loaded on the authenticated title page.

## Standalone public repository (2026-09-20)

The audited code and complete Scrob history are now public at [antipixelhd/AnyList](https://github.com/antipixelhd/AnyList). GPLv3, upstream attribution, an explicit modification notice, and the canonical planning/status/reference evidence are part of the repository. The former external planning directory was moved in-tree, so there is one canonical copy.

The audit scanned 2,754 historical text blobs across 604 commits and found no private keys or account/provider-token patterns. Four JWT-shaped findings are ARVIO's deliberately public anonymous client key and prior rotations. Runtime environments, local login data, certificates, logs, dependencies and build output remain ignored. Upstream release/Docker destinations were removed; release and container workflows are manual and target only this repository's GHCR namespace. Read-only PostgreSQL backend and Astro frontend CI is enabled for `main`, pull requests and manual runs. The first push published audited head `b5c40ad` without creating a release or package.

## Public CI bootstrap repair (2026-09-20)

The first public CI runs exposed two missing test-environment prerequisites. `aiosqlite` is now an explicit locked development dependency, and the backend job installs the development group. The backend tests require a migrated disposable PostgreSQL schema, so CI now runs `alembic upgrade head` against its dedicated test database before discovery. The dependency lock check and complete local backend suite pass: 1,137 tests, 57 expected skips. The frontend CI job passed on public commit `08af76d`; backend CI on that commit still failed because it had no migration step. The migration repair is committed locally and needs a new public CI run before claiming a green release gate.

## Final release identity sweep (2026-09-20)

The last outbound webhook user agent now identifies as `AnyList/1.0`. The PWA registration helper and quick-rating browser hook also use the final name. A source search found no remaining `MediaTracker` product strings in active backend/frontend code; the generic “media trackers” section comment is unrelated to the old brand. All 138 webhook tests and the production Astro build pass.

## Green public CI and release tracking (2026-09-20)

The [public CI run for `169ab46`](https://github.com/antipixelhd/AnyList/actions/runs/35518952525) passed both backend and frontend jobs after the locked SQLite test dependency and disposable PostgreSQL migration initialization were added. The [Public-ready core milestone](https://github.com/antipixelhd/AnyList/milestone/1) now contains issues for final CI, isolated deployment, desktop/Android/iPhone verification, and explicit owner acceptance. The release checklist links each gate and its evidence.

The public issue review highlighted that older documentation named the test host and neighboring services. Current Google setup, plan and status text now use generic deployment references while preserving the verification findings. Earlier Git history remains unchanged, so any formerly published operational endpoint remains discoverable in historical commits; none was a credential in the publication audit.

## AnyList isolated test deployment at public head (2026-09-20)

The public `f3d26dc` source archive was staged separately from the prior test source and matched its local SHA-256. Compose configuration validated before the build. Only the isolated app image was built; a mode-600 disposable database dump was created and checked with `pg_restore -l` before the app container was replaced. The database container retained its identity, both app and database report healthy, and the app source marker is `f3d26dc`. Startup migrations reached `mt014`.

Over the existing HTTPS test route, login, the web manifest and the service worker returned 200; the OIDC start route returned 302 and the backend still reports Google enabled with password fallback. The backend health endpoint returned `{"status":"ok","app":"AnyList"}`. A read-only query confirmed the one disposable Nuvio and one disposable Stremio connection each retain `push_playback=false` and `push_watched=false`. No container reported an unhealthy state. The prior source and private database backup remain available for rollback. Authenticated UI acceptance and real Android/iPhone checks remain open.

## Established stream completion notification repair (2026-09-20)

An established Stremio/Nuvio snapshot could import a newly watched title into tracking as already Completed, then fail to recognize a transition because reconciliation compared the imported row with itself. The title reached the personal list, but no recent activity or rating request was created.

Snapshot reconciliation now records a title first seen after the approved baseline as a new transition. An unrated Completed title creates one persistent `rating_needed` record and one completion activity; repeated polling remains idempotent, and first-import flood suppression is unchanged. For a newly tracked series, activity counts the imported cumulative progress instead of reporting a zero-episode change.

A disposable PostgreSQL regression reproduces the reported **The Death of Robin Hood** Stremio manual-sync path and verifies Completed status, one unresolved Stremio rating prompt, one completion activity, and duplicate suppression across a repeated snapshot. The focused four-test safety set and all 58 `TrackingApiTests` pass. Live-provider confirmation remains part of owner verification.

## Truthful rating activity labels (2026-09-20)

Rating-only activity now includes the saved value directly in its action label, such as `Rated 7.5 / 10`, as well as the existing score pill. Activity serialization clears an inconsistent legacy `rating_changed` claim when its row has no score, so rows such as the reported local Interstellar record fall back to their actual status/update instead of displaying a rating that does not exist. Valid scored events and combined progress/rating cards retain their rating data.

The focused three-test activity set and all 59 PostgreSQL-backed `TrackingApiTests` pass. The production Astro build also passes.

## Condensed quick editor and seasonal-score behavior (2026-09-20)

The list quick editor no longer exposes a separate **Show score** setting. For an ordinary title, Score remains the direct whole-title rating. For a series using rated-season averaging, the calculated value appears in the same Score field with a short explanation. An untouched value preserves averaging; changing it opens a focused confirmation before switching to a manual whole-show score, and canceling restores the calculated value without altering season ratings.

The desktop editor hero, poster, field heights, spacing and action area were condensed. At 1440×900, the rendered editor measures 752px high and fits without internal scrolling. Browser QA used a save-blocked in-page fixture with two rated seasons: it displayed 7.5, prompted when changed to 8, and restored 7.5 after **Keep season average**. No seeded data was changed. The production Astro build passes.

## Home shortcuts, CTA contrast, and list total cleanup (2026-09-20)

Home now reads the signed-in user's tracking presentation preference. Combined mode shows one **Movie/Series List** shortcut; separate mode shows **Movies** and **Series**. The shared primary-button hover/focus state keeps dark readable text on the brighter blue background, repairing the disappearing text on **Find people** and **Find your next title**.

The overall result total above the first status group is visually removed from personal and public lists. Its live filtered-result announcement remains as screen-reader-only feedback, and the compact/grid switch stays right-aligned. The production Astro build passes. Authenticated browser QA confirmed the combined shortcut, final hover colors of `rgb(7, 19, 29)` on `rgb(102, 199, 246)`, a 1px assistive-only result-count box, and `flex-end` toolbar alignment.

## App-bar control and scroll corrections (2026-09-20)

The profile action now uses a square avatar container. Its border is transparent at rest, including on profile and Settings routes, and the blue outline is reserved for hover and keyboard focus. Fast search uses the same unfilled icon-control shape as Settings and Notifications with the shared blue accent. At phone widths the control is absent and Ctrl/Cmd+K cannot open its dialog.

Desktop scroll handling now begins hiding the 72px app bar after the first ordinary downward scroll beyond the top threshold, while any meaningful upward scroll reveals it. Top-of-page, keyboard-focus and reduced-motion protections remain. The Settings and tracker shells already share `AppBar`; 1440×900 measurements confirmed identical Fast-search `(1166, 19, 34×34)` and profile `(1216, 18, 36×36)` bounds on both pages.

The production Astro build passes. Browser QA confirmed the transparent/blue Fast-search treatment, 4px avatar radius and transparent resting border, full `-72px` hide after one 120px scroll, upward reveal after 40px, and a 390×844 phone layout with no Fast-search activation or horizontal overflow.

## Desktop Fast-search composition (2026-09-20)

Fast search now opens as a compact, animated field positioned near the top of the desktop viewport. Before input it renders no results or placeholder card. A query hides the previous cards immediately, debounces, searches Movies and Series concurrently, then shows the populated category cards together. Combined-list mode uses one narrower Movies & Series card; separate mode uses individual Movies and Series cards, omitting either one when empty. Empty and failed searches show a small explicit status, and successful searches announce counts to assistive technology. Escape/backdrop dismissal, stale-request cancellation and reduced-motion support remain.

The production Astro build passes. Authenticated browser QA at 1440×900 confirmed a field-only 68px opening, concurrent `severance` results, a no-match query with zero visible cards, and Movies plus Series cards after temporarily disabling combination on the disposable preview account. Its original combined preference was restored afterward. A 390×844 check from the prior app-bar slice confirmed Fast search cannot open on phone.

## Grouped connection-delivery verification (2026-09-21)

Docker Desktop was restored and a fresh disposable PostgreSQL 16 database migrated to `mt014`. The multi-provider endpoint regression passed through initial pending state, one provider success plus another's error, retry, and final removal. The complete `test_tracking_api` plus pure outbound projection set passed (62 tests). A local preview fixture rendered exactly one title card with two provider rows and an inline retry error at 390×844, without horizontal overflow; the fixture was removed afterward. The page no longer says “You're all caught up” while a pending-delivery card remains. The [phone verification image](verification/pending-connections-phone.png) records the expanded card. The production Astro build passed. Final deployed/live-provider checks remain open.

## Separate Favorite and streaming Library actions (2026-09-21)

Title details now keep Favorite as a personal preference and expose a distinct Library add/remove action for connections with collection push enabled. Migration `mt015` adds an owner-level desired state and one delivery record per eligible Stremio/Nuvio connection. The local decision commits before external I/O; reviewed-baseline gating, safe per-provider errors, retry counts and pending state survive refreshes. Successful accounts update independently, failed accounts remain in the title status and grouped Notifications queue, manual retry is available, and future connection pulls retry remaining work.

The implementation preserves remote-only records: Stremio changes merge with the existing item and Nuvio uses its existing merge API. A successful provider write updates only that connection's `CollectionFile`; a small manual anchor preserves an explicit addition. An explicit removal is excluded from subsequent full streaming-library builds, so another collection source cannot silently re-add it. Disabling collection push cancels that delivery without claiming success.

Migration downgrade/re-upgrade passed on disposable PostgreSQL. The 65 tracking/projection tests and 120 sync/Nuvio/Stremio tests pass, including focused no-target, first-reconciliation, partial failure, retry, removal and grouped-queue cases. The production Astro build passes. Browser QA at desktop and 390×844 confirmed distinct Favorite/Library controls, an unreconciled provider's pending explanation and Retry action, and no horizontal overflow. The fixture was removed; the [phone verification image](verification/title-library-pending-phone.png) records the pending state. Real disposable provider delivery remains part of final deployed verification.

## Legacy discovery UI retirement (2026-09-21)

Seven inherited Scrob discovery presentations no longer compete with the AnyList Browse experience. `/movies`, `/shows`, `/search`, `/discover`, both `/trending/*` routes and `/airing-today` now provide thin 302 compatibility redirects. Useful movie/series selection and title queries survive the redirect, while a signed-in `/search?type=user` bookmark opens that user's canonical Social search. Login's public navigation now points directly to Browse. The internal `/discover-title` route remains as Browse's remote-catalogue import bridge rather than a user-facing presentation.

The production Astro build passes. Authenticated browser checks confirmed a movie-search bookmark retained `type=movie&q=severance`, a user-search bookmark opened the signed-in profile's Social route, and the old Trending Shows URL opened Browse with `type=series`.

## Legacy personal-page retirement (2026-09-21)

Eight inherited Scrob personal presentations now resolve into the accepted AnyList workflows. Custom-list bookmarks open the canonical username list, both provider collection pages open the owner-only streaming Library, Progress selects Watching on the Series list, Dropped selects Dropped on the combined list, History opens the owner's Profile Overview activity, and Calendar opens Home. Anonymous routes retain the appropriate login or Browse boundary.

The canonical list accepts a validated `status` query and applies it as the initial visible filter, so compatibility redirects preserve their meaning instead of only changing the URL. Custom-list storage, exports and backend APIs remain unchanged even though custom lists are outside the accepted release interface.

The production Astro build passes. Authenticated browser checks confirmed Progress selected Watching with no horizontal overflow, Dropped selected Dropped, both collection routes opened Library, History opened Profile Overview, and Lists opened the canonical combined list.
