#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
start_database
# Integration tests can reset tables: always use the separate test role/database.
export DATABASE_URL="$CLOUD_TEST_DATABASE_URL" TRACKING_TEST_DATABASE_URL="$CLOUD_TEST_DATABASE_URL"
export TMDB_API_KEY='' TVDB_API_KEY='' TVDB_SUBSCRIBER_PIN=''
export UV_PYTHON=3.13 UV_PROJECT_ENVIRONMENT="$ROOT/backend/.venv"
(cd "$ROOT/backend" && uv run --no-sync alembic upgrade heads && uv run --no-sync python -m unittest discover -s tests -q && uv tool run ruff==0.16.8 check .)
python3 -m unittest discover -s "$ROOT/ops/preview/tests" -q
python3 -m unittest discover -s "$ROOT/ops/cloud/tests" -q
npm --prefix "$ROOT/frontend" run check
npm --prefix "$ROOT/frontend" run build
npm --prefix "$ROOT/frontend" test
