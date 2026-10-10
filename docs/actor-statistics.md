# Actor statistics

Actors uses the canonical Movies/Series titles in the profile's list, including
planned titles. Count counts each title once per actor; Mean Score uses the
profile owner's ratings, excluding unrated titles. Time Watched includes all
recorded plays of those listed titles, including repeats. A series credit means
the actor appeared in that series; it does not imply every watched episode
featured them.

The client displays the top thirty actors by Count, Mean Score or Time Watched
in two columns on desktop and one on smaller screens. The snapshot retains the
union of those rankings (at most ninety actor groups). Each card has twelve
titles ordered by personal score, with unrated titles last. Genres also retain
twelve rated titles. Both carousels display four at once and advance four per
arrow step.

Actor names open the requested placeholder actor page. Poster links open the
existing title page. Hover labels include all supplied character names for the
actor/title pair. Multiple roles and duplicate provider credits do not increase
Count or watch time.

## Character artwork

[TheTVDB's Character schema](https://github.com/thetvdb/v4-api/blob/main/_autodocs/types.md)
distinguishes `image` (the character/role image) from `personImgURL` (the actor
portrait). Movies and series extended records supply these associations. Live
checks on 10 October 2026 returned twenty role images for twenty-one Tenet cast
members. Coverage varies, and provider uploads are not independently verified.

[TMDB credits](https://developer.themoviedb.org/reference/movie-credits) supply
role labels and actor portraits; they do not supply a character-specific image.
The Media/Characters switch uses TVDB role images where supplied and otherwise
keeps the title poster. Characters is disabled when the current scope has no
role images. Person portraits are never used as character artwork.

`catalogue_credits.character_image_url` stores artwork on the specific
title/person credit. A TVDB Character record remains a title/performer
association, not a global fictional-character identity. Movie cross-references
use IMDb remote-ID lookup; principal cast person cross-references use the
authoritative TMDB ID in TVDB people extended records. Names never establish
identity. Where both person identities already point to different canonical
entities, the existing identity-conflict protection remains in force. TMDB
cast is preferred on shared titles to prevent unverified duplicate actor cards.

The shared actor backfill examines at most twenty-five listed titles per batch,
with weekly revisit markers and the existing provider rate lanes, response
caches, retry state, and last-good snapshots. TVDB people enrichment is limited
to twelve principal cast members per title; all supplied cast credits are
retained. Statistics reads perform no provider calls. Credit updates invalidate
the shared statistics metadata revision. Migration `mt041` stores role artwork,
allows typed TVDB movie identities and adds credit invalidation triggers.

[AniList connections](https://docs.anilist.co/guide/graphql/connections) provide
character/voice-actor associations for anime. This pass uses the existing TVDB
credentials and does not introduce a second anime matching system.
