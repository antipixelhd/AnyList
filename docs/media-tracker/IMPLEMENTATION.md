# Media Tracker implementation handoff

## Typography maintenance

Use [AGENT.md](../../AGENT.md) and the shared typography registry for new UI work. Font families, sizes, and weights are centralized; colors remain owned by existing themes/components. Tracker/global styles are ordered module entrypoints. Preserve the 62.5% root and compensated layout dimensions. See STATUS.md for validation of the 2026-09-24 migration.

## Netflix viewing-history import (2026-09-24)

Add the Netflix tab in Settings → Connections → Import and a provider-neutral review flow: upload/prepare, match resolution, progress review, final summary/commit. Keep draft state private and resumable; cancellation discards it. Parse English and German CSV exports, preserve viewing dates, group repeated titles and episodes, and resolve titles against the existing catalog before fetching metadata. Fetch a season once per language, then match its episodes locally; only unambiguous matches advance without review. Metadata provider failures must remain recoverable and must never silently become successful matches.

Commit accepted watches and inferred cumulative episodes atomically with existing-entry preservation, source deduplication, date handling, and an idempotent result. Validate malformed files, ambiguous movie/show names, colon-heavy and arc-style episode labels, sparse histories, specials, progress cutoffs, repeated imports, concurrent edits, cancellation, and desktop/phone review navigation. Use `reference/NetflixViewingHistory.csv` for local smoke checks without copying that personal export into versioned fixtures. Implementation status and validation evidence belong in STATUS.md.

Status: release-one implementation completed and validated locally as of 2026-09-19. See STATUS.md for current test and isolated-deployment evidence.

## Reading order and source of truth

Read PLAN.md, DECISIONS.md, GLOSSARY.md, and UI-REFERENCE.md. This file turns those accepted decisions into an implementation sequence; technical proposals below are agent recommendations, not invented additional user requirements. Local builds, provider checks, Google OAuth, and the isolated test deployment have been verified; see STATUS.md for current evidence. Production rollout remains outside this release-one test milestone.

Working repository: `../..`. Read-only upstream reference: `../../../reference Repos/scrob`, commit 3d75f172fc054ed90c39af9d336d5f5feda40d54. These are the canonical planning files and are versioned with the application. Preserve license notices and the read-only references. Do not push to the local-reference origin.

## Delivery sequence

### 1. Establish the local baseline and data contract

- Inspect applicable instructions and current dependencies; run appropriate existing baseline checks once the runtime is configured. Capture existing failures without attributing them to new changes.
- Inventory schemas/routes for users, catalog mappings, ratings, watch events, playback progress, lists, collection sources, connections, sync jobs, and privacy.
- Define migration/backfill behavior before changing storage. Separate tracked-list membership from streaming library membership. Existing generic/custom lists are not the new tracked list by default; preserve stored data even if their UI is out of scope.
- Map existing provider capabilities honestly: readable/writable fields, rating scales, completion, library removal, active-playback dismissal/restoration, stable identifiers, timestamps, complete/incremental snapshots.
- [x] Validate free external-score enrichment through MDBList with a live free key. Missing scores stay missing; no paid-only fallback.

Deliverable: runnable local baseline, documented schema/connector changes, migration strategy, and any concrete capability limits that need a product decision.

### 2. Build the visual foundation and primary UI

- Keep Scrob's technology stack unless a concrete implementation need justifies change; replace its layout and styling freely.
- Define dark palette, spacing, typography, cover/banner sizing, navigation, forms, focus states, responsive layouts, and reusable list/detail/profile components from the supplied screenshots.
- Use references/anilist-profile-list-filters.png for the full profile-list composition: profile tabs, desktop filter sidebar, search/status shortcuts, metadata filters/year/sorting, grouped tables, and view selector. Combine it with the prior row-hover/editor references; adapt sidebar behavior for mobile.
- Build one shared profile-list view for owner and visitors. Status groups stack vertically, with sorting/filtering/search, compact/grid modes, progress and favorite controls. Owner-only actions are authorization-gated, not just hidden.
- Row ellipsis opens the quick editor; title opens details. Touch and keyboard users must access these actions without hover.
- Build movie/series detail layouts with source-specific external scores, eligible followed-user scores, status/favorite/editor access, and season progress/ratings for TV.
- Implement profile identity/customization, following, basic totals/favorites, Home activity, private Notifications, and owner-only Library tab.
- Include loading, empty, partial-data, unavailable-image, and recoverable-error states. Avoid exposing provider/debug terminology in normal product flows.

Deliverable: coherent, usable core interface at desktop and phone sizes, wired progressively to real application state rather than static mock data.

### 3. Implement tracking, ratings, privacy, and dates

- Add explicit statuses and independent granular watch/progress state; cumulative released regular episodes, season watched/unwatched operations, rollback confirmation, and completion priority.
- Implement manual/calculated show-rating modes and first-season prompt; preserve manual score on mode switch. Empty calculated set is unrated. No episode ratings or automatic season score inheritance.
- Map zero input to no rating and exclude it from aggregates. Preserve precision for calculated scores; format one decimal for averages. Allow any status with/without a rating and without progress evidence.
- Implement date rules, editable private notes, rewatch count, favorites, profile privacy, anonymous-access setting, and email-provisioned Google SSO. Disable open registration/automatic account creation.
- Enforce private profile exclusions in API data, aggregates, feeds, and caches; note confidentiality and Library owner access must also be enforced server-side.

Deliverable: persisted end-to-end daily tracking and social workflows with migrations and focused behavioral tests.

### 4. Extend reconciliation and Notifications

- Store successful per-connection baselines, change provenance, and import/outbound acknowledgments. Baseline absence is never deletion evidence; incomplete/error snapshots must not advance destructive reconciliation.
- First connection imports and summarizes reconciliation before outbound writes. Keep ambiguous conflicts local until resolved.
- Keep library mirroring independent from tracked status/history. Add missing Nuvio removal detection; add supported change propagation across accounts, without feedback loops.
- For verified unfinished playback removal, apply movie Dropped/show Paused locally, remove corresponding playback state across Stremio/Nuvio, and gate other tracking-platform state until confirmation. Completed wins. Correcting to Watching restores supported playback state on next sync.
- Implement durable per-title Notifications and auto-confirm preference. Real uncertainty/conflicts/first merges/destructive deletion remain review-required. Initial imports do not flood social activity; inferred activity publishes after confirmation.
- Export effective scores with destination rounding and exact midpoint-up behavior. Store enough outbound projection/acknowledgment information to recognize echoes without losing original precision or switching rating mode.
- Confirmed tracked deletion clears all personal entry state while preserving library membership and shared catalog. Minimal deletion markers prevent stale resurrection; retry supported resets independently and report failures accurately.

Deliverable: testable change-aware sync that matches the accepted behavior, with explicit per-provider limitations instead of unsupported promises.

### 5. Verify, then stage on VPS

- Complete the acceptance matrix below; inspect the actual interface on desktop/mobile with representative data and empty/error states.
- Exercise migrations on disposable data and check that reference repositories remain unchanged.
- Inspect existing VPS infrastructure and choose isolated service names, network/ports, database/storage, and test hostname. Use the root AGENTS.md connection instructions. Do not replace existing services.
- Deploy only after local verification, using test accounts before real accounts for outbound sync. Verify Google callback configuration, private access, worker/scheduler execution, logs, persistent state, and backup/restore approach for the test instance.
- Report implemented behavior, checks run, provider limitations, and outstanding release blockers. Do not call release complete because the UI looks finished while required data behavior is missing.

## Proposed technical defaults

- Keep existing local media IDs/cross-provider mappings; movie/TV search uses the existing catalog integration and resolves discoveries to stable local records. Do not require mirroring an entire external database to implement a central catalog.
- Distinguish tracked state, stream-library membership, watched events, playback position, and rating mode in the domain even if some existing tables can be reused.
- Use null for unrated internally, numeric half-point validation for manual scores, and non-rounded intermediate arithmetic for averages. Convert legacy zero values intentionally during migration.
- Use regular season/episode ordering and exclude specials from automatic backfill; inspect existing alternate-order mapping rather than guessing coordinates. Preserve inferred versus observed provenance, without inventing exact timestamps for inferred past episodes.
- New activity may set missing dates from observation time; repeated polling must not overwrite manually edited/cleared dates. Import historical dates only when supported by evidence.
- Keep connector reconciliation separate from HTTP handlers where practical, using idempotent change application, per-user/connection coordination, retryable outbound operations, and suppression of echoed changes. Reuse Scrob's three-way reconciliation patterns where applicable.
- Carry pending/unavailable provider actions explicitly; never present a failed or unsupported remote deletion as successfully propagated.
- Use credential precedence user key then admin key, quota-aware caching, and source-labeled external ratings. Do not expose keys to other users or logs.

These choices can be refined from implementation evidence without reopening settled product requirements. Escalate a verified provider limitation only if it materially changes the promised behavior.

## Release acceptance matrix

| Scenario | Required result |
| --- | --- |
| Owner opens Movie/Series List | Routes to their profile list; same presentation another authorized viewer sees, with owner-only editing. |
| Desktop hover / keyboard / touch | Quick editor and detail navigation remain distinct and accessible. |
| Planning title with rating or zero | Rating allowed without progress; zero is unrated and absent from averages. |
| Show manually rated, seasons untouched | Only whole-show score exists; no implied season scores. |
| First season rating | Per-show mode choice, calculated default; other seasons stay unrated. |
| Mode changes / last season cleared | Saved manual value survives explicit switches; empty calculated set remains unrated. |
| New season release | No invented season score or watched state; existing Completed status stays; unwatched indicator updates. |
| Later watched episode / earlier season rollback | Preceding released episodes fill; rollback clears later progress with the required prompt. |
| Completed title watched again | Remains Completed automatically in release one. |
| Dates | Planning empty; newly started gets start; completion gets finish; manual Completed gets finish only; unknown imports stay unknown; edits/clears survive polling. |
| Private profile / notes / Library | Unauthorized responses and aggregates leak none of the protected data. |
| Anonymous browsing off/on | Login gate by default; enabling allows only eligible public catalog/profile views. |
| Unknown Google account | Cannot self-provision; invited existing email can log in after configuration. |
| Fresh empty connected account | Establishes baseline; removes no existing local/remote tracking or library state. |
| First import / conflicting values | Reconciliation summary before outbound; local ambiguous values preserved; no historical feed flood. |
| Library added/removed remotely | Mirrors library membership but never creates/deletes a tracked entry. |
| Verified unfinished playback removed | Movie Dropped/show Paused; playback removal mirrors across streaming connections; other trackers wait for confirmation. |
| Uncertain/partial snapshot | No inferred destructive changes; review if needed, failed snapshots do not establish false baselines. |
| notification corrected to Watching | Supported restoration on next sync, no repeated removal loop. |
| Auto-confirm enabled | Ordinary status interpretations confirm; true conflicts/uncertainty/first merges/deletions still require review. |
| Calculated score exported and echoed | Correct conversion; original local precision and rating mode preserved. |
| Tracked entry deleted | Confirmation; personal entry data cleared; library retained; shared data retained; marker blocks stale resurrection. |
| Failed outbound removal | Accurate pending/failure state and retry; no false success or uncontrolled reimport. |
| External score missing/quota exhausted | Honest unavailable/stale state, source labels intact, free-key policy honored. |

## Future backlog, explicitly outside release one

### Product and UX follow-up implemented locally

The user subsequently authorized these requirements. They are implemented locally and remain gated from VPS redeployment until final regression verification.

- **Private sync inbox:** implemented with priority, seen/dismissed lifecycle, retention controls, automatic low-priority cleanup, pinned decisions, connection actions, durable unmatched matching, per-provider ignores, and outbound conflict gates.
- **Settings cleanup:** inherited player/history, maintenance, and legacy data-clearing panels are omitted from the Media Tracker Account page; retained metadata, security and export controls stay available.
- **Combined list:** implemented as the default “Movie/Series List” with a Type column.
- **Anime classification:** implemented as administrator-global display filtering; storage and provider sync remain intact.
- **Detail pages:** personal/following scores are visually separated from external ratings; the community score is removed.
- **Social:** public profile discovery, one-way follow/unfollow and the functional Social tab are implemented.
- **UI/UX polish:** list focus no longer changes the viewport, compact list content is narrower, and desktop/phone browser checks passed.

### Completed local UI pass before VPS redeployment

The user resumed this pass. It was completed and verified locally before the “Verify, then stage on VPS” step:

1. [x] ADR-018 retains Astro/Tailwind after the representative Settings, notice-card, editor, and responsive comparison; Element Plus remains unsuitable as a piecemeal Vue runtime.
2. [x] Shared tracker controls and notice/editor/Settings primitives are defined; Font Awesome free-solid icons render through one server-side Astro component.
3. [x] Settings and Notifications use the shared foundation; combined-list presentation moved to Profile.
4. [x] Direct local delivery state is absent from the provider review inbox and remains available under pending connection updates.
5. [x] The AniList-derived editor omits favorite, custom lists, and per-entry privacy and provides dirty-state/outside-click/Escape/focus-return behavior.
6. [x] Representative desktop and 390×844 checks passed for Settings and the editor; earlier primary-route desktop/phone checks remain valid.
7. [x] Production build, complete 1,111-test backend suite, `mt008 → mt007 → mt008`, and authenticated two-account preview passed. Obtain user review before rebuilding the isolated VPS instance.

- Books and games with their appropriate metadata/rating sources.
- Anime/manga only with corresponding AniList sync and provider-native entry grouping.
- Detached season list entries and advanced viewing/rewatch options.
- Sharing movies/lists and a recommended-by-friends experience, designed against the finished core.
- A Stremio addon/catalogue or possible AIOmetaData integration exposing the platform's Planning movies/series to connected clients. This is a user-requested future idea; integration feasibility is not yet verified.
- Additional provider capabilities, including possible Harbor rating integration if supported; do not assume availability.
- Branding beyond the temporary Media Tracker name.

Custom lists, bulk editing, likes, comments, messaging, and per-entry privacy are not release-one requirements. Do not add them solely because a screenshot or upstream app contains them.



## Cloud history field reconciliation checkpoint

- [x] Reconcile cloud status, dates, and progress together; preserve local values on uncertain ordering, expose all changed fields in Notifications, and apply a confirmed decision atomically.

## Inherited-route privacy checkpoint

- [x] Apply the global anonymous-navigation gate to comments; keep inherited friends-only profiles private in discovery and follow flows; hide private social identities from public previews; prevent shared avatar caching.

## TVDB-native catalogue checkpoint

- [x] Route the manual episode refresh through the show's canonical provider and preserve TVDB identities/positions atomically.
- [x] Extend the scheduled shared catalogue sweep to TVDB-native titles, preserving global/admin/user key precedence and subscriber PINs.
- [x] Advance cumulative streaming history against preserved TVDB-native canonical episode positions.

## Isolated deployment checkpoint

- [x] Deploy committed build `83a4fca` to the isolated test project, preserve its PostgreSQL volume at `mt008`, verify application/database health and the Media Tracker manifest/source marker, and verify unrelated VPS Compose projects remain running.
- [x] Exercise outbound synchronization only with disposable/test Stremio and Nuvio state, including the first-import approval barrier and observed remote add/remove round trips; restore the remote state and disable ordinary push flags afterwards.
- [x] Verify Google OIDC against the dedicated HTTPS test client and preserve password recovery.

## Local precedence and deletion-delivery checkpoint

- [x] Persist status-specific source/time metadata; compare provider observations against that timestamp rather than unrelated note/rating edits.
- [x] Queue local playback dismissals to every eligible Stremio/Nuvio account when an explicit local edit leaves Watching.
- [x] Keep first and empty snapshots non-destructive and unapproved until the reconciliation summary is confirmed.
- [x] Execute confirmed local deletion through durable streaming and cloud action queues. Streaming resets do not alter library membership. Trakt, Simkl, and MDBList resets wait for approved cloud reconciliation and remove only supported tracking state, never provider collection/library membership.
- [x] Include pending cloud delivery in Notifications' collapsed operational queue; keep direct local edits out of the provider-review inbox.
- [x] Migrations `mt009`/`mt010`, focused reconciliation tests, provider adapter tests, and the complete 1,126-test backend suite pass.

## Authentication-shell checkpoint

- [x] Apply the Media Tracker brand, Font Awesome icon language, AniList-derived typography, and release-accurate feature copy to login/registration and the legacy base title/install labels.
- [x] Carry the same product identity through About, PWA/offline metadata, browser artwork, activation/reset email, TOTP enrollment, health responses, backup naming, startup logs, and the repository README. Preserve Scrob attribution and compatibility identifiers where they describe the upstream project or a protocol-level contract.

