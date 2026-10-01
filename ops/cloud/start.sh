#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
start_database
export SERVER_URL="${CLOUD_SERVER_URL:-$SERVER_URL}"
if [[ -n ${CLOUD_SERVER_URL:-} ]]; then
  export CLOUD_PREVIEW_HOSTNAME
  CLOUD_PREVIEW_HOSTNAME=$(node -e 'console.log(new URL(process.env.CLOUD_SERVER_URL).hostname)')
fi
export UV_PYTHON=3.13 UV_PROJECT_ENVIRONMENT="$ROOT/backend/.venv"
export ASTRO_DEV_BACKGROUND=0 ANYLIST_CLOUD_RUNTIME=1
(cd "$ROOT/backend" && uv run --no-sync alembic upgrade heads)
# Restart explicitly after checkout changes; do not rely on hot reload alone.
bash "$ROOT/ops/cloud/stop.sh"
(cd "$ROOT/backend" && exec nohup "$ROOT/backend/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port 7331 >"$STATE/backend.log" 2>&1) &
echo $! > "$STATE/backend.pid"
(cd "$ROOT/frontend" && exec nohup node "$ROOT/frontend/node_modules/.bin/astro" dev --host 0.0.0.0 --port 7330 >"$STATE/frontend.log" 2>&1) &
echo $! > "$STATE/frontend.pid"
wait_http http://127.0.0.1:7331/health
wait_http http://127.0.0.1:7330/login
kill -0 "$(cat "$STATE/frontend.pid")" "$(cat "$STATE/backend.pid")"
echo 'AnyList ready: frontend :7330, backend loopback :7331.'
