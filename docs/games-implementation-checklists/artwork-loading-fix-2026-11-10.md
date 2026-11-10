# Game artwork viewer loading fix — 2026-11-10

Code verified on authenticated beta: `46a77c09`. Baseline: [artwork-loading-measurements-2026-11-10.md](artwork-loading-measurements-2026-11-10.md). Final aggregate readings: [artwork-loading-fix-2026-11-10.json](artwork-loading-fix-2026-11-10.json).

## Behavior

The selected full-size image gets a head start before the remaining downloads are admitted. The complete gallery still preloads on its retained slide elements, beginning with both neighboring slides; selecting or exposing another image bypasses the head start immediately. Existing in-flight requests are promoted without restarting, and decoded slides remain usable on return.

The viewer also preloads its existing small strip images. Pending slides can display the matching strip or grid preview until the full-size image is decoded. This reuses the exact preview URL, accepts previews already loading, preserves the best available preview, and clears it once the full image is ready. Dialogs move outside the detail root on opening, so each slide references its strip thumbnail directly. Pending-image identity uses `src` while `currentSrc` is still empty.

The artwork grid continues assigning sources only on exposure. The IGDB 720p preview / 1080p viewer resolution limits are unchanged. There is no new game image-count limit. Square and portrait game artwork remain in the viewer: Shenmue now retains all 32 rendered entries and WarioWare all 36, rather than downloading and then removing 14/15 images. The existing landscape-only rule remains for other media.

## Live-provider verification

Isolated local Chrome 157 against the real authenticated beta pages and IGDB CDN. Desktop was 1440×1000 with normal CPU/network. Mobile simulation was 390×844 with 4× CPU slowdown: fast used 100 Mbps / 20 ms configured latency; constrained used approximately 4 Mbps / 100 ms. These are beta development-server lab samples, not physical-phone measurements or production percentiles.

Cold visits cleared the browser HTTP cache. Warm visits retained normal caching. Throttling began after the page settled because throttling the complete streamed document produced an unrelated Chromium parsing stall; these measurements isolate viewer work. No builds or test suites ran alongside the final matrix. Immediate cases began switching as soon as the opening click returned, then advanced ten images with nominal 100 ms pauses and traversed every retained image after loading. The distant-jump case selected Artwork 36 and Artwork 2 before traversal.

| Case | Full gallery ready | First switch after opening | Visible navigation placeholders |
| --- | --- | --- | --- |
| Shenmue desktop, cold, 32 images | 405 ms | 27 ms | 0 |
| Shenmue desktop, warm | 15 ms | 46 ms | 0 |
| WarioWare fast mobile simulation, cold, 36 images | 1,057 ms | 156 ms | 0 |
| WarioWare fast mobile simulation, repeated cold | 1,011 ms | 119 ms | 0 |
| WarioWare fast mobile simulation, warm | 8 ms | 243 ms | 0 |
| WarioWare fast mobile simulation, distant jumps, cold | 1,009 ms | 1,126 ms | 0 |
| WarioWare constrained mobile simulation, cold | 12,513 ms | After loading | 0 |
| WarioWare constrained mobile simulation, warm | 8 ms | After loading | 0 |

The constrained case opened the same valid landscape image as the baseline. It reached decoded readiness in approximately **1.7 seconds**, versus **11.2 seconds** before. Complete-gallery download remains limited by roughly 5 MiB of full-size artwork at that bandwidth; it took 12.5 seconds, compared with 12.0 seconds in the baseline. The priority change favors the selected image while retaining complete preloading. Early traversal on constrained bandwidth was not used to establish the fast-connection usability result.

Full-size transfer remains approximately 3.76 MiB for Shenmue and 4.99 MiB for WarioWare. Warm full-size transfers were zero bytes. No final image requests failed. Existing previews covered early fast-connection switches before full-size decoding completed. Per-frame sampling checked every exposed slide, including outgoing/intermediate slides during carousel motion, rather than only the selected image.

The 4× CPU simulation still produced loading/opening work and occasional long frame intervals; fast cold keyboard interactions peaked at 168–192 ms, versus 72 ms warm. The distant-strip jump included a 623 ms frame gap before the first committed jump. These runs establish placeholder-free switching in the measured fast cases, not perfectly uniform frame delivery on every device. Native physical-phone gestures and actual browser memory pressure were not measured. Retaining all game artwork increases the decoded-pixel estimate to about 149 MiB for Shenmue and 224 MiB for WarioWare; this is width×height×4, not a measured browser-memory allocation.

Repeated Shenmue → WarioWare → browser-back-to-Shenmue entry switches reached the matching selected full-size images and produced no browser errors. Intermediate iterations that exposed preview races are excluded from the final aggregate readings; those races have focused coverage.

## Checks

- 37 focused viewer, carousel, gallery and completeness tests pass. Coverage includes delayed bulk admission, navigation during the head start, teardown/group invalidation, failures, retained decodes, preview promotion/replacement, explicit strip preloading, pending URL identity and portaled-dialog previews.
- Full frontend suite: 934/941 pass. Seven failures reproduce on unchanged HEAD: four contributor-filter fixtures and three Steam-import fixtures. The unchanged baseline had an additional invalid gallery-test fixture, corrected while extending this coverage.
- Astro check: zero errors, zero warnings, 41 existing hints. Production build passes. No database migration is required.
