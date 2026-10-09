# AnyList catalogue foundation

Implemented on `beta`, 2026-10-09. This is the schema and server metadata phase. Statistics APIs, statistics snapshots/jobs, frontend redesign, reading/play tracking and board games remain phase two or later.

## Canonical model and identity

`catalogue_entities` is the stable local identity root. Its checked, immutable `kind` distinguishes movie, series, game, book, book series, game collection, person, organization, fictional character, book edition and game release. This replaces the earlier proposal for separate external-ID tables with one foreign-key-backed `catalogue_identities` table. `(namespace, external_id)` is unique; namespace/kind compatibility is enforced in PostgreSQL and repository helpers.

People keep one identity across contribution roles. Organizations carry producer, broadcaster, developer, publisher, distributor or other source-qualified credits. Company and network namespaces remain separate. Streaming availability stays in the existing regional browsing integration; it is never inferred from production credits.

Book editions and game releases have separate tables linking to their work, with publisher/format/language/pages or platform/region/release date. ISBNs map to editions, not works. Hardcover canonical book aliases resolve to the canonical work. IGDB `version_parent` resolves edition-like game variants to their parent work, retaining the variant as an `igdb.game_version` release identity. The edition's native `igdb.game` ID and verified Steam IDs resolve to the parent work, including edition-like recommendation targets. DLC, expansions, remakes and remasters remain distinct works connected by typed relationships. Recommendations and collection/series membership have their own relation types and source provenance.

`catalogue_credits` preserves each provider credit/job/performance and source label, including edition-scoped book contributions. `catalogue_character_appearances` connects independently identified characters to works; `catalogue_character_performances` connects performer credits to appearances with language. Composite FKs enforce the same work. TMDB credit IDs and TVDB title/performer association IDs never become fictional character IDs. Name/title similarity never merges identities. A conflicting authoritative cross-reference stops the import; administrators can bind an unmapped identity with a recorded review reason. Merging existing conflicting roots requires a separately reviewed relation rewrite.

`catalogue_legacy_links` bridges existing movie/series/person `Media` rows; `catalogue_show_links` bridges existing `Show` rows. Neither migration nor backfill changes those IDs, person lists, watch history, ratings, progress or canonical episode numbering. Shared entities have restrictive deletion behavior; legacy-row deletion removes only its bridge.

## Retention, requests and refresh

Raw provider responses and fetch provenance live in `catalogue_metadata_snapshots`, separately from canonical fields. Field provenance and protected manual corrections prevent missing/secondary data from clearing trustworthy values. Legacy metadata wins over provider rewrites; IGDB and Hardcover outrank RAWG for game/book work fields. Partial imports add/upsert observed relations rather than deleting absent credits, editions or artwork. Missing credit labels/billing positions, character flags, series positions and edition/release fields retain known values; zero and false remain valid updates. Generated placeholder names never replace known names or prevent later enrichment. Provider removals require a future explicit reconciliation policy.

Refresh claims use durable three-minute leases and recheck ownership before publication. A successful metadata snapshot lives seven days; price snapshots live six hours. Failures retain the last successful payload and canonical records, record an allowlisted error code and schedule exponential backoff. Search/request caches contain at most 128 responses and 16 MB in total for ten minutes. Requests use bounded timeouts, at most three attempts, small page limits, a four-MB limit enforced while streaming decoded responses, and numeric/date Retry-After handling. Oversized streams and error responses close without being cached. Metadata detail and Steam pricing fetches have a 120-second outer budget. Unexpected adapter/ingestion failures roll back partial writes, persist an allowlisted failure code/backoff and retain last-good payloads; active leases and stale retry results report `refreshing`/`last_good` accurately.

Dedicated PostgreSQL provider lanes serialize outbound requests across application workers, with spacing of 0.3 seconds for IGDB/TMDB, 1.1 for Hardcover, one for RAWG and 0.5 for ITAD/TVDB. Request counts are durable. RAWG defaults to a conservative 19,000-request monthly application budget; the provider's plan/account usage, including other installations, is authoritative. These lanes apply to the new catalogue API, not every historical TMDB/TVDB caller in the app.

Ordinary detail imports fetch at most five 50-row pages for characters/editions/contributions. Coverage records explicitly report incomplete results. Books with more than 250 editions can resume with `edition_page=6`, then the returned `next_edition_page`. This is bounded enrichment, not a provider database dump.

Existing movie/series writers now retain title origin/production countries, episode totals/runtimes and stable cast IDs. Legacy screens retain a cast preview; complete normalized credits are ingested by catalogue refresh. Copying show metadata retains descriptive fields without substituting TMDB season numbering for a TVDB-native show.

## Backend contract

All routes use current authenticated sessions. Identity bindings, manual corrections, performance bindings and legacy backfills require an administrator. Provider search and refresh endpoints additionally use the existing request limiter.

| Route | Behavior |
| --- | --- |
| `GET /catalogue/providers` | Attribution names, URLs and TMDB notice; no credentials |
| `GET /catalogue/search?provider=…&kind=…&q=…&page=1&limit=20` | Bounded provider search; source-qualified results |
| `POST /catalogue/refresh/{provider}/{kind}/{external_id}` | Fetch/cache/import a work; return local entity ID, status and coverage |
| Same refresh with `?edition_page=6` | Resume Hardcover editions in a separately cached 50-edition page |
| `GET /catalogue/resolve?namespace=…&external_id=…` | Resolve an existing mapping without similarity matching or provider fan-out |
| `GET /catalogue/entities/{id}` | Canonical fields; edition/release parent and specific fields in attributes |
| `GET /catalogue/entities/{id}/{section}?page=1&limit=50` | Paginated identities, credits, characters, performances, editions, releases, relationships, prices or source state; raw payloads/leases excluded |
| `POST /catalogue/entities/{id}/identities` | Admin-reviewed binding with reason; conflicts return 409 |
| `PATCH /catalogue/entities/{id}` | Admin correction of name/description/artwork; fields become protected |
| `POST /catalogue/performances` | Admin-reviewed credit/appearance/language binding |
| `DELETE /catalogue/performances/{id}` | Admin removal of a mistaken manual link; source entities and tracking remain intact |
| `POST /catalogue/backfill?media_cursor=0&show_cursor=0&limit=50` | Idempotent legacy identity and cached-credit import, next cursors, has_more and conflict IDs |
| `POST /catalogue/entities/{id}/steam-prices/{appid}?country=DE` | Verified Steam identity only; ITAD current and dated Steam store low |

A catalogue record does not authorize adding a game/book to existing movie/series tracking routes. The legacy `MediaType` enum is unchanged. Phase two must build a deliberate work-to-tracking domain boundary, not turn every edition/platform into a tracked title.

## Provider evidence and limitations

Official documentation, live schema/response checks and credential-free representative fixtures were independently checked on 2026-10-09. No earlier agent's unverified API assumptions were used as authority.

| Provider | Verified behavior | Limits and terms |
| --- | --- | --- |
| [TMDB](https://developer.themoviedb.org/reference/tv-series-aggregate-credits) | Movie credits and TV aggregate roles/jobs, companies, networks and external IDs; 550 movie and 1399 series details/search worked | Roles are labels, not global characters; aggregate credits do not imply every watched episode. [Attribution](https://developer.themoviedb.org/docs/faq) requires approved logo and notice; non-commercial developer access, commercial licensing separately |
| [TheTVDB v4](https://thetvdb.github.io/v4-api/) | Login, extended series 121361, `remoteIds`, companies, `peopleId`, `personImgURL`, title/performer association artwork; search worked | Existing formatter corrected to prefer `peopleId` and person portrait. Association IDs/art are retained as source evidence, not merged fictional entities. [Access models](https://thetvdb.com/api-information) depend on the application's key/subscriber model; universal quota not asserted |
| [IGDB](https://api-docs.igdb.com/) | Twitch client-credentials exchange, Steam source/UID/URL, companies, releases, collections, typed game links, stable characters and mug shots; game 1942 returned 25 characters, some with artwork | Documented 4 requests/second and eight concurrent requests; non-commercial use under Twitch agreement, commercial partnership separately. Company coverage does not supply a comprehensive game staff/voice cast |
| [Hardcover](https://docs.hardcover.app/api/getting-started/) | Bearer GraphQL, native search, public books, canonical aliases, editions/ISBNs/publishers, work and edition contributions, series positions and independent characters | Documented 60 requests/minute, depth three, 30-second query timeout, disabled substring/regex filters and expiring tokens. Book 328491 returned 47 characters, but sampled character `image_id`s were null; artwork availability is not promised. `image` is an object; use `cached_image` or selected image fields. Backend-only public metadata access does not establish unrestricted redistribution rights |
| [RAWG](https://rawg.io/apidocs) | Query API key, game/search, developers/publishers/platforms and store links; exact Steam hostname/app path maps game 3328 to app 292030 | The indexed third-party Context7 guide incorrectly suggested token-header authentication. Current [official terms](https://rawg.io/tos_api) specify the free 20,000/month tier with attribution/link on every page using RAWG data and audience limits. No full staff/fictional character feed is ingested |
| [IsThereAnyDeal](https://docs.isthereanydeal.com/) | API-key Steam AppID lookup to UUID; prices v3 and storelow v2 accept UUID arrays; verified Steam shop 61. Sample DE current and historical low include currency/minor units; low timestamp supplied | Only Steam shop results are normalized. `historyLow.all` is not substituted for a Steam low. Country is explicit; stored provider URL/affiliate tags remain unchanged. Documented verified-account default 1,000 requests/five minutes, account limits may vary. Link/mention attribution and private/public usage conditions apply. No purchase/review/general pricing system |

The API exposes attribution metadata for consumers. Any phase-two UI displaying provider data must implement its provider's display/attribution requirements; this backend phase does not claim a frontend attribution redesign.

## Other providers, deferred

| Provider | Possible later use | Boundary |
| --- | --- | --- |
| [Steam Web API](https://partner.steamgames.com/doc/webapi) | Ownership/playtime or additional app information | AppIDs are retained now; no Steam credential or ownership/session integration is required here |
| IsThereAnyDeal | Additional shops, bundles or history | Only the authorized Steam current/store-low slice is implemented |
| [Open Library](https://openlibrary.org/developers/api) | Book-work/edition/author and cover enrichment | Preserve work/edition IDs; no title-based automatic merge or bulk harvest |
| [Google Books](https://developers.google.com/books/docs/v1/using) | ISBN/volume descriptions or cover fallback | Volume IDs need their own namespace; no credentials or quota assumptions added |
| [VNDB Kana](https://api.vndb.org/kana) | Visual novel staff, characters and artwork | Separate access/content/image rules need validation before integration |
| [Wikidata](https://www.wikidata.org/wiki/Wikidata:Data_access) | Reviewed cross-references | Item IDs and Commons image rights are separate concerns; similarity is not identity evidence |

## Validation and deployment

`mt036` added 13 tables, foreign keys, uniqueness/indexes, typed-reference validation triggers and same-work performance constraints. Additive `mt037` attaches Steam prices directly to their verified namespace/work/AppID mapping and edition credits to the edition/work pair. Parent mapping deletion/reassignment and edition movement cannot invalidate retained relations. Performer insert/update locks and credit mutation guards prevent a linked actor/narrator credit becoming a non-performer, including concurrent writes. Existing inconsistent references stop migration for review; no cleanup deletes data. Administrators can explicitly remove a mistaken performance link before a guarded credit correction, without deleting the contributor, character or tracking facts. Shared-identity deletion is deliberately restrictive; downgrade refuses rather than silently discarding populated canonical data.

Tests use private schemas in an explicitly named disposable PostgreSQL database. They cover empty and populated parent schemas, full empty-database Alembic upgrade, namespace collisions, conflicts, repeated/partial imports, edition ISBNs, multi-role credits, last-good behavior, claims, permissions, Steam-only low dates, provider failures/pagination, and legacy history/list/rating/progress compatibility. The final 153-test backend selection and four provider-installer tests passed. A fresh disposable database upgraded through the complete Alembic chain to `mt036`. Live imports through the actual PostgreSQL-backed refresh path produced 1,878 entities, 1,651 credits, 300 book editions and 72 character appearances in a disposable schema; IGDB/RAWG and TMDB/TVDB cross-references resolved to shared roots. Hardcover page six and dated Steam-only pricing also succeeded. These sample counts are validation evidence, not user coverage.

Beta deployment of commit `6a7d808598f6e6b50d9edf1e9070ef16b4762dcd` succeeded through [Preview Deploy](https://github.com/antipixelhd/AnyList/actions/runs/37966484246), with a pre-migration backup and forward upgrade from `mt035` to `mt036`. Provider defaults were installed through the reviewed `ops/cloud/install-provider-defaults.py` helper using private stdin; keys are never committed, seeded into the database or returned by browser APIs. Preview backends passed health checks.

Post-deployment checks confirmed beta's database revision `mt036`, healthy HTTP service, anonymous rejection and authenticated catalogue access. Live API refreshes passed for IGDB, RAWG, Hardcover, TMDB movie/series and TVDB, plus Steam current and dated store-low pricing. Verified cross-references reused shared game/series roots. The bounded legacy backfill completed with 111 media bridges and 56 show bridges, zero identity conflicts and unchanged legacy media/show/watch-event row counts. This validates compatibility and import behavior, not every user's metadata completeness.

Phase two can join tracked legacy IDs through the bridge, deduplicate `(contributor_id, work_id)` before aggregating title counts/runtime/scores, and paginate independent character appearances. Daily statistics state/snapshots, privacy-aware publication and frontend work still need their own implementation.

## Goal completion audit, 2026-10-09

The follow-up audit closed gaps in partial relation retention, placeholder naming, IGDB edition aliases, unexpected failure/backoff state, stale price status, undated-low replacement and provider memory bounds. Steam lows without dates do not replace an already verified low/date pair. FK/trigger protections were strengthened in `mt037` without changing tracking facts.

The final selected 162 backend tests passed, including both concurrent performer-write orders, mapping/edition mutation rejection, empty/populated migration paths and transactional rollback when existing references are invalid. The complete empty-database Alembic chain upgraded to `mt037`. Live imports passed again through the PostgreSQL-backed provider lanes, including Hardcover edition page six and Steam-only pricing. Real IGDB edition 119402 resolved to parent work 1942 while retaining its distinct release identity.

Audit fixes in commit `ccd1719922ab5ca16ab38f99eda8c480dff92067` deployed successfully through [Preview Deploy](https://github.com/antipixelhd/AnyList/actions/runs/37984113073). The deployment created a pre-migration beta backup and upgraded `mt036` to `mt037`. Post-deployment checks confirmed `mt037`, healthy HTTP service, anonymous rejection, authenticated catalogue access and the administrator performance correction route. All six metadata provider lanes and Steam pricing passed; a fresh IGDB edition import resolved 119402 to parent work 1942 while retaining its distinct release. Repeated bounded backfill completed with 111 media bridges, 56 show bridges, zero conflicts and unchanged legacy media/show/watch-event row counts. Statistics APIs/jobs/frontend, game/book tracking and comprehensive user-coverage auditing remain phase two.
