# Mobile first-time launch QA

- Date: 2026-10-20
- Origin: http://127.0.0.1:7340/
- Isolated real Chromium session: qa-mobile, closed at completion.
- Viewport: exactly 375 × 812 CSS pixels. Observed document scrollWidth 375; no horizontal overflow on welcome/login.
- Outcome: blocked before account creation. First tracked title not reached.
- Findings: 1 high-priority launch blocker. No app source inspected or modified; no account created.

## ISSUE-MOBILE-001 — New visitor reaches sign-in with no way to create or request an account

Severity: high (blocks the requested first-time success journey).

As a first-time mobile visitor I can read the value proposition, but the only form is Email / Password / Sign In. There is no Create account, request access, or explanation of how to obtain credentials. Dismissing the install prompt does not reveal a registration action. Both available branding links lead back to the same sign-in experience.

1. Open `/` in a fresh browser. It redirects to `/login?next=%2F` and displays only a sign-in form. [Initial viewport](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/01-start.png), [full page](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/02-login-full.png).
2. Dismiss the installation prompt; no signup path appears. [After dismissal](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/03-dismissed-install.png).
3. Retry discovery through the footer AnyList link. [Before click](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/04-no-signup-before.png), [same sign-in after click](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/05-no-signup-after-footer.png).

[Retry video](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/videos/no-registration-path.webm).

Expected: a discoverable way to create/request an account, or explicit access instructions if this deployment is invitation-only. This is an entry-path blocker, not a claim that a registration form fails validation.

## Other observed details

- The welcome page fits the narrow viewport. Sign In is 293 × 46 CSS pixels.
- The install prompt appears before any successful app interaction. At this viewport its top is about y=651 while Sign In ends about y=654; only the bottom few pixels overlap. The label remains visible. I could dismiss the prompt successfully. This is not counted as a separate mobile CTA obstruction.
- Install and password visibility controls measured 57 × 28 and 28 × 28 pixels respectively; I did not observe failed taps, so these are measurements, not additional proven failures.
- No JavaScript page errors returned from the browser error log on welcome/login.

## Slow 3G verification and limitations — resolved on repeat

Before first navigation, a separate persistent CDP connection attached to the page target and successfully received `{}` for `Network.emulateNetworkConditions` with `offline:false`, `latency:400`, `downloadThroughput:50000`, `uploadThroughput:50000`, and `connectionType:cellular3g`. The helper connection stayed open throughout this journey.

The initial attempt did **not establish effective Slow 3G**: the helper selected the first page target, which was a separate `chrome://newtab` page instead of agent-browser's active page. Earlier screenshots 01–05 are viewport evidence only.

I repeated the journey in a fresh browser at 375 × 812 with the helper attached to the actual active `about:blank` page before navigation. `Network.enable`, `Network.emulateNetworkConditions`, `Network.overrideNetworkState`, and `Network.emulateNetworkConditionsByRule` all succeeded. The rule matched all URLs with the same 400 ms / 50,000 bytes-per-second settings. The CDP connection remained open throughout.

Effective throttling was verified with an actual uncached same-origin fetch of the visible login URL: HTTP 200, 209,677 bytes, 4,627.3 ms. A repeat returned 4,634.3 ms. These measurements agree with 400 ms latency plus approximately 4.2 seconds of transfer at 50 KB/s. The page additionally reported `effectiveType=3g`, `rtt=450`, `downlink=0.4`. [Machine-readable verification](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/throttle-verification.json).

[Throttled first landing](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/06-slow3g-cold-loading.png) and [settled throttled blocker](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/mobile/screenshots/07-slow3g-final-blocker.png) reproduce the same missing account-creation path. The development page's load event completed at about 30.34 seconds, although the screenshot already showed the usable login form before that. This is a local development build observation, not a production load-time conclusion or an additional issue. Native browser cache was kept enabled; the explicit verification fetches alone used `cache:no-store`. The second browser session and helper were closed.

Desktop Chromium viewport emulation does not open a real mobile software keyboard. Keyboard occlusion, physical touch accuracy, device CPU behavior, and the authenticated first-success journey remain untested because account access blocks progress.
