#!/usr/bin/env bash
# Run as an unprivileged user inside an isolated network namespace on Linux.
set -euo pipefail
umask 077
cd "$(dirname "$0")/../.."
root=$PWD
if [[ $(id -u) == 0 || $(readlink /proc/self/ns/net) == $(readlink /proc/1/ns/net) ||
      "$root" != /tmp/anylist-stress-* ]]; then
    echo 'Expected an unprivileged private network namespace and a fresh /tmp/anylist-stress-* directory.' >&2
    exit 1
fi
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy PREVIEW_SLOT
export DATABASE_URL=postgresql+asyncpg://anylist_stress@127.0.0.1:57639/anylist_test_stress
export SECRET_KEY=$("$root/runtime/bin/python" -c 'import secrets; print(secrets.token_hex(32))')
export APP_VERSION=stress ANYLIST_STRESS_NAMESPACE=private
export TMDB_API_KEY=synthetic IGDB_CLIENT_ID=synthetic IGDB_CLIENT_SECRET=synthetic
export TZ=UTC NODE_ENV=production HOST=127.0.0.1 BACKEND_PORT=57640 PORT=57641
export SERVER_URL=http://127.0.0.1:57641 XDG_DATA_HOME="$root/state/data" XDG_CACHE_HOME="$root/state/cache"
export DATA_DIR="$root/state/data"
export PYTHONPATH="$root/backend" PGHOST=127.0.0.1 PGPORT=57639 PGUSER=anylist_stress
pg=/usr/lib/postgresql/16/bin
python="$root/runtime/bin/python"
state="$root/state"
if [[ ${STRESS_REUSE:-0} == 1 ]]; then
    test -f "$state/fixture.json"
else
    mkdir "$state"
fi

shutdown() {
    for pid in ${load_pid:-} ${frontend_pid:-} ${app_pid:-} ${fixture_pid:-}; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    wait || true
    "$pg/pg_ctl" -D "$state/postgres" stop -m fast >/dev/null 2>&1 || true
}
trap shutdown EXIT
trap 'exit 143' TERM INT

if [[ ${STRESS_REUSE:-0} != 1 ]]; then
    "$pg/initdb" -D "$state/postgres" --locale=C.UTF-8 -U "$PGUSER" --auth=trust >"$state/init.log" 2>&1
fi
"$pg/pg_ctl" -D "$state/postgres" -l "$state/postgres.log" -w start \
    -o "-h 127.0.0.1 -p $PGPORT -k $state -c shared_buffers=128MB -c max_connections=100 -c effective_cache_size=4GB -c shared_preload_libraries=pg_stat_statements -c log_lock_waits=on -c track_io_timing=on"
if [[ ${STRESS_REUSE:-0} == 1 ]]; then
    "$python" "$root/scripts/stress/fixture.py" "$state/fixture.json" --refresh-tokens
else
    "$pg/createdb" anylist_test_stress
    (cd "$root/backend" && "$python" -m alembic upgrade head) >"$state/migrations.log" 2>&1
    "$pg/psql" -X -q -d anylist_test_stress -c 'CREATE SCHEMA stress_metrics; CREATE EXTENSION pg_stat_statements WITH SCHEMA stress_metrics;'
    "$python" "$root/scripts/stress/fixture.py" "$state/fixture.json" "${STRESS_ACCOUNTS:-400}"
fi
"$python" "$root/scripts/stress/server.py" jellyfin >"$state/jellyfin.log" 2>&1 &
fixture_pid=$!
"$python" "$root/scripts/stress/server.py" >"$state/backend.log" 2>&1 &
app_pid=$!
(cd "$root/frontend" && exec node scripts/serve.mjs) >"$state/frontend.log" 2>&1 &
frontend_pid=$!
printf '%s %s %s\n' "$app_pid" "$frontend_pid" "$fixture_pid" >"$state/pids"

ready=0
for attempt in $(seq 1 40); do
    if curl -fsS --max-time 1 "http://127.0.0.1:$BACKEND_PORT/health" >"$state/health.json" 2>/dev/null &&
       curl -fsS --max-time 1 "$SERVER_URL/login" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep .5
done
test "$ready" = 1
"$python" "$root/scripts/stress/load.py" "$state/fixture.json" "$state/unused.jsonl" --smoke
if [[ ${1:-} == '--smoke' ]]; then exit 0; fi
"$pg/psql" -X -q -d anylist_test_stress -c 'SELECT stress_metrics.pg_stat_statements_reset();' >/dev/null

"$python" "$root/scripts/stress/load.py" "$state/fixture.json" "$state/results.jsonl" "$@" &
load_pid=$!
printf '%s\n' "$load_pid" >"$state/load-pid"
wait "$load_pid"
"$pg/psql" -X --csv -d anylist_test_stress >"$state/sql.csv" <<'SQL'
SELECT calls, round(total_exec_time::numeric, 2) AS total_ms,
       round(mean_exec_time::numeric, 3) AS mean_ms, rows, shared_blks_hit,
       shared_blks_read, temp_blks_written, left(query, 1000) AS query
FROM stress_metrics.pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database WHERE datname=current_database())
  AND query !~* '^(INSERT INTO (users|media|tracked_entries|catalogue_entities|game_entries|user_profile_data)|CREATE|ALTER|ANALYZE)'
ORDER BY total_exec_time DESC LIMIT 30;
SQL
