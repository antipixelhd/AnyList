#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
# Codex universal's runtime selector runs before dependency synchronization.
export CODEX_ENV_PYTHON_VERSION=3.13 CODEX_ENV_NODE_VERSION=22
if [[ -f /opt/codex/setup_universal.sh ]]; then
  source /opt/codex/setup_universal.sh
fi
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<22||(a===22&&b<12))process.exit(1)'
if [[ ! -x "$PG_BIN/initdb" ]]; then
  as_root apt-get update -qq
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16
fi
if ! command -v uv >/dev/null; then
  echo 'The Codex universal image must provide uv' >&2
  exit 1
fi
if [[ ! -f "$STATE/runtime.env" ]]; then
  # A dedicated cluster lives outside the checkout and cannot touch preview data.
  cluster_id=$(printf '%s' "$ROOT" | sha256sum | cut -c1-16)
  pgdata="/var/lib/anylist-cloud/$cluster_id"
  as_root install -d -o postgres -g postgres -m 700 "$pgdata"
  [[ ! -f "$pgdata/PG_VERSION" ]] || { echo 'Unexpected orphan Cloud cluster; refusing to replace it' >&2; exit 1; }
  as_postgres "$PG_BIN/initdb" -D "$pgdata" --auth-local=peer --auth-host=scram-sha-256 >/dev/null
  printf "listen_addresses='127.0.0.1'\nport=5440\nunix_socket_directories='%s'\n" "$pgdata" | as_root tee -a "$pgdata/postgresql.conf" >/dev/null
  export ANYLIST_CLOUD_PGDATA="$pgdata" ANYLIST_CLOUD_RUNTIME_FILE="$STATE/runtime.env"
  python3 - <<'PY'
import os, secrets, shlex
from pathlib import Path
dev, test = secrets.token_hex(24), secrets.token_hex(24)
values = {
    'CLOUD_PGDATA': os.environ['ANYLIST_CLOUD_PGDATA'],
    'CLOUD_DEV_PASSWORD': dev, 'CLOUD_TEST_PASSWORD': test,
    'DATABASE_URL': f'postgresql+asyncpg://anylist_cloud:{dev}@127.0.0.1:5440/anylist_cloud',
    'CLOUD_TEST_DATABASE_URL': f'postgresql+asyncpg://anylist_cloud_test:{test}@127.0.0.1:5440/anylist_cloud_test',
    'SECRET_KEY': secrets.token_hex(32), 'SERVER_URL': 'http://localhost:7330',
    'BACKEND_PORT': '7331', 'ENABLE_REGISTRATIONS': 'true',
    'DATA_DIR': str(Path(os.environ['ANYLIST_CLOUD_RUNTIME_FILE']).parent / 'data'),
}
Path(os.environ['ANYLIST_CLOUD_RUNTIME_FILE']).write_text(''.join(f'{k}={shlex.quote(v)}\n' for k, v in values.items()))
PY
fi
start_database
export UV_PYTHON=3.13 UV_PROJECT_ENVIRONMENT="$ROOT/backend/.venv"
uv sync --project "$ROOT/backend" --frozen --group dev
uv tool install ruff==0.16.8
npm --prefix "$ROOT/frontend" ci
(cd "$ROOT/backend" && uv run --no-sync alembic upgrade heads)
echo 'Cloud dependencies and isolated development database ready.'
