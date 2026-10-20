# Skeptical first-time user — launch QA

Target: http://127.0.0.1:7340/

Date: 2026-10-20

Browser: isolated real Chromium session `qa-skeptic`, desktop 1264 × 758.

First success: one title visibly added to Planning / tracked list.

## Outcome

Natural outcome: abandon before entering personal details. The app claims “YOUR PRIVATE MEDIA JOURNAL” but offers no visible privacy/terms information, and I cannot establish how to get an account.

Continued QA outcome: blocked at sign in; there is no signup or invite/request-access control. No account created, no credentials submitted, and first title success not reached. The test did not invent routes or use an existing private account. Browser session closed.

## Findings

### SKEPTIC-001 — No visible way for a new user to obtain access
Severity: High for public first-time onboarding; deployment-dependent if this instance is intentionally invite-only.

1. Open `/`; browser redirects to `/login?next=%2F`.
2. Inspect entire welcome page: the only form actions are Email, Password, password visibility, and Sign In. There is no Create account, invitation explanation, or request-access path.
3. Follow footer “AnyList” to `/`; it returns to the same sign-in page. Reopen `/` once more: same result.

Expected: a clear new-user access path, or a concise instruction explaining where credentials/invitation come from. Actual: a new user has no next action toward first success.

Evidence: [initial full-page screenshot](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/skeptic/screenshots/01-welcome-no-trust-links.png), [after footer navigation](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/skeptic/screenshots/02-returned-to-signin.png), [repeat visit / final blocker](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/skeptic/screenshots/03-final-signin-blocker.png).

Repro video: N/A — absence is visible on load and captured in full-page screenshots.

### SKEPTIC-002 — Privacy and terms cannot be inspected before providing credentials
Severity: Medium; trust blocker for this persona.

1. Open `/` and inspect header, welcome text, form, and footer.
2. Look for privacy, terms, or operator/data handling information before signing up.
3. Only two brand links are present; both lead back to sign in. Repeat visit confirms no legal/trust links.

The page calls this a private journal and describes optional connected-account sync, yet the visitor cannot find privacy/terms information to assess what those claims mean. Pricing/cost information is also absent; because this is explicitly self-hosted, that is an information gap rather than evidence of hidden charges or a broken pricing page. One trust-information finding, not separate issues for every missing label.

Expected: accessible privacy/terms and sufficient operator/access context. Actual: no visible route to inspect them. Persona would stop before sharing an email or account details.

Evidence: [welcome page and complete footer](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/skeptic/screenshots/03-final-signin-blocker.png).

Repro video: N/A — static missing content.

## Additional observation, not a confirmed ranked defect

An “Install AnyList” prompt appeared before sign-in and visually covered much of the Sign In button in the initial screenshot. It was gone after footer navigation and did not reappear on the repeat visit. Dismiss was available. Since I could not reproduce it consistently, do not count it as a confirmed defect from this persona. [Initial prompt evidence](C:/Users/joshu/Documents/4_Stremio-SelfHost/media-tracker/qa-output/launch-2026-10-20/skeptic/screenshots/01-welcome-no-trust-links.png).

## Limits

No authenticated areas or title workflows tested because the UI exposed no account-creation path. No console errors observed; only development connection messages. Findings describe the local launch instance, not a verified production configuration or policy requirement. No app code/config inspected or changed.
