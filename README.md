# AnyList

AnyList is a private, self-hosted movie and television tracker for an administrator and invited friends. It combines AniList-inspired lists and profiles with bidirectional synchronization built on the Scrob codebase.

The current release focuses on movies and whole television shows. It provides half-point ratings, optional per-season ratings, episode progress, favorites, one-way following, private or public profiles, activity, and a notification inbox for provider changes that need review.

## Release-one behavior

- Movie and series lists use Planning, Watching, Paused, Dropped, and Completed groups.
- Ratings accept `0.5` through `10` in half-point increments. Unrated values are stored separately and never displayed as zero.
- Series remain one list entry. A user can keep a manual show rating or calculate it from explicitly rated regular seasons.
- Profiles expose lists, favorites, totals, following, followers, and activity according to the profile privacy setting.
- Streaming-library membership remains separate from tracked-list membership.
- Stremio, Nuvio, and inherited tracking connections use reconciliation baselines, conflict review, deletion markers, and outbound gates rather than treating every remote snapshot as authoritative.
- TMDB and TheTVDB provide the central catalog. MDBList can supply distinct IMDb and Rotten Tomatoes ratings.
- Invite-only Google OpenID Connect is supported when an administrator configures a client; unknown identities are not auto-created.

The detailed product contract, accepted edge cases, test scenarios, and current handoff live in [`../docs/media-tracker`](../docs/media-tracker/PLAN.md):

- [Product plan](../docs/media-tracker/PLAN.md)
- [Implementation and acceptance scenarios](../docs/media-tracker/IMPLEMENTATION.md)
- [Current verified status](../docs/media-tracker/STATUS.md)
- [Remaining external verification gates](../docs/media-tracker/FOLLOW-UP.md)
- [AniList interface analysis](../docs/media-tracker/ANILIST-ANALYSIS.md)

## Local development

The supported Windows launcher starts the local PostgreSQL container, applies migrations, prepares two preview accounts, and starts the FastAPI and Astro development servers:

```powershell
cd C:\Users\joshu\Documents\4_Stremio-SelfHost\media-tracker
.\scripts\local.ps1
```

Open `http://localhost:7340`. Generated preview credentials are written to the ignored file `.venv\LOCAL-LOGIN.txt`.

Stop the two web processes while retaining the database:

```powershell
.\scripts\local.ps1 -Stop
```

The local ports are:

| Service | Address |
| --- | --- |
| Astro frontend | `http://localhost:7340` |
| FastAPI backend | `http://127.0.0.1:7341` |
| PostgreSQL | `127.0.0.1:55438` |

## Verification

Run backend tests against the dedicated local database:

```powershell
$env:SECRET_KEY = 'test-secret'
$env:DATABASE_URL = 'postgresql+asyncpg://media_tracker:local-development-only@127.0.0.1:55438/media_tracker_test'
$env:TRACKING_TEST_DATABASE_URL = $env:DATABASE_URL
Push-Location backend
try { & ..\.venv\Scripts\python.exe -m unittest discover -s tests -q } finally { Pop-Location }
```

Build the production frontend:

```powershell
cd frontend
npm run build
```

With the local preview running, verify both prepared accounts and the main tracking routes:

```powershell
.\.venv\Scripts\python.exe scripts\verify_local_preview.py
```

## Isolated deployment

`compose.test.yaml` builds this working copy and binds it to a configurable test-only address and port. Copy `.env.example` to an ignored `.env.test`, set a strong database password and `SECRET_KEY`, and keep registrations disabled. Provider write directions must remain off until the first reconciliation has been reviewed with disposable test accounts.

The production-style container applies Alembic migrations at startup and serves the Astro frontend and FastAPI backend on one port. Put an HTTPS reverse proxy in front before testing Google sign-in or exposing an instance beyond a private network.

## Source and license

AnyList is derived from [Scrob](https://github.com/ellite/scrob). The upstream architecture and provider adapters remain a substantial part of this repository; protocol names, migration-compatible identifiers, and existing deployment identifiers intentionally retain some `scrob` or `media-tracker` terminology.

This project is licensed under the [GNU General Public License v3.0](LICENSE.md). Third-party names and trademarks identify interoperable services only and do not imply affiliation or endorsement.
