# AniList reference analysis

Completed public browser inspection of <https://anilist.co/user/antipixel/> and representative list/title flows on 2026-09-18. No account login or account changes were performed. Authenticated editing behavior is taken only from the user's supplied screenshots. These observations define the release-one UI baseline together with `UI-REFERENCE.md`; they do not add unrelated AniList features to scope.

## Profile and navigation

- The profile banner, avatar/name block, and centered profile tab row form one persistent identity shell. Overview and list pages retain that shell instead of opening a separate personal-list application.
- The public tabs observed were Overview, Anime List, Manga List, Favorites, Stats, Social, Reviews, and Submissions. Media Tracker adapts these to Overview, Movie List, Series List, Favorites, Social, and the owner-only Library; excluded AniList features stay excluded.
- The overview leads with compact aggregate/genre information and an image-led Activity section. Media Tracker uses movie/series totals and the self/followed activity feed, while private sync review remains a separate Notifications surface.
- Clicking a list item title is ordinary navigation to the detail page. Owner-only controls are additional affordances and do not change the page seen by visitors.

## List page

- A left rail contains a text filter, status groups/counts, Format, release Status, Genres & Tags, Country, year, and Sort. The content column places the view toggle at the upper right and renders statuses as vertically stacked groups.
- Selecting Watching immediately reduced the visible groups to Watching without a full navigation. Media Tracker should preserve list state during filtering and use the same immediate feedback.
- The compact table gives the title most horizontal space; Score, Progress, and Type remain visually secondary and consistently aligned. Covers are small anchors rather than dominant cards.
- Hovering a cover expands its artwork. The live AniList layer intercepted a later sidebar click until dismissed; Media Tracker deliberately uses a pointer-transparent preview so neighboring controls remain usable.
- The authenticated screenshot establishes the ellipsis action revealed over the cover and its quick editor. The user's screenshots also establish the modal fields and title-link behavior. These actions are owner-only, keyboard reachable, visible on touch without hover, dismissible with Escape/outside click, and subject to reduced-motion preferences in Media Tracker.
- The later Attack on Titan editor screenshot adds stronger evidence for the editor's banner/cover overlap, field alignment, compact Save action, and separated Delete action. Favorite, custom lists, and per-entry privacy remain excluded by the written product scope even though they appear in the reference.
- Default compact view plus grid toggle remains the accepted behavior. Filtering must continue to work in both modes; phones collapse the rail into compact filter controls rather than preserving a narrow desktop sidebar.

## Title details

- Desktop composition uses a full-width backdrop, an overlapping portrait cover and status/favorite controls at left, and the title/description in the main column. A short centered tab row separates the hero from detail content.
- The public title tabs observed were Overview, Characters, Staff, Stats, and Social. Media Tracker keeps Overview, Seasons for series, and Social because cast/staff/stat pages are outside release one.
- AniList keeps dense facts in the left column and uses sections for relations, characters, staff, distributions, trailer, following, tags, and external links. Media Tracker adapts the hierarchy to catalog facts, four distinct score contexts, synopsis/genres, followed-user scores, external IDs, and image-led seasons.
- The authenticated screenshot establishes the split primary action: the main button shows the current status and the arrow exposes Set as Completed, Set as Watching, Set as Planning, and Open List Editor. Media Tracker retains its additional Paused/Dropped states in the full editor.
- On a phone, AniList orders the banner, cover with primary actions, title, horizontally scrollable tabs, facts, description, and subsequent sections in one column. Media Tracker now follows that order while keeping ratings, social context, and season controls readable without horizontal page overflow.

## Motion, input, and accessibility adaptation

- AniList relies on quick color/background transitions, cover enlargement, modal overlays, dropdowns, and responsive reflow. Media Tracker uses short transitions for equivalent state feedback and removes them under `prefers-reduced-motion`.
- Public inspection did not provide authoritative evidence for AniList's full keyboard model. Media Tracker therefore uses native links/buttons/inputs, visible focus behavior, ARIA-expanded/menu relationships where needed, Escape/outside-click dismissal, and touch-sized controls rather than copying inaccessible behavior.
- Navigation should preserve a stable shell and predictable placement while every route remains directly addressable and server rendered. ADR-018 records the shared app bar plus purpose-built Astro components as the implementation foundation.

## Evidence

Reference captures in `references/`:

- `anilist-profile-desktop-2026.png`
- `anilist-list-desktop-2026.png`
- `anilist-list-hover-2026.png`
- `anilist-title-desktop-2026.png`
- `anilist-title-mobile-2026.png`
- `anilist-profile-list-filters.png` (user supplied)
- `anilist-list-editor-reference-2026.png` (user supplied)
- `media-tracker-recent-events-feedback-2026.png` (user-supplied capture of the current design problem)

Earlier `anilist-live-*.png` captures include the public consent/hover states and are retained as supporting evidence. The user's three Bleach screenshots remain the authoritative release-one reference for quick editing and detail status actions.

## Release-one adaptation checklist

| AniList behavior | Media Tracker adaptation | State |
| --- | --- | --- |
| Shared profile/list identity shell | Same public and owner list route with owner-only actions | Implemented |
| Vertical status groups and left filters | Movie/series groups including Paused and Planning | Implemented |
| Compact list, cover hover, quick editor | Pointer-transparent preview, cover ellipsis, touch fallback | Implemented |
| Banner/cover detail composition | Whole-show details with catalog facts and seasons | Implemented |
| Status split action | Completed/Watching/Planning shortcuts plus full editor | Implemented |
| Activity presentation | Image-led self/followed activity cards | Implemented |
| Responsive detail order | Cover/action, title, tabs, facts, scores/content | Implemented; compiled after final refinement |
| Characters/staff/reviews/comments | Outside release-one scope | Intentionally omitted |

The final title-tab/mobile-order refinement passed the production frontend build. A fresh browser screenshot could not be captured after that small CSS-only refinement because the browser approval quota was exhausted; the immediately preceding mobile build had no horizontal overflow, and browser verification remains part of the final release audit.


