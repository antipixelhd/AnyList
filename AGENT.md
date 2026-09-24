# AnyList visual implementation guide

Read this alongside [AGENTS.md](AGENTS.md) and the canonical [project documents](docs/media-tracker/IMPLEMENTATION.md).

## Typography

[typography.css](frontend/src/styles/typography.css) is the source of truth for font families, semantic sizes, weights, and reusable `.type-*` classes. Use its `--type-<category>-size` and `--type-<category>-weight` variables in existing component selectors, or apply the corresponding class. Do not add independent text size/weight overrides for an existing category.

Navigation uses `Overpass, -apple-system, BlinkMacSystemFont, "Segoe UI", Oxygen, Ubuntu, Cantarell, "Fira Sans", "Droid Sans", "Helvetica Neue", sans-serif`. Everything else uses `Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI", Oxygen, Ubuntu, Cantarell, "Fira Sans", "Droid Sans", "Helvetica Neue", sans-serif`.

Navigation means the app menus, profile tabs, settings navigation, and section selectors. Content links, status filters, form fields, and action buttons remain body typography. Icons retain their own geometric sizes.

| Category | Default size | Weight | Usage |
| --- | --- | --- | --- |
| nav | 1.4rem | 600 | Primary navigation and profile tabs |
| sidebar | 1.4rem | 400 | Sidebar navigation; selected items use 500 |
| page | 2.4rem | 400 | Page heading |
| profile | 1.9rem | 700 | Profile name |
| section | 1.9rem | 400 | List section heading |
| panel | 1.6rem | 500 | Panel/dialog heading |
| body | 1.6rem | 400 | Body prose |
| row / table-heading | 1.5rem | 400 / 500 | Standard list data and headings |
| compact-row / compact-heading | 1.3rem | 400 / 500 | Compact list data and headings |
| card | 1.4rem | 500 | Card title |
| control | 1.4rem | 400 | Form controls and filter options |
| label / action | 1.3rem | 400 / 500 | Labels and compact actions |
| meta | 1.2rem | 400 | Supporting text |
| caption / badge | 1.1rem | 400 / 500 | Timestamp and small badge |
| stat | 2.4rem | 700 | Statistic value |

The Statistics page uses dedicated categories for its 3rem/700 heading, 1.2rem/600 highlight labels, and 2.9rem/700 values (2.3rem on phones). Chart labels use the 1.2rem supporting-text category. Keep these in the registry so the page's compact grid remains aligned.

Additional display, avatar, and prose-heading categories retain the hierarchy needed by authentication artwork and user-authored biographies. Define justified new categories in the central registry and document their purpose; do not create a token for every historical pixel value.

This pass intentionally changes typography only. Categories do not impose colors. Preserve existing component colors, hover/focus colors, light/dark themes, and user-selected profile accents.

## Root units and stylesheet ownership

All profile pages use `--profile-content-gap` between the profile tabs and the first content container: 32px on desktop and 24px on phones. Keep route-specific top padding out of the profile content wrapper so this distance remains consistent.

The root is `62.5%`: at the browser's default 16px setting, 1rem is 10px. Body text defaults to 1.6rem/400. Respect user font-size preferences; do not replace the percentage with a fixed pixel root.

For a pre-migration non-text length expressed in rem against a 16px root, multiply its rem value by 1.6 to retain the same geometry. Do not rescale pixels, unitless line heights, percentages, viewport units, or media-query breakpoints. Utility theme dimensions need the same compensation. New text sizes use semantic tokens.

`tracker.css` and `global.css` are ordered entrypoints. Keep their imports ordered: foundations and themes first, shared controls/shell next, feature modules after their dependencies. Preserve cascade behavior when moving rules. Keep responsive and reduced-motion rules with the feature they affect. Use readable CSS, and consolidate an obsolete declaration at its owning rule instead of appending another override.

Existing focused stylesheets own their named component or feature. Extend those files instead of recreating their rules in the tracker entrypoint. Page-local styles are only for genuinely local composition; shared typography belongs in the registry.

## Verification

Build the frontend and inspect desktop, tablet, and phone layouts after typography changes. Check actual downloaded fonts, computed family/size/weight, initial and client-side navigation, long-title wrapping, dialogs, chart labels, keyboard focus, 200% zoom, and horizontal overflow. Compare layout dimensions and existing colors when changing root units. Do not mutate account data merely to test visual changes.
