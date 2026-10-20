# Breaker first-time launch QA

Date: 2026-10-20. Target: http://127.0.0.1:7340/. Session: qa-breaker, fresh isolated real Chromium, default desktop viewport. No app source/config read; UI-only exploration.

Outcome: First success blocked before account creation or adding a title. Two findings: one high-severity onboarding blocker and one medium first-visit obstruction. No actual validation, double-click, or back-navigation defect established in exposed UI.

## ISSUE-B1 — No visible way for a new user to obtain an account

Severity: High for first-time launch conversion. Reproduced by opening / twice and following both visible brand links. This may be an intentionally private/self-hosted instance, but the page does not explain how to request access, get invited, or contact its owner.

1. Open http://127.0.0.1:7340/. Redirect lands at /login?next=%2F, showing welcome pitch plus Sign in, Email, Password, Sign In. [Initial view](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/01-initial.png).
2. Dismiss install prompt; inspect entire page and footer. No sign-up, invitation, request-access, help, or recovery control is visible. [Returned sign-in view](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/03-return-signin.png).
3. Double-click top AnyList brand link. Remains on sign-in page. [Header loop](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/04-header-brand-loop.png).
4. Scroll down; inspect footer, then click its AnyList link. Remains on sign-in page. [Footer](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/05-footer-no-signup.png).
5. Stop: no legitimate next step toward first title success. [Outcome](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/06-outcome-blocked.png).

[Repro video](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/videos/onboarding-blocked.webm). Suggested resolution: expose the intended account-access path (sign up, invitation, or request access) on welcome/sign-in.

## ISSUE-B2 — First-visit install banner covers Sign In

Severity: Medium. Visible on first load: bottom-right Install AnyList banner overlays much of blue Sign In control and hides its label. User must discover tiny Dismiss icon before normal sign-in. Independently observed by this fresh persona; a same-session reload after dismissal does not reproduce, consistent with persisted dismissal.

Evidence: [Annotated first-load screenshot](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/01-initial.png). Static issue, video N/A. Suggested resolution: position/defer install prompt so it cannot obscure the first essential action; preferably offer it after first success.

## Breaker checks and limits

- Pasted harmless junk `junk<>not-an-email` in Email and junk in Password; double-clicked Sign In. Native validation correctly rejected malformed email: Please include an '@' in the email address. No crash or duplicate visible action. [Expected validation](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/breaker/screenshots/02-junk-validation.png).
- Browser Back during the partially completed sign-in returned to about:blank, the session's previous page. This is normal initial navigation, not a defect. Returning to / again showed sign-in.
- Double-clicked header brand link; no crash. Footer link also returned to sign-in.
- No actual credential guessing or login attempt with a syntactically valid unknown account. Signup was not exposed, so no disposable account could be created legitimately.
- Browser errors empty; console contained only Vite connection debug lines.
- Protected in-app flows, title success, authenticated forms, and mid-flow back/duplicate-write resilience remain untested due to gate. No hidden-route guessing or auth bypass.
