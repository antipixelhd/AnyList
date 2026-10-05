# Details page provider research

Checked 4 October 2026. The `/dev/details` previews use fixtures and isolated browser storage. No new provider integration or account writes were added.

## Scores and Steam

| Enhancement | Confirmed route | Design and integration implication |
| --- | --- | --- |
| Audience average | TMDB `vote_average` and `vote_count`; IMDb aggregate rating and vote count | One prominent aggregate with its source. Keep personal scores separate. |
| Ten-point distribution | Aggregate AnyList users’ own ratings | TMDB’s public schema exposes no per-title vote histogram, and IMDb’s free rating file contains only average/count. Do not derive a histogram from either average. The mock chart is labeled **sample AnyList scores**. |
| Steam review sentiment and text | Valve `GET /IUserReviewsService/GetAppReviews/v1/` | Documented anonymous access; filters, review text, helpfulness, play time, positive/negative totals. Totals are first-page fields when querying all review types. Sentiment is binary, so show a positive percentage and review count rather than convert it to a ten-point rating. Start with the summary; make review text an intentional disclosure. Cache and handle HTTP 429. |
| Regional Steam price | IsThereAnyDeal `POST /games/prices/v3` | Register an API key. Match the game/edition, choose country, filter by the Steam **shop**, and preserve provider URLs. A Steam activation key sold elsewhere is a different offer. Display currency, region and retrieval time. |
| SteamDB | No general public API currently offered | Its FAQ prohibits automated scraping. Use Steam directly or a documented price provider. |

Sources: [TMDB schema](https://developer.themoviedb.org/openapi/tmdb-api.json), [IMDb free datasets](https://data.imdb.com/non-commercial-datasets/), [current Valve review API](https://partner.steamgames.com/doc/webapi/IUserReviewsService), [deprecated older endpoint](https://partner.steamgames.com/doc/store/getreviews), [IsThereAnyDeal API and terms](https://docs.isthereanydeal.com/), [SteamDB FAQ](https://steamdb.info/faq/#does-steamdb-have-an-api).

## Similar works

IMDb’s free datasets do not supply recommendation links. The official licensed API documentation reviewed did not confirm access to the IMDb website’s “More like this” algorithm. Treat that capability as unconfirmed until IMDb supplies the subscribed product’s schema; do not assume website fields are licensed API fields.

The practical choice is TMDB: `/movie/{id}/recommendations` and `/tv/{id}/recommendations`, with the corresponding `/similar` endpoints as an alternative. Keep movies and TV separate, deduplicate by provider ID, remove the current title and already displayed Relations, and show a short row near the bottom. The preview’s three suggestions per category are curated illustrative choices, not claimed API results.

Sources: [movie recommendations](https://developer.themoviedb.org/reference/movie-recommendations), [TV recommendations](https://developer.themoviedb.org/reference/tv-series-recommendations), [movie similar](https://developer.themoviedb.org/reference/movie-similar), [TV similar](https://developer.themoviedb.org/reference/tv-series-similar), [IMDb licensed API overview](https://data.imdb.com/documentation/api-documentation/).

## Relations

There is no complete universal relationship provider across all four media types. Use typed edges and retain the provider, source ID and whether an edge is explicit, inferred, or curated.

| Category | Best starting point | Limits |
| --- | --- | --- |
| Movies | TMDB `belongs_to_collection` → collection `parts` | Collection membership is explicit; previous/next from release order is an inference. It can mix prequels, reboot continuity and side stories. Confirm sequence before presenting a narrative relation. |
| Movies / series | Licensed IMDb bulk `movieConnections` | Typed links to IMDb titles, including sequence and spin-off categories. Exclude incidental references, spoofs and unrelated “featured in” links from the default view. This is confirmed in licensed **bulk data**, not assumed to be available through every GraphQL product. |
| Games | IGDB `parent_game`, `dlcs`, `expansions`, `standalone_expansions`, `remakes`, `remasters`, `collections` | Twitch client credentials required. Parent/DLC/remake edges are useful. Collection membership is not an immediate-sequel edge. Shared universes may require curated confirmation. |
| Books and adaptations | Wikidata typed statements such as `follows` (P155) and `based on` (P144), joined to provider identifiers | Open cross-media edges with uneven coverage. Match works rather than ISBN editions. Open Library supplies book metadata and covers; it is not a complete sequence/adaptation graph. |

Display order: **previous/next entry → source/adaptation → spin-offs and other genuine connections**. Omit unknown or empty relations. A shared genre or creator belongs in Similar, not Relations.

Sources: [TMDB collection details](https://developer.themoviedb.org/reference/collection-details), [IMDb movieConnections](https://data.imdb.com/documentation/bulk-data-documentation/data-dictionary/titles/#movieconnections), [IMDb connection types](https://help.imdb.com/article/contribution/titles/movie-connections/GNUNL9W2FTZDGF4Y), [IGDB games](https://api-docs.igdb.com/#game), [Wikidata follows](https://www.wikidata.org/wiki/Property:P155), [Wikidata based on](https://www.wikidata.org/wiki/Property:P144).

## Fixture honesty and feature scope

The old shelf module included unrelated Control, Severance and Dune suggestions. Those were curated browsing choices, not API-derived relations, and have been removed from Relations. The remaining edges are manually selected genuine links: Dune (2021) precedes Part Two; Herbert’s Dune is their source; Dune Messiah is the novel’s next entry; Control and Alan Wake share Remedy’s universe. Severance has no relation fixtures and shows no Relations section.

Verification: [publisher identifies Dune Messiah as Book Two](https://penguinrandomhouselibrary.com/book/?isbn=9780593201732), [Remedy confirms the connected franchises](https://investors.remedygames.com/en/as_an_investment/outlook_and_guidance). Dune movie art is from TMDB; Control scenes are actual Steam screenshots. Poster/hero files are excluded from the lower gallery. The book’s film-adaptation banner is credited and not repeated. Season 2 has neutral placeholders rather than duplicated hero art.

Already implemented in the product in some form: library actions, quick rating, independent season ratings and season averages, tracking, favorites and follow scores. The preview adds a different presentation and local interactions. Proposed extensions remain provider adapters for games/books, typed cross-media Relations, similar-title suggestions, community score distributions, private notes, next-unwatched selection, spoiler controls and artwork browsing. Steam scores, review text and prices were researched, not integrated or presented as live values.
