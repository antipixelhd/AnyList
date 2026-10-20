# Impatient first-time user QA

- Target: http://127.0.0.1:7340/
- Date: 2026-10-20
- Persona: skim, first obvious CTA, quit at first genuine blocker.
- Session: qa-impatient, fresh isolated desktop Chromium, default viewport 1264 x 569.
- First success target: add one title to Planning or another tracked state.
- Outcome: blocked before account creation; no account created, no first title tracked. Session closed.
- Counts: 1 blocker (P1), 1 secondary observed annoyance (P2, unverified recurrence).

## ISSUE-001 — No visible way for a first-time user to get an account

Severity: P1 for a public launch onboarding flow. If this deployment is deliberately invite/admin-only, the issue is missing access instructions rather than a missing signup implementation.

Observed: Opening / redirects to /login?next=%2F. The welcome page describes benefits and presents only Email, Password and Sign In. The footer contains only AnyList. No create-account, request-access, invitation instructions or demo entry is visible. With no preexisting account, this first-time user cannot begin.

Reproduction:
1. Open http://127.0.0.1:7340/ in a fresh session. See screenshots/01-start.png.
2. Skim the welcome text and click the visible primary Sign In CTA with empty fields. The browser correctly reports Please fill out this field. See screenshots/04-no-account-path.png.
3. Inspect the entry page including footer: there is no visible account-acquisition path. See screenshots/05-full-entry.png.
4. Quit because no credentials exist and the UI offers no next action.

Expected: The entry page provides a visible route to obtain access, or tells new users how to obtain an account from their host.

Impact: A first-time launch visitor cannot reach the app's first useful action. The required-field validation is expected browser behavior and is not itself a defect.

Evidence absolute paths:
- C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/impatient/screenshots/01-start.png
- C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/impatient/screenshots/04-no-account-path.png
- C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/impatient/screenshots/05-full-entry.png
- C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/impatient/videos/01-first-click.webm

Video limitation: Includes automation delays and one command-level mis-target before the actual Sign In click; the final screenshot and page snapshot establish the observation. No server request was triggered by the empty form.

## Secondary observation — Install prompt obscures the primary entry CTA

Severity: P2 annoyance; observed once, recurrence not verified because persona stops at the onboarding blocker.

At the default 1264 x 569 viewport, the Install AnyList toast overlays most of Sign In, including its label, during initial page arrival. It exposes its own Install and dismiss buttons. The prompt disappeared before the later primary-button interaction, so no claim is made that it permanently blocks submission.

Static evidence: C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/impatient/screenshots/01-start.png

## Limits

No app source/configuration, hidden routes, existing private accounts or backend APIs inspected. No app files modified. No account could be created from available UI. Console inspection at wrap-up showed only development connection messages; browser error list was empty. This run establishes entry-page first-time usability only, not authenticated flows or production behavior.
