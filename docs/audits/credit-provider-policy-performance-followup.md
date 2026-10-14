# Provider policy performance follow-up

Compared application commit `0be2eac` with `68e051d`, following the [credit access audit](credit-performance-fixes-2026-10-14.md). This pass measures the provider policy; it makes no production application or schema changes.

## Finding

The provider policy improves cold title enrichment and periodic refreshes by avoiding redundant TVDB work. Core bulk credit ingestion, search, concurrent reads and character projection show no material regression attributable to the change. Small cached-path timing variations remain; the TVDB-primary path adds one cached SQL lookup and uses more SQL on its first import despite lower total latency and database CPU.

## Method and limits

- Disposable PostgreSQL 16: two CPUs, 2 GiB memory and 512 MiB shared memory. Python 3.13 runs on the Windows host without an application CPU cap. No preview/production data or real provider credentials are used.
- Rebuilt the existing synthetic fixture: 1,000 titles / 100,000 people, then 5,000 titles / 200,000 people, with 212 source credit associations per title. Benchmark imports add extra rows afterward.
- Reused the existing generic verification harness. Searches assert identical ranked results; import refresh/additive checks compare complete credit state. The role stress case is prepared with 9,001 existing roles.
- Paired imports and provider cases use five samples per version, alternating the version order. Character projection uses three samples per version on separate actors with 9,001 retained roles and 3,000 new roles each. These are diagnostic medians, not reliable production tail-latency estimates.
- Previous entry functions are loaded from Git. The paired import checks use the previous ingest/additive functions and shared unchanged lower-level helpers on the same schema. Provider comparisons use previous credit/backfill entry functions and deterministic current adapters in unrestricted mode for the previous policy; their unrestricted provider request behavior is equivalent.
- Provider payloads are stubs. Zero-delay cases measure application/database work. A separate 20 ms fixed wait per request is a sensitivity experiment, not measured TMDB/TVDB network performance. Provider rate-limit/accounting lane waits and HTTP response caching are excluded identically for both versions.
- Database CPU is the container cgroup delta, including background activity and probe overhead; short-request CPU values should not be treated as isolated SQL CPU. Historical comparisons can vary with host activity, caches, storage and fixture content.

## Controlled provider policy comparison

Each synthetic movie has 100 cast, 16 crew and two studio associations. TVDB source responses retain all 116 people associations even when only a missing section is projected. The series case has a stored TVDB canonical catalogue and both provider IDs.

Zero simulated network delay; cold enrichment median:

| Case | Before | After | Wall change | Provider requests | SQL statements | DB CPU before / after |
|---|---:|---:|---:|---:|---:|---:|
| Complete TMDB title | 1023.5 ms | 336.6 ms | -67.1% | 28 → 1 | 371 → 45 | 418.3 / 185.4 ms |
| Missing overview only | 955.9 ms | 401.5 ms | -58.0% | 28 → 2 | 372 → 75 | 375.8 / 199.5 ms |
| Missing crew section | 1054.2 ms | 560.1 ms | -46.9% | 28 → 14 | 394 → 235 | 413.2 / 227.1 ms |
| TVDB-primary series | 875.4 ms | 595.9 ms | -31.9% | 29 → 27 | 371 → 392 | 333.4 / 216.4 ms |

The complete-title case eliminates one TVDB title request, 24 person requests and two company requests. Missing-overview enrichment retains the TVDB title request but eliminates all those profile requests. Missing-crew enrichment retrieves up to 12 crew profiles, with no cast or studio profile fanout.

The TVDB-primary case correctly retains TVDB metadata and its canonical catalogue. Its first import uses 392 rather than 371 SQL events because TVDB identities are established first instead of reusing a pre-imported TMDB contributor set. Its cached request uses six instead of five SQL events. Those are real access-count increases; the measured wall time and CPU still improve.

Periodic refresh of a complete title, with already cached provider payloads:

| Metric | Before | After |
|---|---:|---:|
| Wall time | 269.7 | 138.3 |
| SQL events | 38.0 | 21.0 |
| Database CPU | 124.2 | 70.8 |

Wall time and database CPU are milliseconds. Background refresh still preserves authoritative primary-source cleanup. It does not download fresh data in these cached cases.

## Cached request costs and small slowdowns

| Case | Before / after, first block | Before / after, second block | SQL events before / after |
|---|---:|---:|---:|
| Complete TMDB title | 10.84 / 8.39 ms | 7.74 / 7.75 ms | 5 / 5 |
| Missing overview only | 8.69 / 10.19 ms | 7.35 / 6.98 ms | 5 / 5 |
| Missing crew section | 9.94 / 8.96 ms | 7.40 / 8.56 ms | 5 / 5 |
| TVDB-primary series | 10.17 / 8.76 ms | 8.08 / 8.49 ms | 5 / 6 |

All cached requests made zero provider calls in both blocks, so the second column does not include the simulated provider wait. The missing-overview case was 1.50 ms slower in the first block but 0.38 ms faster in the next; the missing-crew case was 1.16 ms slower in the second block. These small variations do not establish a consistent cached latency regression. The extra TVDB-primary lookup is consistent and retained for source correctness.

## Controlled bulk imports

Both versions execute the same bounded SQL counts. Full source row counts are asserted after every paired sample.

| Credits | Operation | Before | After | Wall change | SQL events | DB CPU before / after |
|---|---|---:|---:|---:|---:|---:|
| 1000 | cold | 937.0 ms | 908.7 ms | -3.0% | 28 / 28 | 495.7 / 503.4 ms |
| 1000 | refresh | 552.8 ms | 549.0 ms | -0.7% | 15 / 15 | 250.9 / 252.8 ms |
| 1000 | additive | 83.3 ms | 76.6 ms | -8.1% | 13 / 13 | 36.3 / 36.8 ms |
| 3000 | cold | 2439.4 ms | 2470.0 ms | +1.3% | 62 / 62 | 1392.0 / 1417.5 ms |
| 3000 | refresh | 1284.8 ms | 1090.4 ms | -15.1% | 29 / 29 | 629.9 / 650.5 ms |
| 3000 | additive | 201.9 ms | 217.8 ms | +7.9% | 17 / 17 | 58.5 / 61.9 ms |

The 3,000-credit additive repair is about 16 ms (7.9%) slower in this sample; cold import is about 31 ms (1.3%) slower. Database CPU differs by about 1.8% for that cold import. There is no added per-credit query fanout or material cold-import regression. The small additive timing difference is retained in the raw evidence rather than counted as an improvement.

## Generic rerun versus the prior audit

| Operation | Previous audit | Rerun | Interpretation |
|---|---:|---:|---|
| Common-name search | 54.8 ms | 51.7 ms | Similar |
| Studio search | 5.9 ms | 5.0 ms | Similar |
| Two-letter search | 74.5 ms | 79.2 ms | Similar |
| 24 mixed reads, two search slots | 299.6 ms | 304.0 ms | Similar |
| 3,000-credit cold import | 2148.4 ms | 2873.7 ms | Single observations; use paired comparison above |
| Same-document refresh | 970.5 ms | 1461.4 ms | Single observations; use paired comparison above |
| Cached additive repair | 307.6 ms | 277.9 ms | Single observations; use paired comparison above |
| Character-image fanout detail read | 51.5 ms | 28.5 ms | No observed regression; same access code |
| 3,000-role projection, 9,001 retained | 1414.3 ms | 2217.0 ms | Slower standalone observation; controlled follow-up below |

The role projection source is byte-equivalent after text newline normalization between application commits. On equal fresh paired fixtures its median was **1683.2 → 1694.0 ms**, with **33 SQL events** for both versions and median maximum loop delay **50.7 → 48.8 ms**. The slower standalone result does not reproduce as a material code regression. The reconstructed retained-role contents and cache state are not identical to the historical fixture.

The image stress read retained a traced allocation peak of **0.302 MiB**, versus 0.295 MiB previously. Full credit state equality and eight displayed role labels still pass.

## Simulated provider wait sensitivity

Exactly 20 ms per stubbed source request; these values are not real API latency measurements.

| Cold case | Before | After | Requests before / after |
|---|---:|---:|---:|
| Complete TMDB title | 1693.0 ms | 342.5 ms | 28 / 1 |
| Missing overview only | 1715.1 ms | 423.3 ms | 28 / 2 |
| Missing crew section | 1667.1 ms | 897.0 ms | 28 / 14 |
| TVDB-primary series | 1743.3 ms | 1382.4 ms | 29 / 27 |

## Reproduction

Use a disposable `anylist-credit-audit` PostgreSQL 16 container on port 55449, database `anylist_test`, with `--cpus 2 --memory 2g --shm-size 512m`. Set `CREDIT_AUDIT_DATABASE_URL` and `DATABASE_URL` to that loopback database, a disposable `SECRET_KEY`, and `PYTHONPATH=.;scripts`. From `backend/`, run:

```text
python scripts/audit_credit_performance.py --output ../.venv/credit-policy-fixture.json --commit 68e051d --keep-schema
python scripts/verify_credit_performance.py --fixture ../.venv/credit-policy-fixture.json --output ../.venv/credit-policy-performance.json --existing-role-registry-size 9001
python scripts/audit_screen_provider_policy.py --fixture ../.venv/credit-policy-fixture.json --output ../.venv/credit-policy-paired.json
```

The paired harness asserts that complete TMDB titles make no TVDB calls, overview-only gaps do not request profiles, crew-only gaps exclude cast/company fanout, cached visits make no provider calls, TVDB-primary series retain their source, and TVDB snapshots retain all 116 source people associations. Its preserved import samples can be reused with `--skip-imports` on the same schema.

See the [raw observations](credit-provider-policy-performance-followup.json) for individual samples, SQL counts, query plans, CPU, event-loop delay, retained rows and provider request breakdowns. The disposable container is removed after verification.
