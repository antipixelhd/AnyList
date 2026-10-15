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

Search reads the indexed local contributor summary. At most 12 distinct studio
candidates receive one movie and one series discovery-page check. No company
profiles or complete filmographies are fetched per search. Public evidence is
cached by visibility filters for 30 minutes (empty results for five minutes,
failures for 15 seconds), with 512 entries, four concurrent provider requests and
at most 24 shared in-flight studio checks. Checks share work across aliases and
requests, have a six-second deadline, and outlive cancellation of one viewer.

Actor/crew search is a separate request from studio verification, so a cold
studio check cannot delay people results. Studio detail pagination retains its
existing bounded timeline reads. Search eligibility samples the latest provider
page of each media type; it does not compute a complete global filmography count
when extra local-only visibility rules exclude part of that page.

Local verification on 2026-10-15 changed `dc` from eight results to DC Studios,
DC Entertainment, DC Films, and `marc` from eight results to Marc Platt Productions.
Every retained example opened a populated works page. Cold studio checks took
1.6–1.8 seconds; warm checks took 6–9ms. These are local development measurements.
