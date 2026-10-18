# Search and browse

Movie/series quick search (`tracking/catalog`) and browse text search call the
same `browse_page` coordinator. `title_search.candidates` owns the bounded TMDB
candidate adapter and correction provenance; `catalog_search.search_rank` ranks
the completed batch. Eligibility performs no hidden provider reads. Actor and
crew share person retrieval; studios use their existing company adapter.
CatalogueProviders remains the provider adapter for book/game catalogue search;
new media kinds should adapt their score provenance instead of reusing TMDB's
numeric popularity scale.

## Policies and tuning

`discovery_policy` reads optional `UserSettings.preferences.discovery_tuning`:

```json
{"newest":{"movie":{"min_popularity":30,"min_votes":0}},
 "search":{"series":{"min_popularity":0}}}
```

Keys are surface (`search`, `newest`, `upcoming`, `popular`, `trending`, `score`,
`oldest`, `title`) then `movie`/`series`. Zero disables a floor. Initial testing
uses popularity 25 on Newest/Upcoming and zero elsewhere; vote gating is zero.
The previous 250 TMDB vote baseline excluded WAR (298852, 16 votes at evaluation)
and Below (285322, 158 votes). Search ignores the account browse vote minimum.
Missing values fail an enabled floor; IMDb votes never satisfy a TMDB vote floor.
These are independent experiments, not comparable scores across providers.

Saved runtime/categories apply to title and contributor results. Browse's
memory-only Show all bypasses personal curation, preserving explicit controls,
adult exclusion and administrator anime visibility. It survives filter/view/type
changes, resets on query change/reload, and never writes account settings.
Quick search has no Show all.

## Chronological discovery

`chronological_discovery` builds public event selections; `release_catalogue`
pages cached events in SQL; `release_browse` merges their ordered prefixes and
attaches private editor state afterwards. Cursors preserve independently consumed
provider/local positions. Use the returned cursor for continuation. Cached
collections have no fixed 500-title display/storage cutoff.

`release_events` preserves YYYY, YYYY-MM and full dates. An overlapping window
needs event-specific upcoming evidence. A returning status without a window is
insufficient; known aired episodes override contradictory upcoming metadata.
Only explicit season identities are labeled. Premiere and season one share an
event; Upcoming selects the nearest future return. `include_returning` is an
internal option. TVDB season years are retained. General movie dates prefer local
distribution evidence and otherwise expose worldwide scope; theatrical dates
require types 2/3 in the selected country. Date scope is available on the year
tooltip, without adding badges to every card.

Audience curation is a **distribution proxy**, not measured local popularity:
positive local offers, a local broadcast network or local distribution qualifies
alongside the selected popularity floor. Global popularity >=60 also qualifies
when distribution is unknown or evidenced in at least three markets. Known
single-market distribution does not become global merely through a high score.
No language/production-origin blacklist is used. The evaluation retained Squid
Game, WAR and Below, removed Japan-only AIBOU from Germany, and retained it for
Japan. This can still miss titles with incomplete distribution metadata and can
retain foreign hits with broad distribution. Text search never uses this rule.

## Work and cache budgets

- Ordinary title search: one provider page/media kind; weak queries may add at
  most two correction pages. No per-result credits/release/streaming reads.
- Text queries within chronological/Upcoming filters use the same candidate
  adapter, finalize at most three search pages plus bounded first-page corrections,
  and resolve release events before filtering. Thus a query for a returning show
  tests its next season window rather than its historical premiere. The timeline
  orders the completed event batch by date; ordinary grid title search retains
  relevance ordering. The existing status control reflects Upcoming and can
  switch the timeline back to ordinary chronological discovery.
- Chronological selection: at most six discover pages. Two cover premieres and
  four cover returning/regional dates; single-feed modes use six. At most 120
raw title candidates, deduplicated/cheap-filtered before six-way detail reads.
  This selection is bounded coverage, not a complete provider catalogue.
  `partial` distinguishes its endpoint from true catalogue exhaustion. Newest
  advances through 180-day windows, finalized before presenting each window.
- At most eight simultaneous public selection builds, shared by matching
  callers. Cancelling the last waiter cancels orphaned work. Selections: 64
  cached entries/30 minutes. TMDB: 2,000 entries/30 minutes, isolated mutable
  copies, pooled connections, 16 network slots, 128 retained shared flights.
- Contributors: eight candidates/group; indexed qualifying-work EXISTS checks,
  no per-keystroke full-credit aggregation. Person evidence is public/cacheable.
  Studio eligibility samples at most two pages/media kind over the job lifetime;
  complete detail timelines remain separate. Worker queue admission is bounded.
- Private browser title cache: 40 entries/60 seconds, invalidated by edits and
  navigation. Browse cache: 40 entries/five minutes. Neither crosses viewers.
- Quick debounce 60 ms, browse typing 100 ms; no artificial minimum loading wait.
  Contributor updates never replace title DOM. Timeline append preserves existing
  cards/headings. First-screen fill has a bounded extra-page budget.

Region is a browser preference inferred from timezone/locale. A manual country
persists for one year and is never overwritten by automatic detection. Ordinary
visits learn the hint before the next browse request. A server cannot know a new
visitor's device timezone before receiving a browser hint; the first direct
visit can require one correction. Account Country remains a fallback for other
metadata/availability clients, distinct from date-format and metadata languages.

## Verification

Relevant suites: `test_browse`, `test_browse_requirements`, `test_discovery_policy`,
`test_title_search`, `test_result_filters`, `test_tmdb`, `test_tvdb`, contributor
evidence/search/studio suites; frontend browse/quick-search/cache/timeline suites.
Database tests require a disposable test database. Also run Astro check/build and
verify authenticated desktop/mobile pages, repeat switches and scroll append.

Local Chrome observations (390×844/1440×1000, 30 searches each, 2026-10-18;
includes debounce, visible layout and a browser frame): mobile warm clickable
p50/p75/p95 80/83/87 ms; first browser title-cache misses 317/425/680 ms. Warm
decoded artwork 98/101/105 ms. Desktop warm clickable 81/83/87 ms, decoded artwork
100/102/107 ms. Query-result entrance animation is omitted; dialog/tab motion is
preserved. Desktop had already warmed server/browser data,
so its first round is not a cold-provider measurement. These are preview lab
measurements, not VPS/network guarantees. Preserve that distinction when tuning.

Controlled local coordinator accounting (authentication/browser/debounce excluded)
recorded one TMDB request on ordinary misses, three on the typo case, and zero
on warm provider-cache hits. Warm calls took 16–25 ms with 8–10 indexed SQL reads,
no database writes and no contributor jobs. Cold calls took 55–315 ms in the six
query sample. Contributor work runs independently under its separate budgets.
