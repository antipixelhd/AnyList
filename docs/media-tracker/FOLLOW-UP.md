# Next development session

User verification feedback. Development resumed on 2026-09-18; use the checked items and STATUS.md for current evidence. Continue to honor PLAN.md and the provider limitations in STATUS.md.

## Completed local queue and external gates

1. **Checkpoint the current work:** completed through the focused commits recorded below. Continue using small, descriptive commits so later changes can be reviewed and reverted independently.
2. **Study AniList concretely:** completed and recorded in `ANILIST-ANALYSIS.md`, including live desktop/phone profile, list, filter, cover-hover, and title-detail evidence. Authenticated editor behavior remains grounded in the user's screenshots.
3. **Choose and document the UI foundation:** completed in ADR-018. Continue with Astro 6, Tailwind CSS 4, shared tokens and purpose-built accessible Astro components; use the shared app bar and one standard server-rendered navigation lifecycle on every page. Element Plus was rejected because it requires Vue 3 and imposes a second theme/runtime architecture.
4. **Finish the visual pass:** Home/Notifications, list cover interactions, detail-page information/status actions, shared app bar, responsive controls, and the release-facing authentication/About/PWA identity are implemented and verified.
5. **Repair incomplete routes and data:** series posters/descriptions and the unified Account/Profile/Connections/Administration settings shell are implemented and verified.

Implementation resumed. Preserve the current provider safeguards: real-account outbound sync remains disabled and first reconciliation remains unapproved.

- [x] **Git checkpoints:** review accumulated changes, exclude credentials/local runtime files, and create coherent commits with clear descriptions. Use focused commits going forward. Preserve reference repositories; do not push to the local-reference origin.
- [x] **Concrete AniList UI/UX analysis:** inspected https://anilist.co/user/antipixel/ and its list/detail flows on desktop and phone. `ANILIST-ANALYSIS.md` records navigation, composition, filtering, cover hover, status/editor evidence, motion/input adaptations, responsive order, screenshots, and the intentional movie/TV deviations.
- [x] **UI component choice:** ADR-018 selects the existing Astro 6 + Tailwind CSS 4 foundation with reusable custom components and native accessible controls. The shared server-rendered app bar keeps navigation consistent without adding a client router; current Element Plus metadata confirms its Vue 3 peer runtime, making it a poor fit for this server-first frontend.
- [x] **Home and Notifications:** replace the plain-text presentation with cover images, clearer visual structure and contextual action buttons. Keep private sync review separate from social activity.
- [x] **Movie list polish:** retain the current layout; add an enlarged thumbnail hover preview and place the ellipsis action over the thumbnail on hover/focus, with a usable touch control.
- [x] **Series metadata:** whole-series catalogue rows are now enriched directly and repaired from canonical show metadata without losing tracker markers. Verified against the imported provider-test series: its TMDB poster loaded at 500×750 and its synopsis rendered on the detail page. Evidence: `verification/series-metadata-detail.png`; 33 focused tests and the 1,076-test backend suite passed.
- [x] **Settings and profile editing:** Account, Profile, Connections and Administration now share a Media Tracker settings shell, navigation, design tokens and responsive behavior. Profile privacy exposes the agreed Private/Public choices; inherited friends-only values remain private until explicitly published. Browser verified Account → Profile navigation and controls, and Profile/Connections at 390 px with no horizontal overflow. Evidence: `verification/settings-mobile.png`.
- [x] **Detail pages:** AniList-derived banner/poster composition, compact facts, score overview, public community average, followed-user ratings, synopsis/genres, external links and image-led season cards are implemented. The split status arrow opens Set as Completed, Set as Watching, Set as Planning and Open List Editor while retaining released-episode completion behavior. Desktop/phone browser verification passed with no mobile overflow; evidence: `verification/detail-expanded-desktop.png` and `verification/detail-expanded-mobile.png`.
- [x] **Isolated VPS test deployment:** rebuilt committed build `83a4fca`, preserved the existing database volume at `mt008`, confirmed application/database health and the Media Tracker manifest/source marker, checked that existing VPS projects were untouched, and removed temporary transfer archives. See `STATUS.md` for the deployment handoff.

This checklist supplements the two external verification gates in STATUS.md; it does not replace them. Real-account outbound sync remains disabled pending controlled test verification.

## Product and UX backlog completed locally

The user authorized this previously deferred work before the next VPS update. It is now implemented and locally verified:

- [x] Notifications is a compact private notification inbox. Nonconflicting imports apply automatically, resolved low-priority items disappear after being seen, unresolved decisions remain pinned, pending connection actions remain visible, and local edits do not create inbox notices. Low-priority visibility and retention are configurable.
- [x] Trakt, Simkl, and MDBList unmatched imports create deduplicated review items. Users can search the catalogue, save a durable manual match, or ignore the external item for that provider. Ignored items are grouped in Settings and can be restored. Unresolved items block that provider's outbound writes.
- [x] Account Settings hides inherited player/history controls and maintenance/data-clearing panels that do not belong to the release-one tracker. Movie and series entries use a combined list by default with a Type column. Anime detection is display-only, hidden globally by default, and leaves stored/synced data intact.
- [x] Title pages separate personal/following scores from external provider scores and no longer expose the old community score.
- [x] Public profile search, one-way follow/unfollow, follower/following counts, and a working Social tab are implemented.
- [x] List focus no longer changes the viewport, compact list content is narrower, and desktop/phone navigation, lists, details, Settings, Notifications, and unmatched-catalogue search passed local browser verification.

Implementation checkpoints: `5c091de` (social lists and inbox), `1b02479` (unmatched provider reconciliation), and `09a611e` (final interface cleanup and provider-match compatibility). The final migration downgrade/upgrade passed, all 1,110 backend tests passed with 38 expected skips, the production frontend build passed, and the restarted local preview passed both-account authentication/page checks. No real-account outbound write was enabled or performed.

## Required UI/UX pass before the next VPS update

The user resumed implementation and this local pass is complete. It was subsequently included in the isolated `mt008` VPS deployment recorded in STATUS.md.

- [x] **Re-audit the component and icon foundation before changing screens.** ADR-018 retains Astro/Tailwind after the representative Settings, cards, list editor, desktop, and phone pass. Product controls now use a shared server-rendered Font Awesome component; copied inline SVGs and the remaining settings text glyph were removed from the primary tracker/settings surfaces.
- [x] **Define the design primitives once.** Shared buttons, quiet/destructive actions, icon buttons, fields, Settings surfaces, notice variants, editor geometry, focus, reduced-motion, and responsive rules now live in the tracker design layer. `NoticeCard` supplies image/icon, severity, metadata, message, detail, and action variants.
- [x] **Redesign Settings in the same visual language as lists and details.** Account/Profile/Connections/Notifications use consistent navigation, cards, control geometry, spacing, and icons. The default-on combined-list setting now lives in Profile.
- [x] **Rebuild Notifications and pending connection updates from the shared card system.** Both use `NoticeCard`. Direct-user `outbound_pending` records are excluded from the provider review inbox and exposed only in the operational pending connection queue; regression coverage enforces the separation.
- [x] **Make dismissible overlays behave consistently.** The list editor, status completion choice, season-rating choice, video player, and tracker popovers support outside-click and Escape dismissal. The list editor protects dirty state and restores focus to its opener.
- [x] **Rework the list editor against the supplied AniList editor reference.** The editor now uses the title banner/poster header, close action, responsive field grid, show-score mode, progress, dates, rewatch count, private notes, save, and delete actions. Custom lists, favorite, and per-entry privacy are absent.
- [x] **Run a whole-product spacing and alignment audit.** Settings and the editor were reviewed at desktop and 390×844; Home, lists, details, and Notifications retain the earlier desktop/phone checks. No horizontal overflow was observed; control/icon alignment and reduced-motion rules are shared.
- [x] **Gate VPS work on evidence.** Browser review covered Account/Profile Settings, the list editor, combined list, and Notifications; the 390×844 Account/editor pass succeeded. `mt008 → mt007 → mt008`, all 1,111 backend tests, the production frontend build, and authenticated local checks for both accounts passed. ADR-018 records the UI/icon tradeoff. The verified work was subsequently deployed to the isolated VPS.




## Provider reconciliation follow-up completed

- [x] Treat imported cloud status, start date, finish date, and progress as one auditable change set. Reliably newer values apply together and produce a compact Notification; uncertain ordering preserves local data and requires a decision. First-connection reconciliation follows the same safeguard.
- [x] Audit inherited anonymous/profile routes. Logged-out comments follow the instance switch; search, follow, social previews, and avatar delivery preserve release-one profile privacy.
- [x] Refresh a TVDB-native title manually without translating or renumbering its canonical episode rows.
- [x] Include TVDB-native tracked titles in the scheduled catalogue sweep using an effective configured TVDB credential.
- [x] Apply cumulative streaming observations to TVDB-native shows using their preserved canonical positions.
- [x] Replace remaining signed-out Scrob branding and release-inaccurate auth marketing with the Media Tracker visual/copy system.
- [x] Complete the release-facing identity across About, activation/reset email, TOTP enrollment, PWA install/offline metadata, application health identity, backup naming, browser artwork, and the repository README while retaining upstream attribution and protocol-compatible identifiers.
- [x] Rebuild the isolated VPS test app from the committed local release, preserve its database/volumes, migrate through `mt008`, verify health, and confirm neighboring services remain running.
- [x] Verify a controlled outbound provider workflow on disposable Stremio and Nuvio connections only. Each provider completed first import, blocked a pre-approval write, accepted the reviewed reconciliation, observed a remote add and removal, restored the disposable remote state, and completed a clean second pull. Ordinary outbound flags were disabled again after verification.
- [x] Verify Google OIDC with the dedicated HTTPS test client, exact callback, invite-only email match, same-site return, and retained administrator password recovery.

## Local-change precedence and destructive delivery completed

- [x] Store status source/time independently from general entry edits and use it for streaming and cloud change ordering.
- [x] Let explicit local transitions away from Watching queue playback removal across every connected Stremio/Nuvio account with outbound playback enabled.
- [x] Treat an empty first connection as an unapproved baseline; it cannot change the local status or fan out a removal.
- [x] Make confirmed local deletion authoritative for Stremio/Nuvio reset delivery after reconciliation approval, independent of ordinary mirror toggles, while retaining streaming-library membership.
- [x] Queue durable deletion acknowledgments for connected Trakt, Simkl, and MDBList accounts; do not run them until that provider's first-import reconciliation is approved. Provider collection/library state remains untouched.
- [x] Expose pending cloud resets alongside pending connection updates and retain sanitized retry state.
- [x] Validate migrations `mt009` and `mt010` through downgrade/upgrade and pass the complete 1,126-test backend suite plus 32 parameterized subtests.
- [x] Deploy commit `a5939f9` to the isolated VPS test project, preserve its volumes and disposable connections, migrate to `mt010`, confirm app/database health, verify Stremio/Nuvio push flags remain off, and confirm neighboring VPS services remain running.
- [x] Restart the local `mt010` preview, repair partial-start recovery in commit `94e2517`, and pass authenticated local checks for both prepared accounts.


## PWA rating notifications completed

- [x] Restore service-worker registration to the tracker and settings shells and verify the installable manifest, icons, offline fallback, and active scope.
- [x] Add explicit per-device Web Push opt-in for Windows and Android-capable browsers.
- [x] Notify only for newly synced Completed titles that remain unrated, with title, cover art where supported, and a direct rating deep link.
- [x] Add one reusable 0.5–10 quick-rate interaction to owner lists, title pages, in-app Notifications, and native notification deep links.
- [x] Persist VAPID identity and user-scoped subscriptions, remove expired endpoints, and cover sync gating and delivery with PostgreSQL-backed regression tests.
