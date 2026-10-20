# First-time launch QA: literal-language user

Date: 2026-10-20
Target: http://127.0.0.1:7340/
Persona: Non-native English speaker who follows only literal UI instructions.
Session: qa-literal (fresh isolated Chromium session)
Viewport: default desktop, screenshot 1264 x 569.

## Outcome

Blocked before first success. Opening / redirects to /login?next=%2F. The page says Sign in and provides Email, Password and Sign In. It never offers Create account, Request access, an invitation explanation, or a way to obtain the required credentials. I did not invent credentials, guess hidden routes, or use an existing account. No title was tracked. This is a launch access/onboarding blocker for this unauthenticated local environment; invite-only intent would change the appropriate remedy, but the page does not explain that intent.

## Findings

### LITERAL-001 — No literal next step for someone without an account
Severity: High / first-success blocker
Observed twice: initial visit and reopen of /.

1. Open http://127.0.0.1:7340/ in a fresh session. Redirect reaches sign-in page.
2. Read all rendered copy and scroll to the footer. The only account action is Sign In; the header and footer only show AnyList branding.
3. Reopen /. The same sign-in-only page remains.
4. Dismiss the install prompt. The unobscured form still has no account creation or access instructions.

Expected: a first-time visitor can follow an explicit account/access action, or learns how to obtain access.
Actual: existing credentials are required without any stated way to obtain them.
Evidence: screenshots/02-no-account-path.png; screenshots/03-retry-outcome.png; screenshots/04-outcome-prompt-dismissed.png.
Repro video: N/A (visible-on-load/static missing access path).
Suggested fix: expose the intended first-time access path, or clearly explain invitation/admin access if public signup is intentionally unavailable.

### LITERAL-002 — Install prompt obscures the Sign In label
Severity: Medium
Observed independently on fresh session and again after reopen before dismissal.

At the default desktop viewport the bottom-right Install AnyList prompt overlaps the sign-in form's primary blue button. Most of the button and its Sign In label are behind the prompt. This leaves Install as the most legible button near the form although the account form is the intended next action. I did not infer the installation behavior or click Install. Dismiss removes the overlapping prompt.

Evidence: screenshots/01-start.png (annotated: button 7 under the install prompt); screenshots/03-retry-outcome.png; screenshots/04-outcome-prompt-dismissed.png (unobscured comparison).
Repro video: N/A (visible on initial render).
Suggested fix: defer the install prompt until after account access or position it away from primary form controls.

## Literal copy observations

Email, Password and Sign In are familiar, literal labels. No translation-specific comprehension issue was manufactured. The blocker is the missing instruction for obtaining access, not English fluency. Marketing copy such as half-points or reconcile was not needed for the available account action, so it was not counted as a separate issue.

## Checks and limits

Browser errors command returned no errors. No application source/configuration was read. No account or data was created. Authenticated features and title tracking were inaccessible and untested. Session closed after recording evidence.
