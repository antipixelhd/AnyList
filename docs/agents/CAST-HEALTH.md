# Cast identity health — 2026-10-13

Beta's Tenet work (catalogue 55) stored Dimple Kapadia twice: TMDB person
78921 / catalogue 10458 and TVDB person 8136457 / catalogue 16964. TVDB's
extended person response has no remote IDs, portrait, birth information, or
biography. Its Priya character credit does have artwork. The authoritative
identity backfill correctly declined to merge the people; the detail projection
incorrectly displayed both provider casts without applying cast priority.

Screen details and actor statistics now share a cast projection. A complete
TMDB movie cast or series aggregate cast takes priority; TVDB remains fallback
when complete TMDB coverage is unavailable. Verified aliases retain their roles.
Unique exact actor-name and character-label matches within a single catalogue
work associate character artwork with the primary card, without binding a person
identity or changing source credits. Empty roles and ambiguous matches cannot
associate artwork. Statistics contract 9 refreshes previously cached results.

Read-only beta audit before deployment:

| Measure | Stored / old projection | New projection |
| --- | ---: | ---: |
| Same-name cast candidate groups | 522 | 7 |
| Titles containing those groups | 63 | 5 |
| Cast source credits examined | 13,426 | 13,426 |
| TVDB people without TMDB identities | 664 | 664 |
| Character artwork associated within a title | — | 84 |

170 fallback source credits are omitted from the preferred projection; no source
records are deleted. Tenet produces one Dimple Kapadia card linking to catalogue
10458, with the TMDB portrait and TVDB Priya image.

The seven remaining groups are all separate TMDB identities: Sergey Safronov
(Stranger Things), Lee Suk, John Choi, Kang Hyeon-woo (Squid Game), John Lister
(Prison Break), Tom Cox (Silo), and Kevin Healey (Reacher). Names alone do not
establish that these are the same person. These require upstream identity review;
they are not the TMDB/TVDB priority defect addressed here.

Run `python -m scripts.audit_screen_cast` from the backend with the intended
application database configured. This command reads catalogue metadata only,
performs no provider calls, and never modifies the database. Candidate counts
are diagnostic signals, not authorization to merge person records.
