# AnyList mixed-load benchmark

The driver sends authenticated HTTP requests through a production Astro build and its API proxy. The real backend lifespan starts sync, metadata, credit, and automatic game workers. PostgreSQL starts from the repository's Alembic migrations. Use a disposable database and an isolated network namespace.

The default fixture creates 400 users, 6,000 screen titles, 2,000 games, 80 screen entries and 24 game entries per user. One quarter of the accounts have a synthetic Jellyfin connection. Each full pull reads 48 films over loopback. Some users also have a scheduled account pull. The automatic game runner has an initial queue of 260 games and can discover more through its normal scheduling paths. Reviewed game identities exercise IMDb credit publication alongside games with no matching public identity.

Each virtual user works through one journey at a time with a mean 3.5-second pause. The mix includes screen and game detail pages and their follow-up requests, title and contributor search, browse, list reads, connection and sync status reads, and manual tracking writes. About 23% of journey selections use varying keys. `--prime` first reads the twelve shared search and detail keys. Short levels admit a subset of connected users' full pulls during the level. Longer levels rotate the subset in three or fewer windows to preserve overlapping sync work. Use the recorded provider calls to verify cache misses. A varying key can become warm when a previous request or worker acquired it.

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
