# Profile statistics: AniList reference, data audit, and implementation plan

Date: 2026-10-09

Branch: `beta`

Status: planning only. No application changes, migrations, or metadata backfills are part of this document.

Updated after the genre/voice-actor references and the request for reusable people, fictional characters, and approximately 24-hour statistics updates. See the companion [people, characters, and daily-statistics architecture](people-characters-statistics-architecture.md) for the expanded schema and refresh design.

## 1. Recommendation

Rebuild statistics around three sections: **Overview, Genres, Actors**. Use the references' navy/cyan graph treatment, clear numeric hierarchy, chart-specific metric switches, and readable distributions. Keep the page dense and restrained: no promotional copy, metric cards, decorative badges, or boxed ranking rows.

Most overview charts can be implemented with the current database schema. That does **not** mean their complete data is already available: some fields are retained inconsistently, some are discarded during ingestion, and some aggregates have conflicting definitions across endpoints.

The principal work is:

1. Define one consistent set of title, watch-time, rating, and date rules.
2. Retain missing descriptive metadata and backfill it without altering watch history.
3. Replace the partial actor aggregation with shared people, normalized contributor credits, and separate fictional-character appearances/performance links.
4. Expose the required grouped metrics through statistics APIs.
5. Publish coherent, approximately 24-hour per-user statistics snapshots and build the three sections using the references' visual language.

**Feasibility:** I can implement the UI, aggregation service, shared person/character/credit models, scheduled statistics cache, ingestion changes, tests, and additive Alembic migrations. TMDB and TVDB API credentials are configured in this workspace. The underlying overview metrics can mostly use existing facts, but the newly requested reusable identities/relations and daily cache require migrations. Character identities/artwork and future game/book/board-game APIs still have source/access gaps. Accurate historical dates and individual dates for collapsed repeat plays cannot be recovered from metadata APIs.

**Evidence limit:** this is a source-code and configuration audit, not a read of the live beta or production database. No real-user coverage percentages, provider authentication results, or live migration readiness are asserted here.

## 2. What the screenshots establish

The five supplied images show AniList's **overview**, including:

- Six headline values: total titles, episodes watched, days watched, days planned, mean score, standard deviation.
- Score bars with Titles Watched / Hours Watched switches.
- Episode-count bars with Titles Watched / Hours Watched / Mean Score switches.
- Format, status, and country distributions using a pie and colored percentage rows.
- Release-year and watch-year lines with Titles Watched / Hours Watched / Mean Score switches.

The references make the numbers and graphs the content. Cyan connects the headline values, selected switches, bars, line, and leading distribution category. The navy background and quieter labels give the graphs contrast without a busy grid or heavy framing.

The two subsequently supplied images show Genres and Voice Actors: ranked entries with Count / Mean Score / Time Watched, title-poster strips, actor portraits, and a titles/characters switch. Adopt that information structure while keeping the previously requested open layout without outer cards. The companion architecture document records the source comparison and implications for fictional-character identity/artwork.

## 3. Proposed design

### Structure and priority

Retain our profile header and primary profile navigation. Beneath them:

1. A left section selector on desktop: Overview / Genres / Actors, using the social/settings navigation conventions. On narrow screens use a compact horizontal section selector.
2. Section heading and media control: All / Movies / Series.
3. Headline totals.
4. Score and episode-count charts.
5. Status, format, and country distributions.
6. Release year, then watch year.

Use meaningful HTML links for the section navigation and preserve selected section/media in the URL. Chart switches affect their own chart. Do not make a single metric switch unexpectedly change unrelated graphs.

The initial overview should cover all recorded history. Put range selection on the Watch Year chart instead of retaining the current ambiguous global year control: current list status and lifetime ratings are not historical snapshots. A future global date filter must have an explicit contract for every affected widget.

### Surfaces and spacing

Keep headline values directly on the page, with small labels below. Use two columns on mobile, expanding into a compact desktop row where space permits. Do not turn each number into a tile.

Use spacing and alignment to separate sections. The earlier request to avoid cards still applies: a subdued tint may sit **inside the plot area**, inspired by the references, but avoid rounded cards around headings, controls, charts, or every genre/actor row. No repeated outer borders or large shadows. Only use a separator where adjacent dense rows need one for scanning.

Desktop charts can form a two-column composition; distributions can form three columns when genuinely readable. Mobile charts stack at full content width. Keep labels legible rather than shrinking entire desktop graphs.

### Color specification

These are proposed reference-inspired values, not measured source colors:

| Role | Proposed dark-theme value |
| --- | --- |
| Page | `#0b1622` |
| Plot / switch track | `#17212f` |
| Main data and selected switch | `#3db4f2` |
| Primary text | `#d5dde7` |
| Secondary labels | `#9aabbd` |
| Secondary category | `#34495e` |
| Additional categories | `#ef4b3e`, `#f28c48`, `#b77bdc`, `#59c7b1` |

Use stable category-to-color mappings; colors must not move when a different category becomes the largest. Give light mode its own tested surfaces/text values. Keep the profile header's owner color, but recommend cyan as the consistent statistics data accent so owner colors cannot make graphs unreadable. Validate contrast in both themes before implementation is accepted.

### Graph behavior

- Bars: modestly rounded tops, cyan fill, compact value labels above, quiet baseline, no unnecessary grid or vertical axis.
- Lines: cyan stroke, visible markers, values on selected/hovered points and on sparse charts. Use interpolation that cannot overshoot into negative or invented peaks. Do not connect through genuinely missing data.
- Distributions: compact pie plus ranked legend rows with label, value, percentage. For a single category, show a full circle and one row; do not invent additional slices.
- Metric switches: compact pill track with one clear cyan selected state. Hide meaningless switches: Score does not need Mean Score as a metric.
- Hover/focus: slightly emphasize the datum and show its exact value. Highlight the same pie slice from its legend row. Support tap selection and keyboard focus; interaction must not depend on hover.
- Motion: short opacity/position transitions and restrained chart interpolation, approximately 160–240 ms. No bouncing counters or oversized entrance effects. Respect reduced motion and avoid animating an initial server-rendered value from zero.
- Dense years: reduce tick frequency or allow a controlled horizontal view. Never silently drop data or create page-wide horizontal overflow.
- Accessibility: color always accompanies a name/value. Provide an accessible data representation, clear focus states, and useful tooltips. Avoid persistent explanatory paragraphs on the page.

## 4. Graph-by-graph comparison with our retained data

“No schema migration” below means the source metric can be derived from existing columns/JSONB. It can still require ingestion changes, a metadata backfill, new aggregation code, and API fields. The shared contributor/character model and persistent daily statistics snapshots are additional schema changes across the feature, even where an individual graph's source facts need no new columns.

| Reference item | What we retain today | What the current summary exposes | What is still required | Schema migration? |
| --- | --- | --- | --- | --- |
| Total titles | Current `TrackedEntry` rows joined to movie/series `Media`; completed watch events also identify watched movies and parent shows | Current list total and unique watched titles | Separate listed-title and watched-title meanings; canonicalize series identities | No |
| Episodes watched | Completed episode `WatchEvent` rows, `play_count`, episode IDs and parent `show_id` | Unique episodes, with repeats reported separately | Sum episode plays for an inclusive “Episodes Watched” value; keep distinct episodes available separately | No |
| Days watched | `Media.runtime`; some runtime JSON; completed events and repeat counts | Estimated minutes, currently only from dated events | Include undated events in lifetime totals; consistent runtime fallback and repeat multiplication; return runtime coverage | No |
| Days planned | Planning status, title runtime, series season/count/runtime metadata, released episode catalogue and existing progress evidence | Nothing | Define remaining planned workload; refresh incomplete catalogues/runtime metadata; exclude unaired/unknown episodes from a claimed numeric total | No for an estimate; data refresh needed |
| Mean score | Current manual score or effective average of season scores on tracked entries | Average rounded to one decimal | Consistent rated-title cohort; retain precision until display; decide display scale | No |
| Standard deviation | The same current effective scores | Nothing | Population standard deviation, excluding unrated titles; consistent precision/cohort | No |
| Score → Titles Watched | Current effective scores plus completed watch evidence | Histogram of all rated current entries; UI coarsens it to ten intervals | Watched-title join; documented display bins; preserve finer scores where useful | No |
| Score → Hours Watched | Scores, event runtimes, play counts | Nothing grouped by score | Allocate completed play minutes to each scored canonical title, then its score bin; unrated handling | No |
| Episode Count → three metrics | Series `number_of_episodes` in some `Media` metadata; season `episode_count` in `Media`/`Show`; tracking catalogue | Nothing grouped by title length | Resolve authoritative regular-episode total; use the reference buckets; aggregate title count, watch hours, mean current score | No; metadata coverage/backfill needed |
| Format distribution | `MediaType` reliably distinguishes movies and series | Media filter, but no distribution | Movie/Series distribution is straightforward. TV/ONA/OVA/Special/Music classifications are not reliably retained or derivable | No for Movie/Series; additional source/mapping for exact AniList categories |
| Status distribution | Watching / Completed / Paused / Dropped / Planning on current entries | Status counts | Distribution/percentages and fixed colors; no new metadata | No |
| Country distribution | Some network/company country fields; occasional country keys may exist in old JSON | Nothing | Persist **title** origin/production countries; normalize codes; refresh old records; represent unknown and multi-country titles correctly | No; ingestion and backfill needed |
| Release Year → three metrics | Movie `release_date`, series `Media.release_date`, `Show.first_air_date`; scores/events/runtimes | No release-year grouping | Group by movie release or series first-air year, not episode air dates or watch dates; join current scores and completed play minutes | No |
| Watch Year → three metrics | `watched_at`, inferred/shared/provisional date flags, completed events, play counts | Monthly movie/episode activity and an event-year filter | Annual canonical-title counts, hours, current mean scores; explicit date quality and collapsed-repeat policy | No for recorded history; lost historical dates are not recoverable |
| Genres page | Genre names in movie/series/show JSON | Top-level genre counts across current entries | Watched-title cohort, canonical genre mapping, title/hour/mean-score metrics, complete ranking and title drill-down | No initially |
| Actors page | Shared TMDB `TitleCredits` cache; cast names/portraits on some media metadata; TVDB cast formatter; legacy person media rows | No actors in current summary; legacy endpoint has top actors | Shared Person identities, normalized media credits, full cast/portraits, scheduled snapshots, rankings and related poster strips | Yes for the newly requested reusable model; JSON-only enrichment remains a smaller alternative |
| Characters view | Role strings and some provider cast associations; no canonical character schema/art cache | Nothing | Separate Character identities/artwork, media appearances and person-performance links; verified source identity and art access | Yes; schema is feasible but complete source coverage is not established |

### Data that we must not substitute

- Company/network country is not the title's country of origin.
- Original language is not a country. Japanese language alone is not proof of production in Japan.
- Episode progress is not the total number of episodes in a series.
- Cached episode rows can be incomplete; counting them is not proof of series length.
- TMDB television `type`, such as Scripted or Reality, is not AniList's TV/ONA/OVA taxonomy.
- Insertion time and inferred import time are not an authoritative historical watch date.
- Playback progress is not a complete record of actual minutes spent watching.
- A series actor credit does not prove that actor appeared in every episode the user watched.

## 5. Current database and ingestion findings

### Current tracking and history are sufficient foundations

[`TrackedEntry`](../backend/models/tracking.py) stores status, score mode, manual score, season scores, progress, start/finish dates, and rewatch count. [`WatchEvent`](../backend/models/events.py) stores completed plays, repeat counts, nullable watch dates, and separate inferred/shared/provisional flags. [`Media`](../backend/models/media.py) and [`Show`](../backend/models/show.py) store provider identity, release dates, and metadata JSON.

These records remain authoritative. The requested daily cadence adds derived state/snapshot/ranking tables so page loads do not recalculate history. Build snapshots from the same source facts and ensure change detection covers history edits, ratings, statuses, imports, and relevant metadata refreshes. Do not replace source history with cached totals.

### Metadata is a reduced projection, not the full provider response

The API wrappers request rich TMDB responses, but [`enrichment.py`](../backend/core/enrichment.py) constructs selected dictionaries before saving them. [`browse.metadata_fields`](../backend/core/browse.py) adds popularity, vote count, and watch providers; it does not preserve country fields.

Concrete gaps:

- Movie and series media enrichment discards title `origin_country` / `production_countries`.
- Media cast keeps only ten people, storing name/character/portrait but omitting the stable person ID.
- Series media enrichment retains `number_of_episodes` and `episode_run_time`.
- [`show_metadata.py`](../backend/core/show_metadata.py) retains seasons, genres, networks, and some freshness/airing fields, but not the same episode totals, runtimes, title countries, or cast.
- `enrich_series_from_show()` replaces the series media snapshot with show metadata while preserving tracking-prefixed keys. Merely adding fields to one writer would therefore not fix retention consistently.
- [`tvdb.format_series`](../backend/core/tvdb.py) provides genres, official seasons, dates, language, and cross-provider IDs, but currently does not return title country or full actor data as part of that projection.
- `tvdb.format_cast()` returns person IDs, roles, and portraits, but caps the result at twelve and is not integrated into the shared statistics credits cache.

**Required fix:** define shared descriptive metadata mappings and update every relevant writer/refresh path. Merge descriptive enrichments while preserving TVDB canonical numbering and tracking metadata. A metadata refresh must never switch a TVDB-native show to TMDB episode ordering.

### Actor infrastructure exists, but is incomplete

[`TitleCredits`](../backend/models/title_credits.py) already stores cast, directors, writers, studios, networks, and fetch time, uniquely keyed by TMDB title ID plus media type. Existing migrations created the table and added networks; a new actors feature should reuse/evolve it.

[`credits.py`](../backend/core/credits.py) currently:

- Retains only `{id, name}` for ten cast members; drops portraits, role details, and ordering.
- Uses series-level TMDB credits, not episode-accurate actor appearances.
- Omits TVDB-only titles from its cache population.
- Triggers via the older own-profile statistics endpoint, not the current summary.
- Uses the newest cache row's fetch time as its scheduling gate. A fresh unrelated row can prevent missing or stale titles from being fetched.
- Uses one owner's resolved API key to launch an instance-wide import.
- Uses process-local task ownership; this is insufficient for durable coordination across workers/restarts.
- Silently skips individual fetch failures.
- Counts watch-event rows as plays rather than summing `play_count`.
- Loads the entire credits table and returns only the top fifteen people per group.

These are concrete ingestion, aggregation, and scheduling changes needed before presenting this cache as a complete actor ranking.

### Two statistics endpoints have different semantics

The current [`tracking.profile_stats`](../backend/routers/tracking.py) and older [`profile.get_user_stats`](../backend/routers/profile.py) cannot be combined without reconciliation:

| Area | Current tracking summary | Older profile statistics |
| --- | --- | --- |
| Watch time | Dated events only; direct runtime column; multiplies repeat count | All completed events; runtime JSON fallback; no repeat multiplier |
| Activity | Monthly play counts | Distinct watched items per period |
| Scores | Effective tracked-title scores | `Rating` records, including rating-date filtering |
| Genres | Current tracked-title memberships | Movie watch rows / series episode watch rows |
| Actors | Not exposed or refreshed | Partial shared credits ranking |

The current summary also filters hidden anime from entry-based statistics but not from its event-based statistics. The new service must apply the same visibility/media rules across both.

Do not put independent fixes in each graph. Build shared title and watch cohorts, then aggregate consistently. Preserve compatibility for the profile overview, which also consumes the existing summary contract.

## 6. Recommended metric contract

These are proposed implementation rules, not descriptions of AniList's internal calculations.

### Canonical title identity

- A movie is one local movie identity, reconciled using existing provider-linking rules.
- A series is its canonical `Show`, associated with its whole-series tracked entry by provider identity.
- Episode events map to their parent series. Season and episode rows are never extra titles.
- Resolve TMDB/TVDB cross-references through existing identity helpers. Never merge people or titles just because names match.
- History-only titles without a tracked entry can still count as watched; their status and personal score can remain absent.

### Cohorts

- **Listed titles:** current tracked movie/series entries; used for Total Titles and Status Distribution.
- **Watched titles:** canonical titles with completed watch evidence; used for Titles Watched chart metrics and Genres/Actors rankings.
- **Rated watched titles:** watched titles with a positive current effective tracked-title score; used for overview Mean Score / Standard Deviation and grouped mean scores.
- **Planned titles:** entries currently in Planning; used for Days Planned.

This keeps “Titles Watched” honest while retaining planning/dropped statuses. A completed list state without episode-level evidence does not justify inventing dated plays. If imported progress has no event evidence, report the coverage gap; any separate progress-based estimate must be explicitly distinguished.

### Episodes and minutes

`Episodes Watched = sum(max(play_count, 1))` for completed episode events. Keep distinct episode count separate in the API.

`Estimated Watch Minutes = sum(resolved_runtime_minutes × max(play_count, 1))` across completed plays. Include undated plays in all-time totals. Actual playback duration is not claimed.

Prefer episode/movie runtime column, then a valid retained runtime; a series-level episode-runtime fallback is an estimate and must be identified in API coverage. Do not assign film runtime to episodes or treat missing runtime as a known zero. Return counted plays with known runtime, estimated runtime, and missing runtime alongside the minute total.

`Days Watched = Estimated Watch Minutes / 1440`; chart hours divide by 60. Round only for display.

### Planned time

Recommend “remaining released workload in Planning,” not announced future seasons:

- Movies: one unwatched runtime per planning movie.
- Series: known released regular episodes minus unique completed episode evidence; sum remaining runtimes, using a clearly identified series-runtime estimate only where needed.
- Use the existing tracking catalogue/released-episode rules. Never perform provider catalogue hydration inside a public statistics GET.
- Exclude specials by default, consistently with regular-episode counts.
- If only a progress number is retained without reliable episode membership/catalogue, classify that planned workload as unknown instead of fabricating exact remaining minutes.

Return unknown planned-title/episode counts. Show an unavailable or partial value honestly. This estimate does not forecast future episodes or watch time with pauses.

### Scores

Use [`effective_score`](../backend/core/tracking_rules.py), once per canonical rated title. Do not weight score means by episodes or rewatches.

Keep our internal 0–10 scale. The references use 0–100; multiplying by ten is a display choice, not a database conversion. Recommend retaining our familiar scale while matching the graph treatment. Effective season averages can contain arbitrary decimals, so exact half-point bars alone are insufficient.

Recommend twenty documented half-point intervals for the score histogram, aggregating derived averages into those intervals while showing exact scores in title details. The screenshots' score labels are a reference for presentation, not proof that their listed scores are equal-width bins.

For positive scores `s₁ … sₙ`, use arithmetic mean and **population** standard deviation: `sqrt(sum((sᵢ − mean)²) / n)`. No ratings means unavailable; one rating means zero deviation. Carry rated-title count in every grouped mean so a single rating is not mistaken for a broad sample.

“Mean Score” on a watch-year chart means the **current scores of titles watched in that year**. Historical score-at-the-time is unavailable from the current mutable tracking score. `Rating.rated_at` and activity changes are not a guaranteed complete rating revision history.

### Episode-count bins

Use `1`, `2–6`, `7–16`, `17–28`, `29–55`, `56–100`, `101+`, plus Unknown. Apply them to a series' total regular-episode catalogue, not watched progress. Recommend excluding movies from this chart; their format distribution already represents them. In Movies mode, omit this chart rather than treating every movie as a one-episode series.

Provider totals can change as shows air. Use a consistent catalogue scope, metadata snapshot date, and source; exclude specials and identify incomplete catalogues. Do not silently use a partial local cache as an authoritative total.

### Countries, genres, and formats

- Movies: retain production-country codes, with provider-supplied title origin fields available for a documented fallback.
- Series: retain title origin-country codes; use title production countries as a documented fallback. Never use network/company country.
- For multi-country titles, recommend equal fractional contributions `1/k` to pie shares, including Unknown when no country is known. Percentages then sum to 100%. Return distinct title counts separately, since membership counts can overlap.
- Genres are multi-valued. Each genre gets a title's full membership, hours, and score contribution; genre totals can exceed overall totals. Use bars/rankings, not a pie pretending genres are exclusive.
- Normalize genre names through a stable cross-provider mapping, preserve source labels, and retain an Unknown bucket. TMDB movie/TV genre names differ, so the mapping must be explicit rather than casually combining labels.
- Start Format with Movie / Series. Exact TV/ONA/OVA/etc. support is a separate enrichment decision requiring a reliable taxonomy and title matching. Neither animation genre nor country is sufficient to infer it.

### Release year and watch year

- Release year comes from movie release date or series first-air date. A series' hours across all its watched episodes belong to the series' release-year bucket.
- Watch year comes from completed event timestamps. A series can count once in each year where it has completed watch evidence; annual title counts need not sum to lifetime distinct titles.
- Default yearly history should use authoritative dates. Exclude undated, inferred, shared, and provisional dates from that view; make estimated-date inclusion available as an explicit option if desired, with coverage in its tooltip/data details.
- A collapsed event with `play_count > 1` has only one retained timestamp. For chronology, recommend counting one dated play and classifying additional repeats as undated, unless upstream evidence proves separate dates or the same-year attribution. All repeats still count in lifetime totals.
- Watch-year hours must use that same dated-play attribution rule. Distinct titles and means remain deduplicated within each year.
- Default to UTC for the initial annual boundaries unless an established user-timezone policy is available and applied consistently.
- Zero-fill known empty years for counts/hours. A mean with no rated titles is null, never zero. Unknown release year is a separate bucket, not year 0.

## 7. Genres and Actors sections

### Genres

Top controls: media selection and sort metric: Count / Mean Score / Time Watched. Default to title count.

Use closely aligned ranked sections: genre name, all three compact metrics, and a bounded strip of related title posters, as shown in the new reference. The switch changes sorting; it does not hide the other metrics or trigger recalculation. Keep rated sample size available in tooltips/details rather than stacking persistent badges. Mean-score sorting should expose its rated sample; do not hide low-sample genres behind an undisclosed threshold.

Related posters link to their media. A full genre watched-title drill-down can follow with title, current score, and watched time, using the same snapshot membership and pagination. Preserve filters. Do not repeat a page of genre cards or limit the section silently to eight genres, as the current summary UI does.

No new genre relation table is required initially. A normalized genre catalogue/association table is a later option if filtering/query plans justify it; it is not a prerequisite for a useful page.

### Actors

Top controls: media selection and sort by Titles Watched / Hours in Credited Titles / Mean Score. Default to title count.

Use dense aligned sections with a modest portrait, name, all three compact metrics, and a related title-poster strip. Sort by the selected metric while keeping the other values visible. Do not create large biography cards. Use a neutral portrait fallback when images are absent. Provide a Titles / Characters view when trustworthy fictional-character identity and artwork are available; role-name strings alone do not establish a complete Characters view.

For the first release, define this as **actors credited in watched titles**. A credited series receives its watched-series hours as a title-level association; this is not actual actor screen time or proof of appearances in specific watched episodes. Use the precise “Hours in Credited Titles” label when that metric is selected.

Required actor data:

- Provider-qualified stable person identity.
- Name and portrait path.
- Full retained cast for the selected credit scope, with billing order/role details where supplied.
- Canonical credited title identity and source.
- Fetch/schema version and completeness state, so ten-person legacy snapshots are not treated as full credits.
- Paginated actor aggregates and credited-title drill-downs.

Ordinary show-level cast supports a limited series-cast definition. If we want all series cast including guests, fetch an appropriate aggregate/season credit source and retain its scope explicitly. This requires additional provider calls and checking the payload; the existing basic `/tv/{id}` credits are insufficient for that claim.

Exact actor-by-watched-episode statistics, voice-language filtering, and actor screen time are **not** established by the current data. Episode-accurate credits would need an episode/person relation or an episode-scoped credits cache, substantially more fetching, and provider coverage verification. Screen time is not supplied by our current providers.

## 8. Database changes: minimum and recommended

### Path A: overview, genres, and TMDB-only actors

**No Alembic migration is inherently required.**

Extend retained metadata JSON with title country fields, consistent episode totals/runtime fields, and stable cast IDs. Expand `TitleCredits.cast` JSON with portrait/order/role/source/scope fields. Mark enriched payload versions/completeness inside JSON or with an agreed sentinel/version contract so old snapshots are refreshed.

Then run an idempotent metadata/credits backfill. JSONB supports additional keys without DDL, but that backfill still writes the database and must happen only during authorized implementation. Ensure future writers preserve these fields.

This path leaves TVDB-only actor coverage incomplete. It is a valid smaller release only if that limitation is accepted and accurately represented.

### Path B: shared people, characters, credits, and daily snapshots — recommended

The broader contributor/character requirement changes the recommended architecture. Use normalized Person records with provider identities; Character records with their own artwork/identities; media credits with extensible roles; media-character appearances; and performance links connecting an acting credit to one or more characters.

Preserve existing `MediaType.person` list/import rows through a legacy-to-canonical person bridge. Backfill canonical people/credits from the existing `TitleCredits` cache and verified IDs, then migrate statistics reads to those relations. The old cache can remain an ingestion/compatibility snapshot during cutover; it must not become a competing identity database.

Add durable per-user refresh state, successful snapshot generations, and genre/person aggregate rows. Build all sections and supported media scopes together on a staggered approximately 24-hour cadence. Serve the last successful snapshot, with immediate live access checks and invalidation for sensitive removals.

The companion [architecture plan](people-characters-statistics-architecture.md) defines the proposed tables, constraints, indexes, future media boundaries, role/character semantics, scheduling races, failure recovery, and migration sequence. This supersedes the earlier recommendation to only expand the JSON actor cache.

The existing `SyncJob` model is account/source-sync specific and is not assumed to be a generic metadata/statistics queue. Integrate with the existing scheduler using database coordination and durable state. Metadata fetching stays separate from per-user statistics computation.

### Changes not needed for this scope

- No new authoritative watch/score fact table solely for these charts; the daily cache is derived.
- No conversion of stored 0–10 scores to 0–100.
- No rewrite of existing watch dates or series episode numbering.
- No new watch-time session tracker to provide estimated runtime-based hours.
- No full biography/image-gallery schema just for names/portraits; canonical Person/Character identities are now explicitly required.
- No historical rating table unless score-at-the-time is explicitly requested.

If exact repeat chronology becomes a requirement, future imports must retain each individual play timestamp. A schema alone cannot recreate past dates discarded by upstream providers/imports.

## 9. Metadata and backfill plan

1. Run a read-only coverage audit on the actual target database and chosen users, including history-only titles and TVDB-native shows.
2. Update every relevant metadata writer and refresh path before backfilling, so newly retained fields are not erased later.
3. Deduplicate targets by canonical provider title, not by user or watched episode.
4. Reuse retained compatible data first; fetch only missing, stale, or version-old title metadata/credits. Do not request a person's full biography for every ranking row.
5. Use configured instance credentials for shared enrichment, bounded batches, documented concurrency, provider-rate-limit backoff, and resumable progress.
6. Validate a complete response before replacing a last-good snapshot. Store source, scope, freshness, and completion status.
7. Update descriptive fields and credits only. Preserve status, scores, watch dates, repeat counts, progress, tracking-prefixed keys, and canonical episode numbering.
8. Publish aggregate coverage counts and retry failed targets. Do not hide a failed actor fetch by interpreting it as no actors.

A normal TMDB movie/show detail response already includes credits through our wrapper, so countries and basic cast can usually share that request. Broader series cast may require additional requests. Episode-accurate credit coverage could require requests proportional to episodes and should not be bundled into the initial rollout without measuring cost.

## 10. Coverage audit still required against live data

The audit should produce counts, not names, notes, credentials, or exported user history. Use a read-only database transaction and a least-privileged account. Audit beta independently from production; development fixtures cannot establish live completeness.

| Coverage measure | Why it matters |
| --- | --- |
| Current listed versus canonical watched titles | Establish headline/chart cohort differences and unmatched history-only titles |
| Watched movies/episodes missing runtime; series fallback availability | Determine exact/estimated/unknown watch-hour coverage |
| Dated / undated / inferred / shared / provisional plays | Establish trustworthy watch-year coverage |
| Collapsed repeat rows and extra repeat counts | Quantify chronology that cannot be assigned to a year |
| Missing or malformed movie release / series first-air dates | Release-year coverage |
| Titles with valid title country arrays | Country backfill volume; company country does not qualify |
| Titles missing genres; incompatible provider genre labels | Genre normalization work |
| Series with complete regular catalogue and known total | Episode-count and planned-time coverage |
| Planning titles with known remaining runtimes | Days Planned confidence |
| Current effective title ratings versus legacy rating records | Avoid silently changing rating semantics |
| Watched titles with no TMDB ID; TVDB-only titles | Actor-cache schema and provider coverage need |
| Missing/stale credits, ten-person legacy snapshots, missing person IDs/portraits | Actor backfill volume and quality |
| Hidden-anime and private-profile cases | Consistent visibility and access control |

Example PostgreSQL queries to be adapted to the application's bound-parameter interface (`:user_id`), **not executed as part of this plan**:

```sql
-- Quality counts for completed playback records. Categories overlap.
SELECT
    count(*) AS completed_rows,
    count(*) FILTER (WHERE watched_at IS NULL) AS undated_rows,
    count(*) FILTER (WHERE date_inferred) AS inferred_rows,
    count(*) FILTER (WHERE date_shared) AS shared_rows,
    count(*) FILTER (WHERE provisional) AS provisional_rows,
    count(*) FILTER (
        WHERE watched_at IS NOT NULL
          AND NOT date_inferred AND NOT date_shared AND NOT provisional
    ) AS authoritative_dated_rows,
    sum(greatest(coalesce(play_count, 1), 1)) AS total_plays,
    sum(greatest(coalesce(play_count, 1), 1) - 1) AS collapsed_extra_plays
FROM watch_events
WHERE user_id = :user_id AND completed;

-- Direct runtime coverage, deliberately not claiming JSON/fallback coverage.
SELECT m.media_type,
       count(*) AS completed_rows,
       count(*) FILTER (WHERE m.runtime IS NULL OR m.runtime <= 0)
           AS missing_direct_runtime_rows
FROM watch_events w
JOIN media m ON m.id = w.media_id
WHERE w.user_id = :user_id AND w.completed
GROUP BY m.media_type;

-- Cache coverage for watched TMDB titles; excludes TVDB-only titles.
WITH watched_tmdb_titles AS (
    SELECT DISTINCT 'movie' AS media_type, m.tmdb_id
    FROM watch_events w JOIN media m ON m.id = w.media_id
    WHERE w.user_id = :user_id AND w.completed
      AND m.media_type = 'movie' AND m.tmdb_id IS NOT NULL
    UNION
    SELECT DISTINCT 'series', s.tmdb_id
    FROM watch_events w
    JOIN media m ON m.id = w.media_id
    JOIN shows s ON s.id = m.show_id
    WHERE w.user_id = :user_id AND w.completed
      AND m.media_type = 'episode' AND s.tmdb_id IS NOT NULL
)
SELECT count(*) AS watched_tmdb_titles,
       count(*) FILTER (WHERE c.id IS NULL) AS missing_credit_rows,
       count(*) FILTER (WHERE c.fetched_at < now() - interval '7 days')
           AS stale_credit_rows
FROM watched_tmdb_titles t
LEFT JOIN title_credits c
  ON c.media_type = t.media_type AND c.tmdb_id = t.tmdb_id;
```

Presence of a cache row or a ten-element cast array does not prove full cast completeness. The implementation must audit payload versions/scope explicitly. The metadata audit must also follow the same canonical-title resolver as aggregation so duplicates and alias rows do not distort coverage.

## 11. API and frontend implementation shape

Introduce a shared backend statistics service, with one canonical title resolver, current effective-score calculation, watch cohort, runtime resolver, and visibility/access policy. Reuse `profile_access` before returning any user-scoped statistics or drill-down data.

Proposed API separation, subject to existing route conventions:

- Overview: totals plus score/length/status/format/country/release-year/watch-year aggregates.
- Genres: paginated genre metrics and a selected-genre watched-title drill-down.
- Actors: paginated provider-qualified actor metrics and credited-title drill-down.

For a bucket return its key/label, distinct titles, watch minutes, score sum/count or mean/count, and relevant coverage. Return all meaningful metrics together where small enough; switching chart metrics should not need another network request. Avoid sending full watch histories or raw credit payloads to the browser.

Keep the existing summary response compatible until its consumers are migrated. Do not regress [`profile-overview.ts`](../frontend/src/lib/profile-overview.ts) by silently replacing fields or score semantics.

The frontend can reuse its existing Chart.js loading and lifecycle patterns in [`profile-stats.ts`](../frontend/src/lib/profile-stats.ts), subject to verifying the installed chart package/version. Pie, bar, and line charts need no new visualization service. Use accessible/server-rendered totals and data fallbacks; request failures should provide a compact retry action rather than empty graphs presented as zero.

Query only the target user's records and relevant credit keys. Do not scan the whole instance credits cache into Python. Paginate drill-downs and actor rankings. Assess query plans on realistic volumes before adding indexes; existing watch-event and title-credit indexes are useful starting points.

The requested persistent snapshots must include media/visibility filters, source revisions, and contract version. Check live access before serving them. Never share a private profile's computed result through a public cache key. Metric sorting should read the stored ranking, not recalculate user facts; related artwork previews must come from the same snapshot generation.

## 12. Permissions and operational feasibility

| Action / access | Current evidence | Needed during implementation |
| --- | --- | --- |
| Repository edits and Git push to beta | Workspace access; original task authorized beta work and push | No new permission for the completed planning document; product implementation is intentionally deferred |
| UI/API code and Alembic migration authoring | Existing Astro/Python/PostgreSQL project | Feasible here once implementation is requested |
| Local migration/test execution | Isolated cloud development/test setup exists | Use disposable test DB; never point tests at beta/production |
| TMDB metadata/credits requests | Credential configured; API host allowed in cloud network policy | No new API key indicated; verify credentials and rate/coverage behavior without logging secrets |
| TVDB metadata/people requests | Credential configured; API host allowed | Verify full cast payload access and any subscription/PIN requirement for the needed endpoint |
| Server-side actor portrait fetching | Existing image cache targets `image.tmdb.org` and `artworks.thetvdb.com` | Those image hosts are not in the observed cloud API allowlist; allow server-side access for local validation if blocked. Deployed-server connectivity must be checked separately |
| Read-only live beta coverage audit | No live connection established in this task; workspace VPN not configured in observed status | A permitted DB connection/read-only role or authorized existing operational route is needed to obtain real coverage counts |
| Applying beta migrations | Preview workflow supports beta deploys and forward Alembic upgrades with DB owner rights | Use the existing deployment workflow if its `PREVIEW_ENABLED` gate, credentials, and runner/VPS connectivity are healthy; do not assume live state from source configuration alone |
| Production changes | Separate beta-to-main process documented | Outside this planning task; use established promotion process when explicitly requested |
| Exact AniList format taxonomy | No AniList enrichment integration or title mapping found | Choose a reliable source, mapping and permitted API access; public read access may suffice, but matches/coverage must be validated |

The repository's [`Preview Deploy`](../.github/workflows/preview-deploy.yml) triggers on beta pushes when enabled. The [`preview controller`](../ops/preview/controller.py) takes a beta backup before changed/pending migrations, then runs forward `alembic upgrade heads`. This provides a path to applying a migration without asking for a new direct database-owner login. It does not prove the deployed workflow is currently healthy.

The observed cloud configuration allows the TMDB and TVDB API hosts and reports their credentials ready. I have not made provider calls or changed network configuration for this planning task. Additional host access is an environment/network permission issue, not a reason to ask you to expose API keys in chat.

## 13. Proposed delivery order

1. **Review this plan:** resolve the few product choices below, then authorize implementation.
2. **Read-only target coverage audit:** report the exact missing-data counts and confirm provider/cast access.
3. **Data contract and visual prototype:** verify overview hierarchy, chart palette, spacing, and desktop/mobile navigation before broad UI work. Use explicitly identified fixture data if live data access is unavailable.
4. **Shared identities, metadata, and statistics-cache migrations:** implement the normalized contributor/character architecture and durable daily snapshots; test upgrades against both existing and empty databases, preserving legacy person-list links.
5. **Controlled backfill:** refresh descriptive metadata/credits, preserving tracking/history and last-good snapshots; measure coverage and API load.
6. **Shared aggregates, scheduler, and APIs:** implement precise metric semantics, daily jobs, coherent snapshot publication, privacy rules, coverage, pagination, and compatibility.
7. **Overview, Genres, Actors UI:** build against the agreed data contract and prototype; validate hover/tap/focus/reduced-motion behavior.
8. **Beta verification and push:** run repository checks, browser checks, and reconcile known fixture totals; push beta under the existing authorization once implementation is requested. Verify deployment/migration health if accessible.

Do not ship the actor section as complete while its backfill is silently pending. A truthful partial state is preferable to permanently hiding missing data or presenting zeros. Keep operational details in diagnostics/data details, not repeated prose in the main page.

## 14. Validation and acceptance

- Every screenshot graph is represented or has an explicit, accepted data limitation; switches use the same cohort across metrics.
- No card wall, decorative copy, repeated subtitles, or unnecessary separators. Main controls/totals precede lower-priority breakdowns.
- Totals reconcile with fixtures containing movies, series, specials, repeats, history-only titles, planning titles, and mixed provider identities.
- Ratings handle season-derived fractional averages, unrated entries, one-rating deviation, and current-score versus historical-score meanings.
- Watch-year fixtures include inferred/shared/provisional/undated dates, year boundaries, and collapsed repeats; lost chronology is not fabricated.
- Release-year fixtures use series first-air year, not the episode's year. Missing release/length/runtime metadata remains unknown.
- Multi-country pie shares sum to 100% apart from display rounding. Genre/actor membership totals are allowed to overlap and never masquerade as exclusive partitions.
- Country/episode/runtime fields survive every metadata writer and scheduled refresh, including TVDB-native series.
- Old top-ten credits are upgraded; new missing titles are scheduled despite fresh unrelated cache rows. Failed fetches retry and retain prior successful data.
- Actor sorting/pagination and credited-title expansion work without merging people by name. Title-level hours are accurately labeled.
- Person-role/character links preserve multiple performances without multiplying title counts, score contributions, or watched time. Legacy person-list links survive migration.
- Daily snapshots coalesce changes, coordinate across workers, preserve last-good data after failure, and never delay privacy changes. Title/character artwork previews match their ranking generation.
- Anonymous/public, owner, unauthorized/private, and hidden-anime behavior are consistent across overview and drill-downs.
- Empty/error/loading states, browser back/forward, Astro client navigation, SSR fallback, keyboard/touch interaction, and reduced motion work.
- Mobile widths around 320/390 px, tablet, and desktop show no unintended page overflow or clipped switches/labels. Dark/light contrast is checked with real chart colors.
- Backend `unittest`, frontend `node:test`, type/build checks, and meaningful migration/backfill tests pass using the documented cloud check workflow. This document-only task does not require rerunning application tests.
- Before applying any eventual migration, confirm live revisions, backup behavior, and the beta deployment result. Do not reset or clone over persistent beta data.

## 15. Product choices to confirm before implementation

These do not block the planning deliverable. Recommended defaults are stated so the implementation can be scoped concretely:

| Choice | Recommended default | Alternative and impact |
| --- | --- | --- |
| Actor coverage | Shared Person identities and normalized TMDB + TVDB-native title-level credits | TMDB-only JSON enrichment: less schema work, incomplete coverage and insufficient foundation for the broader contributor/character requirement |
| Actor meaning | Cast credited in watched titles, with precise title-level hour label | Episode-accurate/voice-language statistics: additional credit fetching, schema and coverage work |
| Format | Movie / Series for this movies-and-series application | Exact anime taxonomy: new enrichment source and mapping; cannot infer ONA/OVA from current data |
| Score display | Existing 0–10 scale, reference-inspired bars | 0–100 display only; no stored-score conversion |
| Annual history | Authoritative dates; estimated inclusion optional | Include all recorded dates: fuller-looking history, but imports/inferred dates can misrepresent past years |
| Planned time | Remaining released regular workload in Planning | Include announced future episodes: forecast rather than a known backlog; weaker coverage |
| Visual surfaces | Open layout with optional tint inside plots; no outer cards | Full AniList chart cards would conflict with the earlier no-cards request |
| Person / character structure | Individual contributors and separate fictional characters, linked through scoped credits/appearances | One untyped entity conflates real people, fictional identities, and organizations |
| Statistics updates | Coherent, staggered approximately 24-hour per-user snapshots with live access checks | Request-time recalculation adds load and makes refresh races/failures visible to visitors |

## 16. Work completed in this planning pass

Reviewed the current stats page/API, legacy statistics endpoint, tracking/watch/rating/media/show models, metadata writers, provider formatters, credits cache/import, released-episode catalogue logic, profile section navigation, and beta migration/deployment configuration. Compared each visible reference graph against retained and exposed data, identified missing fields and semantic mismatches, and separated no-DDL metadata enrichment from contributor/character and statistics-cache migrations.

Follow-up planning incorporated the supplied genre/voice-actor screenshots, existing person media/list/Trakt support, reusable Person/Character/credit relations, and a durable approximately 24-hour statistics policy. See the companion architecture document for the revised recommended migration scope.

No page redesign, API changes, schema migration, live coverage audit, provider calls, or backfill has been performed. The next implementation should start from this contract rather than another visual restyling of incomplete aggregates.
