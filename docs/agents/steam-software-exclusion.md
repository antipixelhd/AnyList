# Steam software exclusion

The importer reads Steam's public software-only `IStoreService/GetAppList/v1`
feed using exact app IDs, all continuation pages, and explicit false filters
for games, DLC, videos and hardware. The shared catalogue snapshot lasts one day;
failures retain positive evidence and defer retries for 15 minutes. The work runs
when an importer preparation/import/sync requests classification, with no new
scheduled worker. It avoids one store request per owned application.

Positive evidence is stored in the existing public app cache. Known software is
omitted from initial picker responses. Progress responses disable software found
after the picker opens, overriding saved selections, removing completion-date
controls and stopping its pending indicator. Server writes reject software before
retargeting, import or auto-add, even when an old canonical mapping exists.
Preparation and background achievement loading skip known software.

An absent app is not certified as a game: Steam's list covers available store
software, and normal complete canonical IGDB admission is still required.
No schema migration or deletion of existing user data is involved.

Verification on 2026-11-04:

- Steam's live feed returned 2,942 software app IDs, including Wallpaper Engine
  (431960), excluding Hades (1145360); no continuation was needed.
- Nine source/unit tests and nine frontend tests passed. Four disposable
  PostgreSQL regressions cover cache reuse/failure, picker updates, write guards
  and exclusion before IGDB. A focused background regression and three existing
  import/component/background tests passed. Ruff, Astro check and build passed.
- The built Connections page with a disposable backend showed Wallpaper Engine
  become disabled/unselected after delayed classification, reject clicks and
  disappear on reopening. Hades stayed selectable; mobile had no horizontal
  overflow or browser runtime errors.
- Existing `game_list` benchmark retained fingerprints, 50 returned rows of 400,
  36,260-byte payloads, 12 SQL reads and zero provider calls. First curated sample
  was 65.95 to 70.14 ms cold, 32.18 to 30.18 ms warm. This adjacent benchmark does
  not measure software-feed acquisition; the focused cache test verifies reuse.

Sol-loop: one round with `gpt-6.1-sol`, verdict APPROVE; no findings or code
changes requested. Tracked behavior diff stayed 85 insertions / 14 deletions,
excluding the separately reviewed new module/tests/docs. Usage was not exposed.
