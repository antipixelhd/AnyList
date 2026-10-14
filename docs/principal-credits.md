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
