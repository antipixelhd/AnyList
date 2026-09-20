# Stage-two research notes

2026-09-20. Findings and recommendations; unresolved choices are not accepted requirements.

## Repository and licensing

The local LICENSE.md and upstream https://github.com/ellite/scrob identify GPLv3. The application remains a derivative of Scrob even if published under its own name in a standalone GitHub repository. GPL does not require GitHub's fork relationship. Preserve attribution, copyright/license notices, identify modifications, and provide corresponding source under GPL when distributing the covered work. Preserve upstream Git history as the preferred provenance record.

Private modification and server-side operation alone do not generally require public release under GPL (as distinct from AGPL). Browser-delivered GPL JavaScript is distributed to users and has source obligations; do not interpret server hosting as a blanket exemption. A public repository is a straightforward publication choice, not something GPL categorically requires for private work. Sources: https://www.gnu.org/licenses/gpl-faq.en.html#UnreleasedMods and https://www.gnu.org/licenses/gpl.en.html . GitHub supports independent duplication: https://docs.github.com/en/repositories/creating-and-managing-repositories/duplicating-a-repository .

Recommendation: independently named repository retaining upstream history, GPLv3 and clear Scrob credit, with an upstream remote for reference. Visibility and final name remain user decisions. Current origin points to the preserved local reference repository; do not push there.

Initial frontend lockfile inventory includes MIT, Apache, ISC, BSD, LGPL, MPL, CC-BY and other declarations. This is not a full dependency-license audit or proof of every distribution obligation. Complete backend/transitive dependency, bundled-asset and attribution review before publication. Font Awesome distinguishes icon/content and code licenses: https://fontawesome.com/license/free . Provider artwork is not made GPL by placing application code under GPL. Review tracked files and history for deployment secrets/personal data before pushing, and bring canonical project documents into the repository with corrected links and no competing copies.

## Components and motion

Current stack: Astro 6, Tailwind 4, purpose-built Astro components and browser scripts; no React/Vue runtime. Context7 documentation lookup confirms Tailwind v4 transition utilities, starting-style/discrete transitions, and motion-safe/motion-reduce variants. Some first-query results were historical blog examples; a focused third query retrieved current v4 guidance. Sources: https://tailwindcss.com/docs/transition-property and https://tailwindcss.com/docs/transition-behavior .

Tailwind supplies styling primitives, not the complete focus/keyboard/state behavior of complex controls. Headless UI supplies components for React/Vue (https://headlessui.com/); adopting it would add framework integration. Basecoat provides a Tailwind-oriented HTML/component approach without requiring React (https://basecoatui.com/) and is a candidate for a representative dropdown/dialog/filter comparison, not an accepted dependency. Check behavior, license and theme fit before selection. Prefer one consistent set of reusable controls over adding several libraries. Proposed motion: short opacity/translation transitions for menus/dialogs/topbar, immediate-feeling feedback, no layout jumps, reduced-motion support. Exact AniList timing has not been measured.

## Search

Current Browse requires a submitted form. Catalog search uses substring matching on locally stored title, limit 80, followed by TMDB's first search page. Personal-list search is lowercase substring matching. The new endpoint does not search TVDB or locally stored alternate titles. A local match missed by substring may appear as an external candidate before opening resolves its existing identity.

Proposed: shared live search with debounce and stale-request cancellation; fuzzy title matching on the loaded list/local catalog; on-demand external candidate retrieval and bounded fallback queries; deduplication by identity. Ranking cannot recover a remote title the provider never returned. Keep fuzzy search suggestions separate from authoritative provider-sync identity matching. Product decisions: global Movies/Series scope, friend search, direct add/edit actions, and filter state.

## Artwork

TMDB movie/TV image endpoints expose poster and backdrop candidates with language metadata. include_image_language can include null (no language tag). A null tag is useful for selecting likely textless art, not proof that the image contains no lettering, and coverage is variable. Sources: https://developer.themoviedb.org/docs/image-languages and https://developer.themoviedb.org/reference/movie-images . Context7 also returned the TV series images endpoint.

Current code stores one selected poster/backdrop; TMDB details do not request image candidates. TVDB artwork currently takes raw.image and a first-artwork backdrop, without textless/type-language selection. Proposed: prefer suitable textless portrait candidates when present, cache the selected image, fall back to normal posters. Do not generate or erase lettering. Keep square cropping limited to compact-row thumbnails; preserve the full portrait for preview/grid/detail. Whether textless preference affects all poster surfaces remains open.

## Names

Update after round two: user selected AnyList. https://www.anylist.com/ confirms an existing consumer list/recipe/meal-planning app under that exact name. This is a concrete brand/search collision, not a trademark-infringement determination. Recommend reconsideration before public naming and logo work; do not silently override the user's selection.

Media Tracker is descriptive but generic. Favor names that cover all media, avoiding movie-only words such as reel/cine/watch as the central concept.

Creative shortlist: Lorelist (lore + list), Talejar (a collection of stories), Talevy (a compact story-oriented coined name), Folist (folio + list). These are suggestions, not cleared trademarks or available domains/handles. Initial search found LoreList.com listed for sale and unrelated Talevy usage. No absence-of-results claim establishes availability.

Avoid obvious existing collisions: Shelfio already names a book app and company; Medialog is used by a media-tracking plugin and other products; Omnilist already covers games/films/shows. Sources: https://www.shelfio.com/en , https://community.obsidian.md/plugins/medialog , https://www.omnilist.net/ . Perform focused repository/domain/name checks once the user chooses a shortlist.
