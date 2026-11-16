# AnyList mixed-load benchmark

The driver sends authenticated HTTP requests through a production Astro build and its API proxy. The real backend lifespan starts sync, metadata, credit, and automatic game workers. PostgreSQL starts from the repository's Alembic migrations. Use a disposable database and an isolated network namespace.

The default fixture creates 400 users, 6,000 screen titles, 2,000 games, 80 screen entries and 24 game entries per user. One quarter of the accounts have a synthetic Jellyfin connection. Each full pull reads 48 films over loopback. Some users also have a scheduled account pull. The automatic game runner has an initial queue of 260 games and can discover more through its normal scheduling paths. Reviewed game identities exercise IMDb credit publication alongside games with no matching public identity.

Set `STRESS_LIBRARY_SIZE=200` on the isolated unit to exercise ordinary larger lists (accepted range 1..500; default 80). Use the same size for both builds, and rebuild matching production frontend artifacts when measuring shared page initialization changes.

Each virtual user works through one journey at a time with a mean 3.5-second pause. The mix includes screen and game detail pages and their follow-up requests, title and contributor search, browse, list reads, connection and sync status reads, and manual tracking writes. About 23% of journey selections use varying keys. `--prime` first reads the twelve shared search and detail keys. Short levels admit a subset of connected users' full pulls during the level. Longer levels rotate the subset in three or fewer windows to preserve overlapping sync work. Use the recorded provider calls to verify cache misses. A varying key can become warm when a previous request or worker acquired it.

Use `--prime --cold-fraction .04` to exercise predominantly warm traffic. Put `--split-catalogue` before the load flags to move catalogue processing into a separately measured process. At the end of that run, a standby must take ownership after the first process stops, publish queued metadata, and serve it from PostgreSQL through the authenticated API.

The Jellyfin fixture serves HTTP on loopback with 60 ms of delay. TMDB, IGDB, Wikidata and IMDb responses are synthesized at the backend HTTP transport with 100 ms of delay. AnyList's cache, database leases, throttling and SQL paths still run. The benchmark covers SSR and API response time. Browser rendering, public CDN and artwork downloads, live provider latency/quotas, and other connection adapters require separate measurements.

## Run on an Ubuntu VPS

Place the tracked `backend` and `scripts/stress` trees in a new directory under `/tmp/anylist-stress-*`. Provision an independent Python environment using the repository backend dependencies as `runtime/`. Place the matching production frontend build and its runtime dependencies in `frontend/` alongside `scripts/serve.mjs`. Start from a directory with no `.env` or provider credentials. The launcher expects Node, `curl`, systemd and Ubuntu's PostgreSQL 16 binaries and `pg_stat_statements` extension.

```sh
root=/tmp/anylist-stress-example
sudo systemd-run --unit=anylist-stress-example --uid="$(id -un)" \
  -p PrivateNetwork=yes -p CPUQuota=300% -p CPUWeight=60 -p IOWeight=60 \
  -p MemoryMax=5G -p LimitNOFILE=16384 -p RuntimeMaxSec=1500 \
  /bin/bash "$root/scripts/stress/run-isolated.sh" \
  --users 4 16 32 64 96 144 220 360 --seconds 32 --think 3.5
```

Start `monitor.py` in the host network namespace when the unit creates `state/`. Pass the unit name, `$root/state/host.jsonl` and a host-local production health URL. The monitor checks that URL every five seconds, samples host and cgroup counters, and stops the benchmark unit if three consecutive probes fail or exceed one second, or if available host memory falls below 2 GiB. It needs access to read the benchmark processes and permission to stop its exact unit.

```sh
python3 "$root/scripts/stress/monitor.py" anylist-stress-example "$root/state/host.jsonl" "$PEER_HEALTH_URL"
python3 "$root/scripts/stress/summarize.py" "$root/state/results.jsonl" --host "$root/state/host.jsonl"
```

Run the launcher with `--smoke` for a route and sync validation without a timed load stage. It checks authenticated pages, searches, game metadata publication and a full 48-item sync. A completed run writes raw level results, loop/pool/provider/queue telemetry and aggregate PostgreSQL statement timings in `state/`. Keep these files and synthetic session tokens out of Git. Stop the dedicated service and remove its temporary data directory when measurements are collected.

Read `rps` as request attempts divided by the scheduled level duration. The summary also gives successful requests per second over that duration plus the time needed to finish outstanding requests. Latency percentiles include failed attempts. Sync completion counts cover jobs admitted by the current level and finished by its scheduled deadline.

`STRESS_REUSE=1` keeps a previous synthetic database and refreshes test session tokens. Archive the previous `results.jsonl` and `host.jsonl` first. Use `--offset` to vary the seed for cold keys and sync cohorts. Record this mode with the results because the durable caches and account histories carry over.

The resource monitor reloads primary PID assignments every sample so a reused run cannot retain the prior process identities. Older reused traces can lack primary/frontend per-PID counters; their cgroup totals and latency remain useful, but do not infer the missing per-process attribution.

## Authenticated page context

The production frontend uses `/auth/page-context` to share authentication, settings, preferences, profile and user notifications within one page render. It hydrates only notification/popup data, retains fresh session checks and keeps private responses out of shared caches. Ordinary browser notification refreshes use `/tracking/notifications`; opening the inbox requests the operational queues through `/tracking/recent-events`.

`scripts/benchmark_page_context.py --output <ignored-report.json>` compares the five legacy calls with the new context using real ASGI routes and an explicitly disposable `TRACKING_TEST_DATABASE_URL` named `anylist_test...`. It checks complete response fingerprints, SQL, CPU/wall time and payload bytes with empty and 200-job histories. The sequential microbenchmark does not measure frontend request parallelism or whole-system capacity. See [the ordinary-user comparison](PAGE-CONTEXT-2026-11-16.md) for mixed HTTP results at 200 entries per user, the remaining CPU ceiling and verification limits.

## Standalone catalogue process

The API embeds the catalogue schedulers by default. To supervise them separately, run migrations first, then start `python -m core.catalogue_worker` from the backend directory with the same runtime configuration as the API. Set `CATALOGUE_WORKER_MODE=external` on the API. The standalone process owns catalogue enrichment and mapping. The API continues to own playback, account sync, and upload lifecycles.

Both modes coordinate ownership with a PostgreSQL session advisory lock. Use a direct PostgreSQL connection or a session-mode connection proxy for that process. With external workers, the surviving standby can take ownership if the first process exits, using the existing durable enrichment jobs and retry state. Size `DATABASE_POOL_SIZE` and `DATABASE_POOL_OVERFLOW` per process against the database's connection budget. The coordinator uses a dedicated connection outside these pools.

## API read workers

Set `API_READ_WORKERS=1` or `2` to let the primary API supervise separate processes for the reviewed title, game, search, browse and profile GET routes. All normal callers keep using the existing API port. Each reader authenticates requests and obtains private viewer state from PostgreSQL. Account, import, playback, sync and write requests continue on the primary API. HTTP rate-limited routes continue on their existing owner.

Readers start after primary initialization. They skip startup recovery and periodic account jobs, and reject writes on their loopback listeners. The primary restarts a reader that exits and can serve reads locally while a reader is unavailable. An operator can independently run the catalogue process with `CATALOGUE_WORKER_MODE=external`. Both settings default to the embedded single-process mode.

Allow connections for the primary, every reader, and the catalogue process when choosing database pool sizes. For example, two readers plus a primary and catalogue process, each configured with `DATABASE_POOL_SIZE=10` and `DATABASE_POOL_OVERFLOW=5`, can use up to 60 pooled connections plus the catalogue ownership connection. Run the split benchmark with the selected environment settings. Its recovery probe kills a reader, checks private edits and active sync state, and waits for a replacement process.

Database dependencies on ordinary API routes use FastAPI's function scope. The response is composed before the session closes, and post-response jobs then open their own sessions. Streaming browse retains request scope because its iterator still reads PostgreSQL. Keep that distinction when adding a streaming endpoint or a background job so waiting jobs cannot retain every request connection in a small pool.

## CPU, publication locks and sync queues

`SYNC_CONCURRENCY` bounds media-server pulls across accounts (default 1, range 1..8). Pulls for the same account serialize before taking a global slot; waiting jobs do not open SQL sessions. More slots can drain the queue faster while reducing CPU available for interactive writes. The measured two-slot runs did not establish a better default on the shared VPS; use 2 explicitly when the available CPU budget supports it. Compare both latency and completed syncs before raising this value. Account reconciliation and its durable cancellation/restart rules remain in effect.

The monitor records per-PID CPU counters, process start times and the kernel clock rate. The summary uses surviving PID pairs for each sample interval, including API readers, rather than subtracting aggregate PostgreSQL counters across connection churn. CPU consumed before the first or after the final observation of a short-lived process is uncounted, so PostgreSQL totals are lower bounds. Legacy aggregate samples are labelled. `host_outside_benchmark_average_cpu_cores` estimates competing host CPU; `benchmark_cpu_pressure_percent` reports time with runnable benchmark tasks waiting for CPU. Compare these alongside throttling before attributing a latency change to code. The cgroup includes the load generator, PostgreSQL, Astro, synthetic upstream, API and catalogue processes; its CPU quota is not an API-only budget.

Each process writes `metrics-<role>-<pid>.json` on shutdown with SQL counts, pool peaks and catalogue lock wait/hold times. Set `STRESS_PROFILE=1` for a diagnostic run to add 100 Hz process-CPU samples with Python stacks and coroutine await chains. These identify ORM callers across greenlet boundaries. Keep profiling off for capacity comparisons, and collect artifacts after normal shutdown so readers and the standalone catalogue process flush their counters.

Profile JSON also contains per-second origin samples distinguishing request handlers, work after the final response body, spawned child tasks, named jobs and event-loop callbacks. SQL counters include those origins only in profiling mode. Child tasks can belong to response streaming, so do not assume every `spawned` sample is a background job. Request context is propagated through SQLAlchemy greenlets. A reader killed by the recovery probe cannot flush its final profile; use the surviving process and per-PID host counters with that limitation.

Run `scripts/benchmark_publication.py --help` for the disposable PostgreSQL identity/season/cached-import microbenchmark. Its `--backend` option pins a baseline source tree. Compare semantic fingerprints, statement counts and provider calls along with CPU and latency. Keep raw reports under ignored `.cloud-runtime`. The [CPU and sync investigation](CPU-SYNC-2026-11-16.md) records the measured changes and validation limits.

Migration `mt095_metadata_revision_shards` distributes transactional statistics invalidation over 64 rows. All readers use their sum at the database snapshot cutoff. Apply the migration before starting updated workers; deploy the aggregate readers and migration together. A sequence or maximum revision would lose changes when transactions commit out of order. Visibility changes still purge private snapshots atomically. Downgrading consolidates the committed sum back into row 1 and must be coordinated with application rollback.

## Large account preparation

`scripts/benchmark_account_prepare.py` measures the actual reconciliation initializer against a strictly disposable PostgreSQL database. `--backend` selects pinned source; `--sizes`, `--metadata-bytes` and `--notes-bytes` exercise scaling and overfetch. It records SQL count/selected heavy columns, CPU/wall time and membership/entry fingerprints. It is not a provider or whole-system benchmark.

For real authenticated HTTP and concurrent primary-loop health probes, put the trusted baseline `backend/core/sync_reconciliation.py` at the lab root as `baseline-reconciliation.py`, then set `STRESS_ACCOUNT_PREPARE=1` inside the normal isolated launcher environment. The launcher runs calibration and `account_prepare_http.py`, writing `state/account-prepare-http.json`, then exits. This mode disables primary periodic jobs, alternates old/new initializer bodies on identical 1,000/5,000/10,000-item fixtures, and supports repeated disposable-state runs. It must not be mixed into capacity results. The diagnostic route exists only in the stress app after its private-namespace check.

The normal mixed-load run includes manual pulls and periodic mapper/statistics/etc. A five-minute account-sync tick is not exercised by short levels merely because scheduled settings were seeded. Use a sufficiently long run or an explicit scheduled-cycle scenario to evaluate that path. See the [architecture investigation](ARCHITECTURE-2026-11-16.md) for the measured quadratic defect, its fix and remaining architectural targets.
