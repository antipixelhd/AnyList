# AnyList mixed-load benchmark

The driver sends authenticated HTTP through a matching production Astro build,
its API proxy and the real API/read-worker code. PostgreSQL uses the repository
migrations. Normal catalogue, metadata, statistics and sync code runs. Use only a
disposable database in a private network namespace; never a deployment database.

## Run

Provision `backend/`, `scripts/stress/`, `runtime/` (Python dependencies), and a
matching production `frontend/` build/runtime in a new `/tmp/anylist-stress-*`
directory without `.env` files or provider credentials. Ubuntu PostgreSQL 16,
`pg_stat_statements`, systemd, curl and Node are required.

```sh
root=/tmp/anylist-stress-example
sudo systemd-run --unit=anylist-stress-example --uid="$(id -un)" \
  -p PrivateNetwork=yes -p CPUQuota=300% -p CPUWeight=60 -p IOWeight=60 \
  -p MemoryMax=5G -p LimitNOFILE=16384 -p RuntimeMaxSec=1500 \
  -E STRESS_ACCOUNTS=500 -E STRESS_LIBRARY_SIZE=200 -E STRESS_RANKINGS=1 \
  -E API_READ_WORKERS=2 -E CATALOGUE_WORKER_MODE=external \
  -E DATABASE_POOL_SIZE=10 -E DATABASE_POOL_OVERFLOW=10 \
  /bin/bash "$root/scripts/stress/run-isolated.sh" --split-catalogue \
  --users 80 160 500 --seconds 40 --think 3.5 --prime \
  --probe-rounds 24 --client-shards 16 --cold-fraction .04
```

Attach `monitor.py` from the host namespace, passing the exact unit name,
`$root/state/host.jsonl` and a host-local deployment health URL. It stops only
that benchmark after three failed/>1s probes or host available RAM below 2 GiB.
The operator needs permission to read the benchmark PIDs and stop its unit.

```sh
python3 "$root/scripts/stress/monitor.py" anylist-stress-example "$root/state/host.jsonl" "$PEER_HEALTH_URL"
python3 "$root/scripts/stress/summarize.py" "$root/state/results.jsonl" --host "$root/state/host.jsonl"
```

Use `--smoke` for authenticated route/full-sync calibration. Reuse with
`STRESS_REUSE=1` only after archiving the previous top-level output files;
`STRESS_MIGRATE=1` explicitly upgrades that disposable database. For a paired
comparison, stop the lab and restore the same saved database snapshot before
each arm. Collect results and remove the stopped lab afterward. Raw artifacts
and synthetic tokens belong under ignored `.cloud-runtime`, not Git.

## Workload and interpretation

Defaults: 400 accounts, 80 screen entries/account, 24 games/account, 6,000 titles
and 2,000 games. Library size accepts 1..500. A quarter of accounts have a
synthetic Jellyfin connection; full pulls read 48 films. The mix includes screen
and game SSR/details, searches, contributors, browse, lists, connections/status,
manual edits and manual syncs. Automatic mapper/enrichment/statistics jobs remain
active. Five-minute scheduled account pulls require a longer run to be covered.

`--prime` warms twelve shared keys. `--cold-fraction` varies keys, not a guaranteed
cache-miss rate. `STRESS_RANKINGS=1` populates IMDb ranking evidence on warm fixture
documents, preserving cold JSON-null stubs. Setup commits bounded batches before
starting any application workers. `--probe-rounds` records serial single-user
latencies first. `--client-shards 16` bounds generator connection-pool overhead;
use the same setting for both arms. Changing think time changes offered load.

TMDB/IGDB/IMDb/Wikidata responses are synthesized at the HTTP transport with
100ms delay. Jellyfin serves loopback HTTP with 60ms delay. This measures real
application/SQL/HTTP work, not live provider quotas, CDN images or browser paint.
`rps` counts attempts over scheduled duration; successful req/s includes drain
time. Percentiles include failures. Sync completions use the stage deadline.

The cgroup includes the driver, API/readers, catalogue, PostgreSQL, Astro and
synthetic upstream. Attribute CPU by the recorded per-PID counters, not a busy
core. Short-lived PostgreSQL processes may be undercounted. Inspect outside-host
CPU, quota throttling, pool/queue/lock telemetry and deployment probes together.
Effective non-secret settings are saved in `runtime-config.json`.

Reader recovery checks replacement, active sync preservation, committed edits,
viewer privacy and write rejection. Catalogue recovery checks one owner,
standby takeover, queued publication and warm database replay. Optional
`STRESS_PROFILE=1` captures process-CPU stacks/origins; leave it off for capacity
comparisons. Collect shutdown metrics after normal process exit.

## Runtime ownership

`main:app` remains the normal entry point. `application.create_app` builds
explicit primary/reader roles with one shared lifecycle and client cleanup.
Routes opt into supervised readers with `@reader` next to their API declaration;
new routes default to the primary. Router order and path constraints are
preserved. Writes, wrapped endpoints (including rate-limit decorators), and
requests subject to configured global limits retain one primary owner. Proxy
forwarding headers are replaced from the primary's ASGI context; reader listeners
trust only loopback proxy headers and must stay private.

`API_READ_WORKERS` defaults to 0. `CATALOGUE_WORKER_MODE` defaults to `embedded`;
external mode requires a separately supervised `python -m core.catalogue_worker`
with the same configuration. Its advisory-lock coordinator needs a direct or
session-pooled PostgreSQL connection. Budget pool connections per process plus
that coordinator. `SYNC_CONCURRENCY` defaults to 1; increasing it trades CPU
headroom against queue throughput and needs its own measurement.

## Focused measurements

- `scripts/benchmark_search.py`: existing result/order, SQL, payload and provider
  controls; see its case selectors and the search-results-performance skill.
- `scripts/benchmark_account_prepare.py`: account matching and payload cost.
- `scripts/benchmark_page_context.py`: full/light page context and legacy parity.
- `scripts/benchmark_publication.py`: identities, season receipts and cached sync.
- `scripts/benchmark_browse_index.py`: ranking plans/order and metadata write cost.

Use their explicit disposable test-URL environment variables and `--help`.
Microbenchmarks do not establish whole-system capacity. Apply repository migrations
before starting updated workers. Regression tests retain transaction cutoffs,
private viewer state, cancellation and migrated expression-index compatibility.

[Recorded results and verification limits](RESULTS.md)
