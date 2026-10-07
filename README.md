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

### Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 22.12 or newer, with npm
- Windows PowerShell and Docker Desktop running with Linux containers

The local launcher runs PostgreSQL 16 in Docker and the backend and frontend
from source, with automatic reload.

### Getting started

1. Clone the repository and install dependencies from its root in PowerShell:

   ```powershell
   git clone https://github.com/antipixelhd/AnyList.git
   cd AnyList
   $env:UV_PROJECT_ENVIRONMENT = Join-Path $PWD '.venv'
   uv sync --project backend --python 3.13 --frozen --group dev
   npm --prefix frontend ci
   ```

2. Start the app:

   ```powershell
   .\Run Local.ps1
   ```

Open [localhost:7340](http://localhost:7340). The launcher configures the local
database, applies migrations, and starts the API on port 7341. No `.env` file is
needed. Create your first account at `/register`; it becomes an administrator.
Sign in with your email address.

To search titles and load metadata, add a TMDB **API Read Access Token** in
Settings. A TheTVDB key is optional for additional TV metadata. Neither is
needed to start the app.

Stop the web servers with `.\Run Local.ps1 -Stop`. Database data is retained.
Logs are in `.venv/backend.error.log` and `.venv/frontend.error.log`.

For Linux Codex Cloud setup and checks, see [ops/cloud/README.md](ops/cloud/README.md).
For frontend commands, see [frontend/README.md](frontend/README.md).
