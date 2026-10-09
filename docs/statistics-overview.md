# Implemented statistics Overview

Implemented on beta, 2026-10-10. This is the Overview milestone from
[the statistics plan](profile-statistics-anilist-plan.md). Genres, Actors, and
game/book tracking remain later work.

## Contract

`GET /tracking/profile/{username}/stats/overview?media_type=all|movie|series`
returns profile context, `pending|ready|error`, generation, UTC computed/next
update timestamps, refresh state, and a typed Overview. All three scopes share
one generation. The existing summary endpoint remains available to the profile
page. Privacy and logged-out access are checked on every request.

The six headline statistics and seven charts use one canonical title cohort.
Current list entries establish list totals/status; completed local watch events
establish watched titles. Verified catalogue bridges/provider IDs establish
identity; names do not. History-only titles are supported. Specials and recorded
repeat plays contribute lifetime watch time and episode plays, while regular
catalogue counts determine series length/planning.

Scores retain fractional season means and use 20 half-point intervals `(lower,
upper]`; deviation is population deviation. Country shares divide a title equally
among its normalized ISO countries, while counts may overlap. Movies prefer
production countries; series prefer origin countries. Missing countries and
release years are explicit Unknown groups. Unknown years are separated from the
chronological line. Empty years have zero count/time and null unrated means.

Watch Year uses authoritative UTC dates only. A collapsed repeat event attributes
one play to its timestamp; remaining repeats and inferred/shared/provisional or
undated plays remain lifetime-only. Runtime fallback is marked estimated;
missing runtime is excluded and exposed in coverage. Headlines show incomplete
time as a lower bound or unavailable. Planning uses retained complete regular
episode catalogues, unique completed membership, and known released episodes;
partial progress, missing release dates/catalogues/runtimes remain incomplete.

## Refresh and retention

Migrations mt039–mt040 add per-user state, owned snapshot generations, shared metadata
revision, database triggers, and explicit UTC scheduling independent of the database timezone. Fact invalidation participates in the writer's
transaction, including bulk/import writers. Removal of facts purges cached
generations immediately; user deletion cascades. Anime visibility changes purge
generations. Shared metadata changes increment a revision checked at the next
due refresh.

Workers claim short database leases with `SKIP LOCKED`, read facts under repeatable
read, and publish all scopes atomically after checking revisions and ownership.
Failed builds keep the previous generation and retry with bounded backoff;
expired leases recover after restart. Unchanged due work skips recomputation.
Jobs are staggered approximately every 24 hours, with a bounded scheduler poll.
No provider requests occur in statistics GETs or calculations. Existing shared
metadata refresh jobs retain the country/language/runtime/season fields needed
for subsequent snapshots. Incomplete provider coverage remains explicit.

## Frontend and validation

The page has per-chart Titles/Hours/Mean controls (Scores has Titles/Hours),
Movies/Series/All navigation, and a Watch Year-only range. Server-rendered tables
provide the numerical fallback. Requests cancel superseded filters and clear
rendered values on denied access; client navigation cleans up charts/listeners.
Legend controls support pointer, keyboard, and touch activation. Charts honor
reduced motion and redraw on theme changes.

Coverage includes arithmetic/cohort fixtures, actual PostgreSQL leases/revisions/
purges/privacy, regression suites, type checking, production build, and browser
checks at 320/390/768/1440px with both themes, metric independence and browser
history. Test databases are disposable and separate from the browser fixtures.
