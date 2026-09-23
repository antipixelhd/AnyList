# Media tracker product and implementation plan

Status: release-one implementation completed and validated on 2026-09-19. See RELEASE-AUDIT.md and STATUS.md for scope and evidence.

Stage two's earlier implementation reached final acceptance review on 2026-09-20, and the owner added mandatory local-development gates on 2026-09-21. The added gates were implemented locally on 2026-09-22. The next step is owner manual verification before any VPS work. Read STAGE-TWO-PLAN.md for the accepted requirements and local handoff, STAGE-TWO-REVIEW.md for the original baseline findings, and PUBLIC-READY-GATES.md for the remaining deployed and owner proof. Google-authenticated review and physical Android/iPhone checks remain later acceptance gates; games/books remain deferred.

The optional-season checkpoint remains after core approval.
Updated: 2026-09-22.

This is the durable handoff for this project. Read DECISIONS.md and GLOSSARY.md with this document. Explicit accepted decisions override recommendations and older conversation statements. Unresolved items must not silently become requirements.

Read IMPLEMENTATION.md for stages, acceptance scenarios, verification tasks, and the future backlog. UI-REFERENCE.md and references/ preserve the user's screenshots.

## Product

A privately operated, internet-accessible movie/TV tracking service for an administrator and invited friends. Use Scrob as the foundation, redesigning the entire frontend around AniList's clean appearance and workflows. The existing frontend does not constrain the design; its technology stack may remain.

The first release must provide movies and TV series, polished personal lists, title details, profiles, following, and retained existing sync. Books and games are later priorities. Anime/manga are optional later additions and require corresponding AniList sync; preserve their provider-specific separate-season entries rather than applying TV grouping blindly.

## Accepted requirements

### Access and privacy

- Administrator creates accounts for friends using their email addresses. Google SSO signs into those existing accounts; no open self-registration or automatic account creation.
- An administrator-controlled instance setting allows anonymous browsing of catalog pages and public profiles; default off.
- Profiles are public or private. Private profiles expose no personal list, rating, or activity data to other users and contribute nothing to other users' followed-person averages.
- Following is one-way, like AniList. Mutual following is not required to count in followed-person ratings or feeds.

### Interface

- Follow AniList closely for navigation, covers/banners, title details, and profiles.
- Initially dark theme. Default compact list with grid toggle. Core list, progress, and rating workflows must work on desktop and phones.
- Lists include sorting/filtering, list search, quick editing, progress, and favorites. No custom lists or bulk editing required in release one.
- Profiles include avatar, banner, short bio, follow button, follower/following counts, movie/series lists, favorites, and basic totals.
- Feed contains self and followed users: status changes, completions, and ratings. Combine rapid changes to the same title. Initial imports populate state/history without flooding feeds; subsequent sync changes can create activity. Apply privacy everywhere.
- Comments, likes, messaging, and friend-sharing/recommendation inboxes are out of initial scope.
- List-row hover reveals an ellipsis opening a quick editor; clicking the title opens its detail page. User-provided screenshots are preserved in references/ and interpreted in UI-REFERENCE.md. Screenshot content alone does not authorize extra features.
- Provide a private Notifications area for unconfirmed sync interpretations, allowing confirmation, a different status, and immediate rating. This is distinct from the social activity feed; provider-specific propagation rules are defined under Sync.
- Home shows self/followed activity and a pending Notifications count. Navigation: Home, Movie List, Series List, Browse, Profile; connections/preferences in Settings. List navigation opens the signed-in user's profile list route, using the same list presentation visitors see rather than a separate personal list screen. Owner-only editing and privacy enforcement still apply.
- Render status groups vertically, like AniList, and allow filtering. Include a distinct Plan to Watch group (the existing Planning state, not a sixth status).
- Additional full-page screenshot references/anilist-profile-list-filters.png establishes inspiration for profile header/tab navigation, left-side list search/status/metadata filters and sorting, vertically grouped list tables, and upper-right view switching. Follow UI-REFERENCE.md for adaptation to movies/TV and mobile; do not infer unrelated features from the image.
- Quick editor includes editable optional start/finish dates, private notes, and rewatch count; no per-entry privacy or custom lists. Planning has no default dates. Starting sets start date; completing sets finish date. Manually adding an already-completed title defaults ONLY finish date to today; start remains empty. All dates are editable/clearable. Unknown imported historical dates stay unknown. Newly observed streaming Watching activity supplies start date, and observed completion supplies finish date; sync-latency approximation is acceptable. Distinguish historical first import from newly observed activity.

### Catalog

- Separate media sections; cross-media global search is not required.
- Use a central application catalog with stable mappings to connected providers' identifiers, so search, lists, and sync resolve to the same entries.
- TV lists/search display whole shows, not individual seasons or episodes. Seasons are accessible on detail pages.
- Detached/standalone season entries are a future feature, not part of initial list/search behavior.
- External IMDb and Rotten Tomatoes scores display distinctly. Prefer both RT critic and audience scores when available. They are not ratings from members of this instance.
- Also display the followed-users average and individual followed users' scores. A separate instance-wide user average has not been requested.
- Only free API-key services are eligible. A configured user key takes precedence over the administrator key.

### Status and watch history

- TV/movies: Planning, Watching, Paused, Dropped, Completed.
- Future books: Planning, Reading, Paused, Dropped, Completed.
- Future games: Planning, Playing, Paused, Dropped, Completed.
- Episode/season watch state is independent of the list status. Preserve history when changing status.
- Catching up through the latest released episode marks a series Completed. New releases add an unwatched-episode indicator without changing status.
- New viewing resumes Watching from Paused or Dropped; catching up completes it. Completed takes priority: subsequent automatic playback updates do not move a Completed title back to Watching in release one, including repeat viewing. This supersedes earlier automatic rewatch/resume behavior; deliberate manual status edits remain possible.
- Manually selecting Completed offers 'Mark all released episodes watched', enabled by default but optional. Never mark unreleased episodes watched.
- Support marking an entire season watched, limited to released episodes.
- Use simple cumulative episode progress: detecting a later episode/season as watched marks preceding episodes watched as well. Keep per-episode records underneath. Episode ordering, specials, and corrections need explicit rules; never infer future episodes as watched.
- Lowering progress corrects later episodes to unwatched; offer season-level mark watched and mark unwatched. Marking an earlier watched season unwatched also clears later seasons; prompt first when it is not the latest watched season. Cumulative inference must not immediately undo this correction.
- Main lists focus on started media, with Plan to Watch the explicit unstarted exception. Allow any status with or without a rating; enforce no status-based rating restriction or progress-evidence requirement.
- Streaming library membership is separate from tracked-list membership. Mirror the library across connected Stremio/Nuvio accounts, but library additions/removals must not automatically add/remove main tracked entries. Display it in an owner-only Library tab on the user's profile. An explicit Library-to-Planning action was suggested but is not a required feature.

### Ratings: current model, supersedes inheritance proposal

- Rating input uses 0–10 in 0.5 steps, but 0 means unrated and must never display as a rating or contribute to averages. Meaningful scores are 0.5–10. This supersedes the earlier valid-zero requirement. Internal null for unrated is an implementation proposal. Ratings require no progress evidence; statuses require no rating.
- By default, rating a show sets only its manual show rating. It does not mark seasons rated, visibly or implicitly.
- Seasons can be rated individually from title details. Never offer episode ratings.
- On the first individual season rating, ask per show: 'Average my rated seasons' (default) or 'Keep a separate show rating.' Do not populate other seasons automatically. Preserve a former manual show score so explicitly switching back can restore it.
- Calculated mode averages explicitly rated regular seasons; exclude specials and unrated seasons. New seasons begin unrated and cannot change the average merely by appearing.
- Manual mode preserves a user-entered whole-show rating independently of season scores. Users may override the calculated show score while retaining season scores.
- User input stays on half-point steps. Display calculated show/followed-user averages to one decimal place; retain calculation precision internally.
- Clearing the last rated season in calculated mode makes the show unrated. Do not silently restore the saved manual score.

### Sync

- Retain and verify Scrob's existing connections; each connection is optional. New providers can come later.
- End state is bidirectional sync: changes here propagate to supported connected accounts, and external changes update local state.
- Adapt each field to destination capabilities, including score-scale conversion/rounding. Do not assume all providers support ratings or season ratings.
- Orient implementation around Scrob's existing behavior. Detect changes rather than blindly replacing state from snapshots. Prefer reliable change timestamps; preserve local edits and flag conflicts when ordering is uncertain.
- Export the effective displayed show score, manual or calculated, where supported. Round to the destination's nearest supported increment; exact midpoints round upward. Preserve local precision and recognize round-tripped converted values so they cannot overwrite the original or change rating mode.
- Before a connection's first outbound write, import and show a reconciliation summary. Merge nonconflicting entries/history, preserve local values in ambiguous conflicts, and let the user resolve conflicts before exporting them.
- Confirmed deletion of a main list entry clears all of that user's state for it: history, ratings (including seasons), dates, progress, and other personal entry data. Prompt before deletion and propagate supported removals to connected accounts. This supersedes the earlier recommendation that list deletion preserve history/ratings. Do not delete the shared catalog entry or other users' data.
- Provider removal is not equivalent to confirmed local deletion. This platform retains richer state than streaming libraries. When an unfinished Watching entry is removed from active playback on a streaming account, infer Dropped for a movie or Paused for a show, and expose the interpretation in Notifications for confirmation/correction/rating. Exact reliable signals qualifying as active-playback removal remain open.
- Missing data in an incomplete/failed provider response is not proof of removal. Detailed implementation must inspect Scrob's existing reconciliation before extending it.
- Refinement: library removal alone only affects the separate streaming library, not main tracked-list state. Precisely identifying 'removed from Watching' versus library removal requires resolution; do not conflate them.
- Apply inferred Paused/Dropped locally and mirror the underlying playback-removal action to other connected Stremio/Nuvio accounts without waiting for review. Hold inferred status writes to other tracking platforms until confirmed. Do not pretend Stremio/Nuvio have equivalent Paused/Dropped fields.
- Auto-confirm is per user, off by default, and covers ordinary inferred Paused/Dropped changes. It releases them to other tracking platforms; rating prompts remain. Conflicts, uncertain removals, first-connection reconciliation, and destructive deletion still require review. Publish inferred status activity after confirmation.
- After confirmed local deletion, keep only a minimal internal deletion marker (title identity, deletion time, pending connection acknowledgments), not old personal ratings/notes/history. Use it to prevent stale reimport while propagating supported removals.
- Once deletion resets have succeeded for all connected providers with relevant outbound push enabled, a new streaming watch dated after the deletion may add the title again. Older or undated provider history must not undo the deletion.
- Confirmed tracked-entry deletion never removes streaming-library membership. Propagate supported tracking/progress/history/rating resets without uncollecting the title.
- Completed takes priority over inferred Paused/Dropped. Verified unfinished active-playback removal propagates to connected Stremio/Nuvio accounts. Correcting the event back to Watching propagates restoration on the next sync where supported. Verify actual provider progress-restoration capabilities; library membership is not a substitute.
- Detect removals relative to a successful per-connection baseline. A newly connected empty account cannot clear existing state. Incomplete/failed snapshots cannot prove deletion. Uncertain cases preserve state and go into Notifications for a user decision.

## Source inspection evidence

Reference repositories: `../../../reference Repos/scrob` and `../../../reference Repos/Yamtrack`. Read-only inspection; no application or live integration tests performed.

- Scrob: Astro/Tailwind frontend; FastAPI/SQLAlchemy/Alembic/PostgreSQL backend. `frontend/package.json`, `backend/requirements.txt`, `backend/main.py`.
- Scrob catalog is movie/series/episode/person-centric. `backend/models/base.py`, `backend/models/media.py`.
- Scrob has season ratings and season list entries already: `backend/models/ratings.py`, `backend/routers/ratings.py`, `backend/models/lists.py`. Initial UI must not expose detached season entries just because the backend permits them.
- Scrob following/privacy: `backend/models/follows.py`, `backend/routers/profile.py`. Existing mutual-follow privacy is not the accepted product privacy model.
- Scrob provisioning/OIDC: `backend/routers/admin.py`, `backend/routers/oidc.py`, `backend/core/config.py`. Disable auto-create and registration; verify email matching and Google login. Generic OIDC exists, not bespoke Google authentication.
- Scrob sync clients: `backend/core/stremio.py`, `backend/core/nuvio.py`; orchestration is tightly coupled to backend models in `backend/routers/sync.py`. Scheduled rather than instant updates. No live sync reliability certification.
- Deeper sync audit: Stremio pulls handle library removal per connection; Nuvio pulls are additive and lack corresponding removal reconciliation. Their pulls explicitly do not immediately fan out changes to other connections; scheduled pushes exist. Requested automatic mirroring is additional behavior, not verified existing functionality (`backend/routers/sync.py`, around 4316–4815 and 5040).
- Stremio library membership and playback state are distinct; Nuvio separately retrieves library/watched/progress. Absence from Continue Watching is not intrinsically proof of user removal. Existing local dismissal only clears local progress (`backend/routers/history.py`, around 725).
- Existing Scrob deletion operations separately uncollect/unwatch/dismiss; no unified all-personal-state deletion was found. Minimal tombstones, private per-title review events, and cumulative earlier-episode inference require additions. Plex's three-way reconciliation in `backend/core/watchlist_reconcile.py` is a reusable pattern, not existing Stremio/Nuvio coverage.
- Scrob currently retrieves structured TMDB scores. RPDB support supplies poster images, not separate structured IMDb/RT fields. Existing MDBList integration imports personal ratings; external score enrichment is additional work.
- Yamtrack has broader catalogs, explicit statuses, decimal ratings, and background imports already. Its missing feature is specifically Stremio/Nuvio integration, not all background sync. Its game provider is IGDB; books include Open Library/Hardcover.
- Repository license identifiers: Scrob GPL v3; Yamtrack AGPL v3. Preserve notices; no copying between projects is required by this plan.

## External score feasibility

MDBList is the leading candidate, not yet a verified production choice. Official documentation lists a free 1,000-request/day tier, provider-ID lookup, and quota metadata. Evidence suggests distinct IMDb/RT critic/RT audience values, but no authenticated free-key payload has been tested. Title coverage can vary.

- https://docs.mdblist.com/docs/api
- https://api.mdblist.com/
- https://mdblist.com/movies/?q_tag=44625
- https://github.com/Kometa-Team/Kometa/blob/master/modules/mdblist.py (secondary implementation evidence)
- OMDb free tier: https://www.omdbapi.com/apikey.aspx ; separate RT audience coverage not established.

Implementation proposal: cache source-specific scores with timestamps, refresh within quotas, and represent missing scores as unavailable. Verify provider terms before choosing cache policy. Never silently substitute one source's score for another.

## Workspace and rollout

- Final product name: AnyList. Stored compatibility identifiers retain their existing names.
- Working repository: C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker, a separate clone of the clean Scrob reference at commit 3d75f172fc054ed90c39af9d336d5f5feda40d54. Both reference repositories remain intact.
- Development and validation began locally. The committed release is deployed to an isolated test project with disposable Stremio/Nuvio connections and outbound flags disabled after controlled verification. Operational endpoints and neighboring-service inventory are kept out of this public product plan.
- The source clone retains a local reference origin. Do not push to the reference repository. A hosted project remote is not required to begin local implementation.

## Engineering verification (agent-owned)

Reliable provider removal/restoration signals, complete versus sparse snapshots, initial baselines, safe forwarding between accounts, ordering/specials, date observations versus imports, and zero/unrated conversion require verification against Scrob. These are implementation investigations, not unresolved product questions. Retain existing connection controls unless the accepted product behavior requires changes. See IMPLEMENTATION.md for proposed technical defaults and verification gates.

## Proposed delivery sequence (not implementation authorization)

1. Obtain final confirmation of this consolidated specification. Keep the documents updated as implementation evidence develops.
2. Audit Scrob's current schema, migrations, auth/privacy boundaries, provider mappings, sync reconciliation, and tests against accepted requirements. Validate free-score provider with credentials when available.
3. Present a coherent UI design covering lists, title details, profiles, feed, settings, and mobile layouts. Establish reusable components and interaction states.
4. Implement domain changes and migrations: explicit statuses, independent progress, rating modes, privacy, activity grouping. Preserve existing user data and integration semantics.
5. Implement redesigned core screens and optional connection settings; add external rating enrichment with credential precedence and quota handling.
6. Validate end-to-end flows, privacy for anonymous/other users, migration preservation, season rating transitions, completion with future releases, sync conflicts/round trips, and responsive accessibility.
7. Deploy an isolated VPS test instance after local verification, exercise test accounts, and present results before connecting real accounts for outbound writes. Inspect infrastructure and choose nonconflicting ports/names during implementation.

## Product and UX backlog implemented locally

These items were later authorized for the first-release local gate and have been implemented. They must pass the final regression gate before the isolated VPS test instance is updated.

### Private sync inbox and automatic reconciliation

- Replace the current private sync-review presentation with a slimmer notification-style sync inbox that can accommodate more events. Viewing a dismissible notification marks it seen and removes it from the inbox. Higher-priority items that cannot be resolved automatically remain pinned until the user provides an input.
- Apply imports that have no conflicts directly to the database and show them only as dismissible notifications. A conflict exists only when the same data point disagrees. For example, an existing rated movie receiving an unrated copy from another provider is not a conflict; differing ratings are.
- Attempt automatic conflict repair first. Prefer the newest reliable value for fields such as ratings and start/finish dates, and record the automatic decision as an event so the user can review or disagree with it. Preserve the existing automatic watchlist interpretation that places removed unfinished movies/shows into Dropped/Paused, and show that interpretation in recent events.
- Notifications must contain automatic provider updates applied to the database, not local edits made by the user (for example, the user's own rating changes). Keep pending connection updates as they work today.
- The highest-priority notifications are unresolved imports requiring user input. If a media item cannot be matched, the user must be able to manually match it or ignore that entry from that service. Settings must expose a per-service blacklist so an ignored choice can later be reverted.
- Low-priority notifications must auto-dismiss after a configurable period. Users may disable these low-priority notifications entirely so the inbox contains only changes that cannot be resolved automatically.

### Settings, lists, catalog classification, and details

- Audit the Settings navigation and remove sections that do not affect this platform; adapt retained settings to the platform's actual behavior.
- Keep the default combined Movie/Series List setting enabled. Its single list is named “Movie/Series List” and includes a third Type column with Movie or Series values.
- Detect and classify anime explicitly. Anime remains synchronized and stored in the database but is excluded from platform display by default. This is an administrator-global setting for now; future work may make it per profile.
- Improve title detail pages by consolidating ratings into a more readable layout and removing the community score (the followed-friends score is the intended current social score).

### Social and responsive UI follow-up

- Public profiles are discoverable and use one-way follow/unfollow; the Social tab exposes followers and following.
- The UI cleanup removes focus-driven viewport movement, narrows compact lists, and has been checked at desktop and phone widths.

## Pre-deployment UI consistency pass (completed locally)

The user resumed this pass after reviewing Settings, Notifications, and the list editor. It is complete locally; the isolated VPS remains unchanged pending review.

- Re-evaluate the UI foundation across the whole product. Preserve Astro/Tailwind only if a small representative prototype demonstrates AniList-level consistency, polish, responsive behavior, and smooth interaction without page-specific styling. Revisit ADR-018 if the evidence points elsewhere. Because Element Plus requires Vue, any switch must be an explicit architecture decision rather than mixing a second framework into selected pages.
- Standardize all product icons on one library. Font Awesome is the requested baseline because it aligns with the AniList reference. Dropdown chevrons, close controls, ellipses, dates, increment/decrement controls, hearts, status actions, and navigation must not mix arbitrary text glyphs and unrelated icon sets.
- Create reusable visual primitives for surfaces, fields, buttons, cards/notifications, popovers/dropdowns, and modal editors. Variants share geometry and interaction behavior. Use spacing, alignment, font weight, scale, imagery, and restrained color to establish hierarchy; avoid redundant headings, category labels, and explanatory copy.
- Redesign Settings to match the established list/detail language on desktop and phone. Place the combined Movie/Series List preference in Profile because it changes profile list presentation.
- Notifications contains provider-originated changes, automatic provider decisions, conflicts, first-import reconciliation, and unmatched-item decisions. A user's direct local edit must never create a notification. If that edit queues an outbound write, it can appear under pending connection updates as operational delivery state.
- Rebuild Notifications and pending connection updates from the shared card primitive so normal, warning, conflict, unmatched, failure, confirmed, and pending variants remain visually related and compact.
- Every transient overlay supports outside-click and Escape dismissal. A dirty or destructive form receives discard protection before closing. Focus returns to its opener and behavior is consistent for pointer, keyboard, and touch users.
- Rework the quick/full list editor to follow the supplied AniList editor composition: visual header, clear close action, balanced field grid, status, score, episode progress where applicable, dates, rewatch count, notes, save, and delete. Omit custom lists, favorite, and per-entry privacy.
- Finish with a product-wide alignment, spacing, state, motion, and responsive audit. The next VPS deployment cannot begin until the selected UI/icon approach is documented and representative desktop/phone screenshots, build, full tests, and authenticated local checks pass.

## Handoff rules

- Do not reinterpret 'finished frontend' as a cosmetic reskin or mockup: lists, social/privacy controls, and workflows must actually work.
- Do not resurrect automatic season-score inheritance; it was explicitly superseded.
- Zero means unrated, never a displayed rating. Completed entries do not automatically resume Watching in release one. Tracked-entry deletion preserves streaming-library membership.
- Do not assume an external snapshot lacking a value proves a deletion.
- Order status changes with status-specific source/time metadata. A newer explicit local decision wins over older provider state and is exported where supported; unrelated local edits do not change that ordering.
- A first import, including an empty fresh account, is an unapproved baseline. It cannot infer removals or write outbound until the user reviews the reconciliation summary.
- Confirmed local entry deletion clears personal entry data; ambiguous streaming-side removals use richer status interpretation and review instead. Do not conflate the two.
- Keep accepted requirements, proposed choices, and verified source facts distinct.
- Keep the local UI gate and user review separate from the next isolated VPS rebuild.


