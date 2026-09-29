# AnyList frontend

Run commands from this directory. Install dependencies with `npm ci`.

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the development server on port 7330 |
| `npm run check` | Check Astro and TypeScript sources |
| `npm test` | Run the node:test regressions |
| `npm run build` | Build the standalone server into dist/ |
| `npm run preview` | Run the production build locally |

The Windows launcher in the repository root starts the frontend on port 7340,
its backend on 7341, and the local database. See scripts/local.ps1.

- src/pages/: routes and server-rendered page composition.
- src/components/: shared views; settings/ contains provider and import views.
- src/layouts/: the app shell and shared settings navigation.
- src/lib/: API types, shared presentation helpers, and browser controllers.
- src/scripts/: feature entry points such as Netflix import.
- src/styles/: shared tokens and feature stylesheets.
- tests/: browser-independent regression tests.

Browser controllers must clean up requests and listeners on client navigation.
Use neighbouring controllers and tests as the guide for page lifetimes.
