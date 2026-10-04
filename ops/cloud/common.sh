#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
STATE="$ROOT/.cloud-runtime"
PG_BIN=/usr/lib/postgresql/16/bin
# Non-login setup/maintenance shells do not automatically activate image runtimes.
export PATH="$HOME/.local/bin:$HOME/.pyenv/bin:$HOME/.pyenv/shims:$PATH"
export PYENV_VERSION=3.13
if [[ -f "$HOME/.nvm/nvm.sh" ]]; then
  source "$HOME/.nvm/nvm.sh"
  nvm use 22 >/dev/null
fi
umask 077
mkdir -p "$STATE"

as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"; else sudo --preserve-env=CLOUD_DB_ROLE,CLOUD_DB_PASSWORD "$@"; fi
}
as_postgres() { as_root runuser -u postgres -- "$@"; }
load_runtime() {
  [[ -f "$STATE/runtime.env" ]] || { echo 'Run bash ops/cloud/setup.sh first' >&2; return 1; }
  set -a
  source "$STATE/runtime.env"
  set +a
}
start_database() {
  load_runtime
  # A published filesystem can retain a PID from another VM. Never trust PID
  # existence alone or signal a process that merely reused that number.
  if as_postgres test -f "$CLOUD_PGDATA/postmaster.pid"; then
    pg_pid=$(as_postgres head -n 1 "$CLOUD_PGDATA/postmaster.pid")
    if [[ ! $pg_pid =~ ^[0-9]+$ ]] || [[ $(as_root readlink "/proc/$pg_pid/exe" || true) != "$PG_BIN/postgres" ]] || ! as_root grep -Fzq -- "$CLOUD_PGDATA" "/proc/$pg_pid/cmdline"; then
      if as_postgres "$PG_BIN/pg_isready" -h "$CLOUD_PGDATA" -p 5440 >/dev/null 2>&1; then
        echo 'Unexpected Cloud database PID; refusing to replace a live database lock' >&2
        return 1
      fi
      as_postgres rm -- "$CLOUD_PGDATA/postmaster.pid"
    fi
  fi
  if ! as_postgres "$PG_BIN/pg_ctl" -D "$CLOUD_PGDATA" status >/dev/null 2>&1; then
    as_postgres "$PG_BIN/pg_ctl" -D "$CLOUD_PGDATA" -l "$CLOUD_PGDATA/server.log" -w start
  fi
  # Credentials are read from the environment, never SQL/command-line literals.
  for kind in dev test; do
    if [[ $kind == dev ]]; then
      export CLOUD_DB_ROLE=anylist_cloud CLOUD_DB_PASSWORD="${CLOUD_DEV_PASSWORD:?Missing development DB password}"
    else
      export CLOUD_DB_ROLE=anylist_cloud_test CLOUD_DB_PASSWORD="$CLOUD_TEST_PASSWORD"
    fi
    as_postgres psql -X -v ON_ERROR_STOP=1 -h "$CLOUD_PGDATA" -p 5440 postgres <<'SQL'
\getenv role CLOUD_DB_ROLE
\getenv password CLOUD_DB_PASSWORD
SELECT format('CREATE ROLE %I LOGIN', :'role') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'role', :'role') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'role') \gexec
SQL
  done
  unset CLOUD_DB_PASSWORD CLOUD_DB_ROLE
}
wait_http() {
  for _attempt in {1..90}; do
    if curl --fail --silent --output /dev/null "$1"; then return 0; fi
    sleep 1
  done
  echo "Service readiness failed: $1; inspect private .cloud-runtime logs" >&2
  return 1
}
