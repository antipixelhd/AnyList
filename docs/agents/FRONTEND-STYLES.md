# Frontend styles reference

Read only for frontend appearance or layout changes.

## Structure

The frontend uses Astro. Pages live in frontend/src/pages/; shared styles live in
frontend/src/styles/. frontend/package.json defines development and build commands.

Connections composes tracking-service views from frontend/src/components/settings/.
Trakt, Simkl, MDBList, and Bingebase each have a focused component. Device sign-in
and disconnect handlers live in lib/tracking-service-auth.ts; polling and request
cancellation live in lib/device-authorization.ts and have focused node:test coverage.

Data-import views use DataImportSettings and DataImportDialogs. lib/data-imports.ts
owns supported formats and multipart uploads; lib/data-import-controls.ts owns
selection dialogs, file input wiring, and navigation cleanup.

## Shared sources

- typography.css: font families, semantic size/weight tokens, line height, and
  the 62.5% root size (normally 10px). Account for this when using rem.
- global-theme.css: shared colors, surfaces, borders, and text colors.
- global.css: Tailwind and shared theme/utility/component imports.
- tailwind-rem-scale.css: utility scaling for the project's root size.
- layout-width.css: shared 1320px usable content width, 32px desktop gutters,
  and 40px column gap. Settings keeps its narrower page cap.
- tracker-profile-list.css: shared profile/list alignment and content gaps
  (32px desktop, 24px at widths up to 650px).
- app-bar.css and tracker-navigation.css: shared navigation styling.
- Feature styles such as tracker-details.css, tracker-settings.css, and
  tracker-rating.css: inspect the file used by the affected page.

## Editing rules

Reuse existing semantic typography, palette, and spacing variables before adding
new ones. Keep feature rules in their focused stylesheet. Inspect imports and
selectors before adding overrides; preserve shared profile alignment.

Check the affected page at desktop and phone widths when changing layout.
Use UI screenshots or historical UI-REFERENCE.md only when the task needs that
specific reference. Avoid loading old phase screenshots as routine preparation.
