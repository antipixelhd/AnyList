# AnyList

A self-hosted movie and TV tracker for personal lists and shared discoveries.

> This modified distribution retains substantial code and provider adapters from [Scrob](https://github.com/ellite/scrob). AnyList is licensed under the [GNU GPL v3](LICENSE.md); see [NOTICE.md](NOTICE.md) for attribution details.

## Features

- Track movies and whole TV series as Planning, Watching, Paused, Dropped, or Completed. Record episode progress, watch history, dates, notes, and rewatches.
- Rate titles from 0.5 to 10 in half-point steps. Rate seasons individually and choose a calculated show average or a separate show score.
- Browse and search a shared catalog with title, season, and episode details. Metadata comes from TMDB and TheTVDB; MDBList can provide IMDb and Rotten Tomatoes scores.
- Keep Favorites and a streaming Library alongside tracked lists. Library membership is separate from watch status and can mirror across Stremio and Nuvio connections.
- Keep Nuvio TV's Next Up aligned with your Watching list while preserving watched episodes for paused and dropped shows.
- Share public or private profiles with lists, favorites, statistics, and activity. Follow people to compare lists and see their activity.
- See series progress and upcoming season dates, with a notification inbox for new releases and connection changes that need review.
- Connect media services and trackers including Stremio, Nuvio, ARVIO, Plex, Emby, Jellyfin, Kodi, Trakt, Simkl, MDBList, and Bingebase. Import Netflix viewing-history CSVs or exports from AnyList/Scrob, Trakt, and Yamtrack/Floppy. Available sync fields depend on the connected service.

## Development

Private VPS source previews use GitHub as their control plane: **normal development
does not require VPS SSH**. See [preview setup and operations](ops/preview/README.md)
for beta, parallel development branches, GitHub controls, and the Stage 2 runbook.

Backend dependencies live in `backend/pyproject.toml` and `backend/uv.lock`;
frontend dependencies live in `frontend/package.json` and `frontend/package-lock.json`.
CI uses Python 3.13 and Node 22; the frontend requires Node 22.12 or newer.

For Windows development, install Python, Node, uv, and Docker Desktop. Run this
from the repository root in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $PWD '.venv'
uv sync --project backend --frozen --group dev
npm --prefix frontend ci
.\Run Local.ps1
```

The launcher expects the root `.venv`; uv's [project environment setting](https://docs.astral.sh/uv/concepts/projects/config/#project-environment-path)
keeps installation aligned with it. The launcher starts the database, applies
migrations, and serves the frontend on 7340 and backend on 7341. Local preview
login details, when available, are in `.venv/LOCAL-LOGIN.txt`. Stop the web
servers with `.\Run Local.ps1 -Stop`; saved database data is retained.
On an empty database, create the first account at `/register`; it becomes an
administrator.

Optional local preview tools run with the root virtual environment:

| Script | Purpose |
| --- | --- |
| `scripts/seed_local_preview.py` | Create the synthetic `preview` account and example lists in the local database |
| `scripts/prepare_local_login.py` | Prepare login details for existing preview accounts; also run by the launcher |
| `scripts/verify_local_preview.py` | Check available preview logins and read-only APIs; write `.venv/<username>-browser.json` for browser QA |

After seeding, run login preparation before verification. Browser state contains
session credentials and stays in the ignored `.venv` directory.

Run checks from the indicated directory. Keep `UV_PROJECT_ENVIRONMENT` set to
the absolute root `.venv` path when using uv commands.

| Directory | Command |
| --- | --- |
| `backend/` | `uv run --no-sync python -m unittest discover -s tests -q` |
| `backend/` | `uvx ruff==0.16.8 check .` |
| `frontend/` | `npm test` |
| `frontend/` | `npm run check` |
| `frontend/` | `npm run build` |

Backend integration tests require a disposable PostgreSQL database. Set
`SECRET_KEY`, `DATABASE_URL`, and `TRACKING_TEST_DATABASE_URL`, then initialize
that database with `uv run --no-sync alembic upgrade head`. Database-dependent
tests skip when their fixture URL is absent. [CI](.github/workflows/ci.yml)
shows the complete isolated test setup.

Start with [backend behavior and module locations](docs/agents/BACKEND.md),
[frontend structure and commands](frontend/README.md), or
[frontend styling conventions](docs/agents/FRONTEND-STYLES.md) for the affected area.
