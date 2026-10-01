# Codex Cloud development

Use Python 3.13, Node 22 (at least 22.12), uv, npm, and PostgreSQL 16. The
locked backend and frontend manifests are authoritative. Cloud tasks run source
with FastAPI/Uvicorn and Astro; Docker is not needed inside the Cloud VM.

## Environment configuration

Create a private **AnyList** environment for `antipixelhd/AnyList`, initially
selecting `beta`, under Settings → Codex Cloud → Environments. Set access to
**Only me**. The repository scripts are the configuration; they do not themselves
create or publish an OpenAI environment.

| Field | Value |
| --- | --- |
| Python runtime | `3.13` |
| Node runtime | `22` |
| Install script | `bash ops/cloud/setup.sh` from the repository root |
| Start skill | Instructions in [start-skill.md](start-skill.md) |
| Validation | `bash ops/cloud/check.sh` |
| Internet | Package managers; metadata domains below when testing providers |

On the legacy environment interface use the same install command as the setup
script and `bash ops/cloud/start.sh` as the maintenance script. Start services
again when beginning a task if its interface does not run startup automatically.
Saving configuration and publishing the prepared filesystem are separate actions;
republish after installation changes and use a new task to test them.
See [OpenAI environment documentation](https://learn.chatgpt.com/docs/environments/cloud-environments).

The setup uses sudo/root only to install PostgreSQL and manage its dedicated
cluster. It creates private development and test roles/databases on loopback
port 5440, unrelated to local Windows or VPS databases. Generated database
passwords and JWT signing secret stay in ignored `.cloud-runtime/runtime.env`.
Published state may retain development data; start from an empty prepared DB and
never import personal/production accounts into the Cloud baseline.

`start.sh` migrates forward, restarts Uvicorn on loopback 7331 and Astro on 7330,
then checks both. Astro listens on all Cloud VM interfaces so the Cloud preview
can forward port 7330. VPS preview listeners remain loopback-only. Set optional
`CLOUD_SERVER_URL` to the actual HTTPS Cloud preview origin when available; startup
sets `SERVER_URL` and adds only that hostname to Astro's permitted hosts. Existing
development hosts are retained. Frontend requests go through its existing API
proxy to loopback FastAPI; no separate public backend URL is required.

`check.sh` always selects the separate disposable test database, suppresses real
provider keys, migrates it, runs backend unittest and Ruff, preview controller
tests, frontend type checks, build, and node tests. It never resets the development
database. `stop.sh` stops only recorded frontend/backend processes; data is retained.
Re-running setup synchronizes locked dependencies after manifest changes.

## Variables and credentials

No external credentials are required to install, build, or run tests. Setup
generates `DATABASE_URL`, `SECRET_KEY`, and the separate test URL itself. It sets
`SERVER_URL`, `BACKEND_PORT`, `DATA_DIR`, and registration for the isolated Cloud
app. Do not copy VPS database passwords, preview JWT secrets, Tailscale identity,
or deployment credentials into Cloud. GitHub access is supplied through Codex's
repository connection; pushing a preview branch triggers the existing deployment.

Metadata lookups optionally use:

| Name | Purpose | HTTPS destinations |
| --- | --- | --- |
| `TMDB_API_KEY` | TMDB **API Read Access Token** (Bearer) | `api.themoviedb.org` |
| `TVDB_API_KEY` | TheTVDB project/subscriber key | `api4.thetvdb.com` |
| `TVDB_SUBSCRIBER_PIN` | Only if that TVDB key needs a subscriber PIN | `api4.thetvdb.com` |

Prefer Cloud network secrets with these names and restricted destinations when
supported by your interface. Programs receive placeholders; verify both TMDB
Bearer authentication and TVDB's JSON login before publishing. Do not also define
a direct environment variable with the same name. A private direct variable is
the fallback if network substitution is unavailable; its raw value is then visible
to programs and the task. Allow `image.tmdb.org` and `artworks.thetvdb.com` for image
retrieval. Store personal values for this environment only. No provider values
belong in the install script, start skill, commits, logs, or screenshots.

Locally the backend reads ignored root `.env.providers`, then `.env`; process
variables override both. Account metadata keys override database global keys,
which override these server defaults. Clearing a database key therefore reveals
the server default rather than deleting it. The settings API reports availability
without returning server environment credentials. Account OAuth/integration tokens
remain account-specific and are not copied into server defaults.

## VPS persistence

The backend systemd template also reads optional root-only
`/etc/anylist-preview/providers.env` before the slot's environment file. Store the
same metadata names there, mode 0600 root:root. Database resets cannot remove it;
every configured preview receives the defaults, and a slot environment can override
them. The frontend does not receive this shared file. Install the reviewed updated
template and provision each existing slot to refresh its unit before restarting.
This is a one-time infrastructure update; subsequent key changes need only backend
restarts through GitHub manual actions. See [private installer](install-provider-defaults.py)
for the administrator-only transfer; it never prints values or changes databases.
