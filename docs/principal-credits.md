# Principal screen credits

Movies and series retain complete provider credits. `core/principal_credits.py`
projects a versioned `principal` flag and provider evidence onto each credit;
detail reads and local contributor search use that projection before loading
people or character artwork. Games, books and full contributor filmographies
retain their existing scope.

## Selection

- Movies select at most 16 cast members, primarily by provider billing.
- Series select at most 24. The first eight billed actors are protected;
  remaining places favour episode participation, featured status and billing.
- Archive and uncredited performances do not qualify.
- Crew select at most 16 distinct people, using exact jobs rather than department
  substrings. Creators/showrunners/head writers, directors, writers/source authors,
  composers, cinematographers, editors and selected department heads take
  precedence over producers. Each group has a quota; producers share two places.
- Film executive producers do not qualify solely through that credit. Series
  executive producers can qualify. Popularity, participation and portrait
  availability break ties; a missing portrait never excludes a principal director
  or author.

Details have Cast, Crew and Studios sections; there is no Other crew section.
The frontend preserves server selection order and continues rendering collections
progressively. Optional TVDB profile lookups and character-art enrichment are
restricted to selected people, with their existing request limits retained.

For selected TV producers, a badge reports the provider's production-job episode
count when available. Overlapping jobs use the maximum count, never a sum. A
director's count cannot become a producer's count. Full-series counts and
creator/showrunner/head-writer badges are suppressed. Aggregate counts do not
establish episode numbers or season ranges, so none are invented.

## Discovery

Local search requires a principal credit on a stored screen title, filters in SQL
before limiting candidates, and matches saved aliases. Remote TMDB discovery
requires known works and an allowed creative department. Broader department
matches also require popularity of at least 5. This is a discovery heuristic,
not an authoritative notability score. Cached people known only through excluded
credits cannot re-enter through remote results. Local and remote candidates are
ranked together and deduplicated by provider/canonical identity, never by name.
Search performs no per-result profile or filmography requests.

## Studios

Legal suffixes are simplified for display; the original name remains searchable.
Provider-declared company aliases are additive. Cross-provider companies share a
canonical root only when TVDB explicitly supplies an unambiguous TMDB company ID.
Old catalogue URLs and source credits continue to resolve to that root. Conflicting
provider IDs or protected edits prevent an automatic bridge.

Parent companies, subsidiaries, name prefixes and historical names do not establish
identity. Marvel Studios and Marvel Films therefore remain separate, as do two
different provider company IDs with similar display names. Broader brand-family
aggregation would require a separately curated relationship, not another name
normalizer.

## Existing installations

Migrations mt048/mt049 add credit evidence and verified company aliases without
deleting credits, people, tracking or provider payloads. Cached credit projections
are invalidated and reprocessed through the existing credit repair path. Legacy
credits without evidence use conservative role/billing eligibility until repaired.
The existing `scripts/backfill_screen_credits.py` command can process stored titles
in bounded batches. Profile backfill version 2 discovers TMDB company aliases and
limits person enrichment to principal credits.

## Credit access and resource bounds

Migration mt050 adds a derived contributor-search table. Database statement
triggers refresh distinct-work counts and eligible roles for affected canonical
contributors when credits change. Verified alias changes also refresh searchable
names and the canonical root. The summary updates in the same transaction as
source data; it does not depend on a background worker or a stale read cache.
Concurrent updates to a root serialize before recomputing its summary.

Quick search reads only summary columns, including known-person eligibility and
studio-page checks. It never groups the complete credit table per keystroke.
Only contributors with qualifying screen credits enter its trigram index. The
index disables GIN's pending insertion list to keep reads predictable. Local
search runs at most two queries concurrently per application worker, leaving
capacity for tracking, detail reads and background work on the shared VPS.

Imports resolve identities in batches of 200 and reuse identical contributor
records within a document. Existing multi-ID records can reuse their verified
root; new or contradictory bridges retain the full reconciliation path and
protected-field checks. Credit writes expand JSON record batches of at most 200
in PostgreSQL, avoiding thousands of SQL expressions on the Python event loop.
Sparse updates preserve known fields. Equal credits do not create new row
versions or reset character links; unchanged repairs skip character projection.
The catalogue write lock and provider-refresh leases remain in place.

Character matching uses keyed exact-role and alias lookups, retaining ambiguity
and title-scoped generic roles. Substantial matching runs in one CPU lane off
the event loop; large ORM reads and database writes yield between batches of 200.
Character links use set-based updates rather than per-credit driver statements. Detail responses
show up to eight role labels per person and inspect at most eight actor-role
associations per actor for artwork. The source associations remain complete.

Character-image reads use indexed SQL probes, returning four distinct URLs per
selected character in batches of at most 256 character IDs. Ordinary detail
reads do not load related title metadata. Anime-filtered statistics read only
the classification fields they need and filter before selecting images. Full
character galleries and contributor filmographies retain their dedicated reads.

See the [performance verification](audits/credit-performance-fixes-2026-10-14.md)
for measured results and remaining limits.


## Metadata provider policy

Detail credit loading and the daily tracked-title backfill share one source
policy. TMDB is primary. A series with an existing TVDB canonical episode
catalogue remains TVDB-primary, even after acquiring a TMDB ID. Existing
catalogue source markers are also respected. Neither enrichment path renumbers
seasons/episodes or changes personal tracking facts.

The secondary provider runs only when the catalogue lacks a cast, crew or
organization section, or a title name, overview, poster, release date or genres.
Secondary projection fills absent sections and scalar metadata additively;
it does not import another full cast/crew when those sections already exist.
The full provider response remains in the shared metadata snapshot. Missing
sections can be projected from that cached response without another download.
Secondary data never supplies competing episode-count/layout attributes.

TVDB person/company identity requests are limited to the sections being filled;
a metadata-only gap does not request person or company profiles. Shared refresh
leases/backoff do not trigger a competing secondary import. The background
worker uses the same source/cache policy rather than re-ingesting both sources.
Its primary-source refresh remains authoritative, including removal of obsolete
primary credits; secondary enrichment is always additive.

Character artwork retains its separate demand/background enrichment path, even
when TMDB metadata is complete. Poster/background galleries retain the existing
TMDB image policy; this change does not merge TVDB title artwork into galleries.
