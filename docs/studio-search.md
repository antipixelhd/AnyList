# Studio discovery and grouping

## Platform references

| Platform | Documented model | Application here |
| --- | --- | --- |
| [TMDB company details](https://developer.themoviedb.org/reference/company-details), [alternative names](https://developer.themoviedb.org/reference/company-alternative-names), [discovery](https://developer.themoviedb.org/reference/discover-movie) | Stable company IDs, separate aliases, production-company discovery filters. | Keep provider identities and source credits. Use company IDs to find attributable works; a company-name match alone does not qualify for search. A pipe-separated company filter unions reviewed group members. |
| [IMDb company formatting](https://help.imdb.com/article/contribution/industry-professional/company-name-formatting/G5UFMUM3FLURTBMF), [name corrections](https://help.imdb.com/article/contribution/industry-professional/company-name-corrections/GF6J8U5FJ5JNAN3V) | Prefer the recent credited name, consolidate branches of the same organization, preserve former on-screen names with linked company listings. | Legal suffixes are display-only. Historical studio eras stay separate. Concurrent branches need explicit evidence before sharing a presentation. |
| [Trakt studio schema](https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/_internal/response/studioResponseSchema.ts), [movie studios](https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/movies/index.ts), [networks](https://github.com/trakt/trakt-api/blob/master/projects/api/src/contracts/networks/index.ts) | Studios have stable Trakt/TMDB IDs and country metadata; title studio credits and TV networks have separate contracts. | Match identities rather than names; retain the production-company versus broadcaster distinction. The contracts do not establish a general company-family merge rule. |

The platform references establish identity/credit conventions, not a universal
notability score. Our work-eligibility and recency ranking are product choices.

## Rules

- Studio results require released production works passing the viewer's vote,
  runtime, category and anime filters, with a discovery floor of 250 votes on
  an attributable work (or the viewer's higher vote threshold). No logo requirement or arbitrary name
  exclusion substitutes for work evidence. Existing local results survive a
  provider outage; unverified remote companies do not enter results.
- Rank prefix/exact matches ahead of incidental substring matches, then newer
  qualifying releases ahead of older catalogues; work count breaks ties. This
  measures credited release recency, not a claimed company founding date.
- `core/studio_groups.py` holds reviewed presentation memberships. Preserve
  separate provider identities, original credits and historical URLs. Never infer
  membership from similar names, shared ownership alone, or date overlap alone.
- Each new membership must document the common production brand and concurrent
  activity. A predecessor/rebrand remains a separate searchable studio. Reviewed
  group pages union movie and series works and deduplicate titles before paging;
  movie and series IDs remain distinct.
- The initial reviewed group combines DC/DC Comics, DC Entertainment and DC
  Vertigo under DC Entertainment. DC identifies these as concurrent brands in
  its [2012 branding announcement](https://www.dc.com/blog/2012/01/19/dc-entertainment-reveals-new-brand-identity)
  and [2015 screen/publishing announcement](https://www.dc.com/blog/2015/06/24/dc-entertainment-takes-center-stage-at-comic-con-international-san-diego-2015).
  Their provider production credits overlap. DC Films and DC Studios stay
  separate studio eras; the latter's new remit is documented in the
  [DC Studios announcement](https://www.wbd.com/james-gunn-and-peter-safran-named-co-chairmen-and-ceos-of-dc-studios/).
  This is a reviewed presentation group, not an assertion that its members have
  identical legal identities. Other studio families require their own evidence.

## Resource bounds and verification

Search reads the indexed local contributor summary and persistent public work
records in `catalogue_contributor_evidence`. It never waits for company-work
verification or person filmography requests. A cold provider-name lookup remains
one request. Reviewed studio members share one job; person credits share one job
across actor/staff scopes and viewer filters. No credentials or list state are
stored in evidence, and discovered titles are not imported into the catalogue.

At most 12 studio candidates per query are admitted to a global queue capped at
256 pending records. Each worker batch leases two jobs (`FOR UPDATE SKIP LOCKED`)
for 45 seconds and performs at most four concurrent provider requests. Network
work happens outside database transactions. Interrupted workers recover through
lease expiry; token-guarded publication rejects superseded results.

Studio variants include provider-side vote/runtime/category filters, anime
visibility, and reviewed membership. Positive evidence lasts seven days; complete
empty evidence lasts one day. Person combined credits are stored once and filtered
for each viewer/role. Local evidence is checked against work metadata in a batch,
not accepted from an unfiltered contributor count. `known_for` may establish a
positive person match before full credits are available; a failed shortlist is
unknown, not verified empty. Complete cached credits override the shortlist.

A studio job reads one movie and one TV discovery page, continuing on later ticks
only when additional pages may contain qualifying works. It retains progress
instead of declaring an unexamined timeline empty. Work counts from this evidence
are observed samples, not claimed complete filmography totals. Positive local
works remain usable during provider failures. Retries back off; after five failed
attempts the job frees its queue slot and waits six hours for search-driven retry.

The open search renders its first response immediately, then refreshes only
pending contributor requests every 1.5 seconds, at most four times. Query changes,
closing, and navigation cancel requests and waits. Unchanged responses do not
replace result DOM. Unknown/failed evidence never becomes verified-empty merely
because a request failed. Studio/people requests remain independent.

Verification must cover cold and repeated name queries, restart persistence,
filtered-empty people/studios, identities/branch grouping, admission limits,
parallel-worker leases, failure/backoff, and full credits beyond a weak shortlist.
Use a disposable database for automated tests. Timings from local development
include provider/network variability and are not production latency guarantees.
