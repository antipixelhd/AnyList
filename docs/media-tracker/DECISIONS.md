# Decision records

Updated: 2026-09-19. Accepted means explicitly agreed during interview. Proposed implementation details live in PLAN.md.

## ADR-001 — Scrob foundation (accepted)

Use Scrob, with an unrestricted frontend redesign. Movies and series suffice for release one, allowing reuse of existing social features, provisioning, and playback sync. Yamtrack's broader catalog no longer outweighs this advantage. Books/games require later domain expansion. Do not transplant Scrob's coupled sync into Yamtrack.

## ADR-002 — Invite-only accounts, configurable visitor access (accepted)

Admin creates accounts by email; Google SSO authenticates existing users. Anonymous browsing is off by default and controlled by an instance switch. Private profiles hide all personal state from other users, including aggregate calculations. Public visibility does not bypass the visitor-access setting.

## ADR-003 — One-way social graph (accepted)

Following is unilateral. 'Friends' ratings means ratings by followed people, not mutual follows or approved friendship requests. Feeds respect privacy. No comments, likes, messaging, or recommendation-sharing system in release one.

## ADR-004 — Show entries and independent watch history (accepted)

Whole shows appear in TV search/lists. Episodes and seasons retain granular watched state. Completed means caught up or a deliberate label, not a promise that future episodes are watched. Marking a show Completed always marks every currently released regular episode watched. Episodes released later remain unwatched; while the entry remains Completed, the list shows the number of newly available seasons rather than an unwatched-episode count. Moving the entry to Watching removes that new-season indicator. A season can also be marked watched. Anime will use its provider's entry structure.

Accepted refinement: progress is cumulative. A detected watched later episode/season marks preceding regular episodes watched. Per-episode storage remains; gap-preserving progress was not accepted. Specials and unreleased episodes are excluded. Clearing an earlier watched season also clears later seasons after confirmation, while preserving a deliberately selected Completed status.

## ADR-005 — Manual or calculated show ratings (accepted; supersedes inherited season defaults)

A manual show rating does not create season ratings. First season rating asks the user to choose a calculated average or an independent manual show score. Calculation includes explicitly rated regular seasons only; exclude unrated seasons and specials. Retain separate season ratings when overriding the show score. Inputs use half-point steps from zero to ten; calculated displays use one decimal place.

Latest refinement: zero means unrated, never a displayed score or an average contribution. Meaningful scores are 0.5–10; this supersedes the original valid-zero decision. No recorded progress is required to rate; no rating is required to select a status.

Final clarification: no status-based rating restriction. Planning may have a rating as well.

The first season-rating prompt is per show and defaults to calculated mode. Never automatically populate other seasons. Preserve the former manual score for explicit restoration on switching back. Removing the final season rating in calculated mode makes the show unrated.

Superseded: assigning a whole-show score as an implicit score to every season, then averaging overrides with inherited scores. Do not implement that earlier proposal.

## ADR-006 — Preserve existing bidirectional sync (accepted)

Retain optional Scrob connections while replacing the UI. Adapt to provider capabilities and score scales; preserve local precision. Change-aware reconciliation, reliable timestamps, and protection for ambiguous local edits are required. Export the effective manual/calculated score, rounded to the nearest supported increment with half ties upward. Recognize returned conversions without overwriting the local original or mode. First connection imports and displays a reconciliation summary before outbound writes; ambiguous conflicts preserve local data and require resolution before export.

## ADR-007 — Free external rating data only (accepted)

IMDb and distinct RT critic/audience scores are desired. Use free API-key options only, preferring configured user credentials over admin credentials. MDBList is a research candidate, not an accepted dependency until free-key coverage is validated. Unavailable source scores must stay unavailable.

## ADR-008 — Delivery scope and style (accepted)

Prioritize polished list/detail/profile/feed workflows for movies and TV, following AniList closely. Dark initially; compact lists with grid toggle; desktop and phone support. Include favorites, sorting/filtering, search, quick edits, progress, profile customization, and simple activity. Defer custom lists, bulk editing, detached season entries, extra media types, and friend-sharing/recommendations.

User screenshots establish row-hover ellipsis -> quick editor and title click -> detail page. Preserve screenshots and interaction notes in UI-REFERENCE.md; do not import every screenshot feature as a requirement.

## ADR-009 — Local deletion versus provider removal (accepted)

Deleting a main list entry is a confirmed full deletion of that user's entry state, including ratings, history, dates, and progress; propagate supported deletion operations. Shared catalog data and other users remain intact. This supersedes the assistant proposal to retain personal data after list deletion.

A streaming provider's limited model cannot express all local states. Removal of unfinished active playback is intended to become Dropped for a movie or Paused for a show; exact detection remains open. Retain local personal data and present this interpretation in private Notifications for confirmation, status correction, and quick rating. ADR-010 separates library removal; ADR-011 defines provisional application/propagation.

Refinement: keep a minimal deletion marker (title, time, pending acknowledgments) after confirmed local deletion to prevent stale resurrection, without preserving deleted personal content.

## ADR-010 — Streaming library is distinct from tracked lists (accepted)

Streaming Library membership is an AnyList-owned choice that works with no connected service and never automatically adds/removes a main tracked entry. Mirror that choice to eligible Stremio/Nuvio connections after their reconciliation gates; a later connection can receive an earlier local choice. Main lists focus on started media, with Plan to Watch as the explicit unstarted exception. Library removal alone is not the Paused/Dropped signal. Precisely distinguishing removal from active playback remains unresolved. The local-first clarification was accepted on 2026-09-21 and supersedes connection-only Library eligibility.

## ADR-011 — Provider-specific review gating (accepted)

Apply inferred Paused/Dropped locally. Mirror the underlying playback removal to Stremio/Nuvio accounts immediately, but hold inferred status propagation to other tracking platforms until confirmation. Auto-confirm is per user, off by default, and covers ordinary inferred Paused/Dropped changes; rating prompts remain. Conflicts, uncertainty, first merges, and destructive deletion still require review. Publish inferred status activity after confirmation. This supersedes blanket holds on all outbound effects. Correcting an event to Watching restores supported playback state on the next sync.

## ADR-012 — Shared profile list presentation (accepted)

Movie List and Series List navigation open the current user's profile list routes. Use the same list screen and presentation for owner and other viewers, with owner-only actions and privacy protection. Group statuses vertically and support filtering. Home has self/followed activity and Notifications count. Editor includes optional editable auto-filled dates, private notes, and rewatch count; no per-entry privacy or custom lists.

## ADR-013 — Completion and safe sync baselines (accepted)

Completed takes priority over inferred paused/dropped and does not automatically become Watching on repeat/later playback in release one. New empty accounts establish a baseline and cannot wipe state. Detect changes per connection; uncertainty goes to Notifications. Confirmed local tracked-entry deletion preserves streaming-library membership.

## ADR-014 — Dates and cumulative correction (accepted)

Planning has no default dates; starting sets start date; completion sets finish date. Manually adding Completed defaults only finish date to today. Start remains unknown unless supplied. Dates are editable/clearable; unknown imported dates remain unknown. Newly observed streaming activity may use observation time with accepted sync latency. Marking an earlier season unwatched also clears later seasons and prompts when not the latest watched season.

## ADR-015 — Library visibility and future catalogue export (accepted)

Library is an owner-only profile tab. Membership never creates tracked entries. Future backlog: a Stremio addon/catalogue, potentially integrated with AIOmetaData, exposing movie/series Planning sections to connected Stremio/Nuvio clients. Feasibility remains unverified; not part of release one.

## ADR-016 — Working copy and staged deployment (accepted)

Use temporary name Media Tracker and a separate Scrob copy at C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker. Preserve references. Develop/test locally, then use an isolated VPS test instance and test connections before real outbound accounts. The shared specification was confirmed and implementation authorized on 2026-09-18.

## ADR-017 — AniList as the UI/UX baseline (accepted)

Stay as close to AniList as practical for information hierarchy, navigation, cover/banner composition, colors, spacing, animations, menus and hover/focus behavior. Extend that baseline for movies, whole-show/season behavior, synchronization review, responsive needs and accessibility. Deviations should solve a concrete Media Tracker requirement rather than introduce an unrelated visual language. Pixel identity is not required where the domain or interaction differs, but fidelity takes priority over independent reinterpretation. Strengthened by the user during active release development on 2026-09-18.

## ADR-018 — Astro-native UI system with Font Awesome (implementation decision)

Keep Astro 6 and Tailwind CSS 4 as the UI foundation, with small purpose-built Astro components, shared Media Tracker design tokens, native controls such as `dialog`/`details`, and a single shared app bar with uniform server-rendered navigation. Do not add Element Plus: its current package is a Vue 3 component library with a Vue peer runtime and its own global theme-token cascade, so adopting it would add a second component/runtime architecture and require overriding a design system that does not match the chosen AniList-derived visual language.

Build reusable behavior where the product needs it—status menus, list editors, review cards, settings navigation, accessible hover/focus/touch equivalents—rather than introducing a full pre-styled component library. The representative pass across Settings, Notifications, the shared list editor, list controls, detail actions, and phone layouts showed that this foundation can reproduce the required AniList-derived finish without a second framework.

Use the official Font Awesome SVG core and free-solid package as the single product-control icon source. Render only imported icons to server-side SVG through the shared Astro `Icon` component; do not load the browser kit or the full icon library. This replaces copied inline SVGs and text glyphs while keeping the server-first architecture and predictable accessibility attributes. Shared `NoticeCard`, button, field, navigation, dialog, and responsive rules provide the common geometry. Respect `prefers-reduced-motion` and use reusable lifecycle-safe setup functions for shared interactions. This decision may be revisited if the frontend later adopts a single client framework.

The user requested a scoped exception on 2026-09-24: the profile bio editor uses MDXEditor in an Astro React island for its rich-text and Raw Markdown controls. Astro remains the page framework and the shared visual system applies to the island.

## ADR-019 — Status-specific provenance and local decision precedence (accepted)

Record the source and effective change time of each tracked status separately from the entry's general update time. Notes, ratings, favorites, and other edits must not make an old status appear newer. A reliably newer provider status may be reconciled normally; an explicit local status decision remains authoritative over older provider observations and creates the supported outbound correction. If ordering is missing or uncertain, preserve the local value and require review.

First imports are baselines, not deletion evidence. A new or empty account cannot infer removals, fan out resets, or enable outbound writes until its reconciliation summary is approved. Confirmed local entry deletion creates durable per-destination reset jobs and retains only the minimal deletion marker until acknowledgments arrive. Stremio/Nuvio resets clear viewing state without removing library membership; Trakt, Simkl, and MDBList clear supported history/rating/list state without removing provider collection/library state. Unsupported capabilities remain explicit rather than being reported as successful.



## ADR-020 — Opt-in completion rating notifications (accepted)

Use standards-based Web Push through the existing PWA for Windows and Android. Notifications are per-device and require an explicit browser permission grant. Queue them only when a subsequent provider sync newly completes an unrated title; exclude initial imports and direct local completion. The payload identifies the title, includes cover art when the browser supports it, and deep-links to the title's quick-rating surface. Rating resolves the in-app prompt. Keep local 0.5 precision and reuse one accessible half-star interaction across lists, title details, in-app Notifications, and push deep links.

Generate and persist one VAPID key pair per Media Tracker instance in application data. Store subscriptions per user, discard expired endpoints, and never treat delivery as authoritative state. The in-app Notification remains the durable source of the rating request.

## ADR-021 — Reconcile overlapping sources before targeted fan-out (accepted 2026-09-21)

Independent provider pulls retain their configured intervals, but overlapping pulls for one AnyList user form a bounded reconciliation cycle: compare each complete snapshot with its own approved baseline, derive changes, resolve compatible observations once, apply local truth, then target only eligible destinations affected by the result. This avoids treating each source's sequential import as final truth or echoing a change back to its source; a failed source cannot imply deletion or indefinitely block unrelated safe deltas. Order competing changes by trustworthy comparable modification evidence, not provider rank; explicit local corrections beat proven stale observations, equally trustworthy simultaneous forward progress takes the furthest point, and unorderable conflicts preserve local state for review. Prompt outbound delivery and durable per-destination retry remain separate from user-visible pull intervals.

## ADR-022 — Read-only offline navigation within an open session (accepted 2026-09-21)

Cache safely displayable content for read-only navigation while an AnyList tab is already open, not as a promise that an offline first load or refresh works. This gives previously loaded pages and list editors continuity without making offline writes, stale connection state, or a service-worker app shell appear authoritative. Partition private data by AnyList user, clear it on explicit logout/account switch, never cache credentials, and localize missing-data/offline feedback rather than replacing the entire live page.

## ADR-023 — Reviewed Netflix viewing-history import (accepted 2026-09-24)

Netflix CSV import uses four steps: prepare, resolve uncertain matches, review show progress, and finalize. Cancel is available throughout. The import changes personal state only after the final Import action. Uncertain matches require an explicit confirm, remap, or skip; season episode counts alone cannot identify watched episodes.

Netflix season labels can split a TMDB aired season into multiple parts. When the labelled TMDB season is absent, search the show's available seasons by exact episode title and accept only a unique match. A 404 for one season leaves unresolved episodes for review instead of aborting the CSV; other provider failures remain visible.

Progress review shows incompletely represented shows first, sorted by title with posters. Any show with missing or uncertain episodes defaults to Partial at the highest matched episode; only fully represented released seasons default to Completed. Partial offers Watching, Paused, or Dropped, defaulting to Watching. Selected progress is cumulative; lowering its endpoint excludes later CSV rows. Existing progress never decreases. Automatic suggestions preserve existing manual statuses, while an explicit review status change overrides them. Skip leaves the title untouched.

Accepted viewing dates set the earliest show start date and, for Completed shows, the latest show finish date. A movie watch sets its finish date. Existing nonempty dates remain authoritative. Inferred episodes have unknown watch dates. Reimports deduplicate historical watches. English and German exports are in scope; the import harness should allow later providers such as Disney+ and Prime without committing to their formats now.
