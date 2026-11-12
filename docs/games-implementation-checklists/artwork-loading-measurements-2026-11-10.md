# Game artwork loading measurements — 2026-11-10

Measured beta at `075ec989` with the existing authenticated game pages and live IGDB CDN images. No application behavior changed. Aggregate readings are in the adjacent JSON file; detailed browser captures and short-lived authentication remain ignored locally.

## Caps and actual gallery sizes

- IGDB grid previews use `t_720p`, full-screen artwork uses `t_1080p`, and the viewer strip uses `t_cover_small_2x`. The measured retained full-screen images fit within 1920×1080. These are resolution limits, not image-count or transfer-byte limits.
- Games retain the complete provider gallery; the 10-image limit used for other media does not apply to games. Among 3,038 stored games with nonempty artwork, the median count was 6, p95 was 18, and maximum was 40. Those stored counts include older normalization policies and are not a hard maximum.
- Shenmue rendered 32 grid entries; WarioWare: Get It Together! rendered 36. Before opening the viewer, only 2–5 grid image sources were assigned in these viewports. The grid did not download every preview upfront.
- Opening the viewer started all 32/36 full-screen images. During decoding, the viewer discarded 14/15 square or portrait entries, leaving 18/21. The grid retains those entries. This exposes a separate inconsistency between the complete game gallery and the viewer's landscape-only rejection in `prepareImage`.

## Readings

Desktop: 1440×1000, normal local CPU/network. Mobile simulation: 390×844, 4× CPU slowdown, 500,000 bytes/s download/upload (approximately 4 Mbps), 100 ms configured network latency. This is a desktop browser simulation, not physical-phone or production percentile data.

Cold runs cleared the browser HTTP cache before opening the actual route. Warm runs revisited the route with normal caching enabled. The existing page could preload a primary image before viewer opening; readings describe the real page behavior. Navigation traversed every retained slide with ArrowRight at a nominal 250 ms pause between commands, after loading settled.

| Case | Full-size requests / retained | Full-size transfer | Selected image ready | Retained gallery ready | Maximum navigation interaction |
| --- | --- | --- | --- | --- | --- |
| Shenmue, desktop cold | 32 / 18 | 3.76 MiB | 164 ms | 447 ms | 32 ms |
| Shenmue, desktop warm | 32 / 18 | 0 | 14 ms | 131 ms | 32 ms |
| WarioWare, simulated mobile cold, first grid item | 36 / 21 | 4.99 MiB | 10.35 s | 11.01 s | 56 ms |
| WarioWare, simulated mobile warm, first grid item | 36 / 21 | 0 | 341 ms | 341 ms | 48 ms |
| WarioWare, simulated mobile cold, valid landscape selected | 36 / 21 | 4.99 MiB | 11.19 s | 11.98 s | 48 ms |
| WarioWare, simulated mobile warm, valid landscape selected | 36 / 21 | 0 | 208 ms | 691 ms | 48 ms |

Full-size transfer excludes the grid and strip previews. Total image transfer during opening and the complete traversal was 4.01 MiB for cold desktop Shenmue and 5.17–5.69 MiB for cold mobile WarioWare. Warm full-size images transferred zero bytes. No image requests failed.

The cold WarioWare run starting at a valid landscape confirms that delayed presentation is not merely waiting for an invalid initial portrait to be removed. Fetching that same selected `t_1080p/ar4biu.jpg` alone, with a cleared cache and the same CPU/network throttling, completed in 440–551 ms in two controls (159,198-byte body). This isolated-image diagnostic supports bandwidth contention as a material contributor; it is not a measured application optimization or a prediction of an exact speedup.

## What is and is not a problem

- **Confirmed cold loading problem on constrained bandwidth:** even with 1080p caps, roughly 5 MiB of concurrent full-size downloads kept a valid selected image in its loading state for over 11 seconds. The selected slide already has high fetch priority; setting that flag alone is not a proposed fix.
- **Confirmed waste from post-download rejection:** cold WarioWare downloaded about 2.07 MiB for 15 images that the viewer then removed. Shenmue discarded 14 images totaling about 1.49 MiB. Resolve the grid/viewer eligibility inconsistency while preserving intended game artwork before choosing any exclusion policy.
- **Small opening hitch under CPU throttling:** WarioWare opening interactions were 160–200 ms, with 123–141 ms opening long tasks. Warm opening frame intervals reached 267–334 ms in the two samples. These readings include viewer construction, decoding completion/reconciliation and rendering; this measurement does not isolate their individual CPU costs.
- **No sustained navigation slowdown observed:** after loading settled, every measured navigation selection was ready, maximum interaction durations were 32–56 ms, navigation frame intervals stayed at or below 33.4 ms, and no navigation long tasks were recorded. These runs do not establish behavior during immediate swipes before preloading completes.
- Retained decoded pixel storage is approximately 103 MiB for Shenmue and 159 MiB for WarioWare using width×height×4. These are estimates, not measured browser memory or proof of memory pressure. No out-of-memory behavior was observed; physical low-memory phones were not tested.

## Direction for a subsequent change

Preserve complete-gallery preloading and the existing resolution caps. Give the selected image a head start before admitting the rest of the full-size downloads, then continue preloading so traversal remains ready. Address post-download removal consistently with the intended complete game gallery. Measure the same cold/warm cases again, including immediate navigation, before claiming improvement. There is no evidence here to justify reducing the artwork cap or removing preloading altogether.
