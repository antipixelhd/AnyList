# Domain glossary

| Term | Meaning in this project |
| --- | --- |
| Catalog entry | Stable local identity for a title, mapped to relevant external provider IDs. |
| Provider | External catalog, rating source, or connected tracking service; its capabilities must be identified rather than assumed. |
| Connection | A user's optional linked external account, with supported sync fields and directions. |
| List entry | A user's tracked movie or whole TV series, with status, progress, rating behavior, and favorite state. |
| Status | Planning, Watching, Paused, Dropped, or Completed for movies/TV. Separate from granular watch history. |
| Completed | Caught up through available episodes, or a manually selected status; not an assertion about unreleased episodes. |
| Watch state | Whether particular released episodes/seasons/movies have been watched; distinct from playback position and list status. |
| Playback progress | Position within an episode/movie, as distinct from which episodes have been completed. |
| Manual show rating | User-entered whole-series score, independent of season scores. |
| Season rating | Explicit user-entered score for one season; never generated just because the show is rated. |
| Calculated show rating | Equal-weight average of explicitly rated regular seasons; specials/unrated seasons excluded. No rated seasons means unrated. |
| Effective show rating | The displayed manual score or calculated average according to that show's chosen mode. |
| Unrated | No meaningful rating; zero in the editor means unrated. Never display or average it as a score. Meaningful scores are 0.5–10. |
| External score | A named source's score, such as IMDb, RT critics, or RT audience. Preserve each source's identity and scale. |
| Friends / followed users | People the current user follows, regardless of whether they follow back. |
| Friends' average | Average of eligible effective ratings from followed users, excluding private users' data. |
| Public profile | Profile eligible to be viewed by other members; visitor access additionally requires the instance setting. |
| Private profile | Personal list/rating/activity hidden from other users and their aggregates. |
| Initial import | Loading existing remote data; does not flood the activity feed with historical events. |
| Sync conflict | Incompatible changes whose precedence cannot be determined safely from available source information. |
| Cumulative progress | A later watched episode/season implies preceding episodes were watched; keep underlying episode records. Exact order/specials rules pending. |
| Entry deletion | Confirmed removal of all personal state for a main list entry, propagated where supported; not deletion of the shared title. |
| Provider removal | Removal from a connected service's collection, interpreted according to its limited capabilities; not automatically an entry deletion. |
| Notifications | Private review area for unconfirmed sync interpretations, with confirmation, status correction, and rating actions. Separate from the social feed. |
| Pending connection update | Operational per-title delivery state for a local change intended for connected services. One visible item may contain multiple provider outcomes and clears only after every applicable delivery succeeds. |
| Streaming library | Membership mirrored across Stremio/Nuvio accounts independently of main tracked-list entries and statuses. |
| Plan to Watch | User-facing Planning group for not-yet-started titles; distinct from mere streaming-library membership. |
| Deletion marker | Minimal internal title/time/pending-acknowledgment record preventing stale reimport after personal entry data is cleared. |
| Auto-confirm | Per-user, default-off confirmation of ordinary inferred Paused/Dropped changes; never auto-resolves conflicts, uncertain removals, first merges, or destructive deletions. |
| Sync baseline | Last successfully established per-connection state used to detect changes; initial absence is not a removal event. |
| Detached season | Future feature exposing a season as a standalone list entry; outside release one. |

This glossary expresses the intended product, not a claim that Scrob's current database already implements each concept.

