# AnyList launch QA — five first-time personas

Date: 20 October 2026. Target: `http://127.0.0.1:7340/`, local development instance.

## Launch assessment

**Blocked for visitors who do not already have an account.** All five independent browser personas reached a sign-in-only entry page. None could reach the defined first success: one title visibly added to Planning or another tracked list. This verifies the local instance's first-time entry barrier; it does not establish production configuration or authenticated feature quality.

Five separate subagents used fresh, isolated real Chromium browser sessions, in batches to fit the available concurrency. Each followed its assigned persona using visible UI. No hidden registration route, existing account, backend API, or source-code inspection was used to bypass the journey. These are simulated personas, not a study of five human participants.

## Ranked findings

Counts represent personas that actually encountered the issue, not extrapolated prevalence. Findings are sorted by count, with severity shown separately.

| Rank | Finding | Encountered | Personas | Severity |
| --- | --- | --- | --- | --- |
| 1 | No visible way to obtain an account or access instructions | **5/5** | Impatient, skeptic, mobile, literal-language, breaker | High: first-success blocker |
| 2 | First-visit install prompt obscures the desktop Sign In button | **4/5** | Impatient, skeptic, literal-language, breaker | Medium: primary-action obstruction |
| 3 | Privacy and terms cannot be inspected before providing credentials | **1/5** | Skeptic | Medium: trust blocker for this persona |

### 1. New visitors cannot obtain access — 5/5

**Reproduce:** Open `/` with no session. It redirects to `/login?next=%2F`. Read the welcome page, form and footer. Email, Password and Sign In are available, but there is no visible Create account, Request access, invitation explanation, or instruction for obtaining credentials. Brand links return to the same entry page.

**Impact:** Every persona stopped before first success. Required-field and malformed-email validation are expected behavior; they do not explain how a new user gets an account.

**Recommended action:** Expose the intended new-user access path. If accounts are deliberately controlled by the instance owner, state how to request access. This finding does not assume unrestricted public registration is required.

Evidence: [impatient final entry](impatient/screenshots/05-full-entry.png), [skeptic complete page](skeptic/screenshots/03-final-signin-blocker.png), [mobile throttled outcome](mobile/screenshots/07-slow3g-final-blocker.png), [literal-language unobscured outcome](literal-language/screenshots/04-outcome-prompt-dismissed.png), and the [breaker report](breaker/report.md).

### 2. Install promotion covers the desktop primary action — 4/5

**Reproduce:** Open the entry page in a fresh desktop session. The bottom-right Install AnyList prompt overlaps most of the blue Sign In button, including its label. Screenshots reproduce this independently across four desktop personas at 1264 px width; documented heights include 569 and 758 px.

**Impact:** Install becomes the most legible nearby action while the sign-in action is obscured. Dismissal restores the button. The prompt is conditional and disappeared on some subsequent visits; this is a first-visit obstruction, not a claim of permanent submission failure.

The impatient and skeptic reports initially marked recurrence as unverified within their own sessions. Independent first-visit screenshots from the literal-language and breaker sessions confirm recurrence across fresh sessions; the merged finding therefore includes all four observed desktop occurrences.

**Recommended action:** Defer the install prompt until after the user completes account access, or place it clear of the form's primary action.

Evidence: [impatient](impatient/screenshots/01-start.png), [skeptic](skeptic/screenshots/01-welcome-no-trust-links.png), [literal-language](literal-language/screenshots/01-start.png), [breaker](breaker/screenshots/01-initial.png). The [375 px mobile capture](mobile/screenshots/01-start.png) keeps the Sign In label visible, so mobile is not counted for the desktop obstruction.

### 3. Missing pre-account trust information — 1/5

**Reproduce:** Before supplying credentials, inspect the welcome page and footer for Privacy, Terms, or operator/data-handling information. No such links are exposed. The only brand links return to sign-in.

**Impact:** The skeptic cannot assess the privacy claim or connected-account implications and would leave before sharing details. Pricing information is also absent, but this self-hosted instance provides no evidence of hidden charges; missing pricing is not a separate defect.

**Recommended action:** Provide accessible privacy/terms and instance-operator information appropriate to this deployment.

Evidence: [skeptic complete welcome page](skeptic/screenshots/03-final-signin-blocker.png).

## Persona outcomes and evidence

| Persona | Outcome | Detailed evidence |
| --- | --- | --- |
| Impatient | Skimmed, attempted primary action, then quit at account-access barrier | [Report](impatient/report.md) |
| Skeptic | Searched visible trust/access information; stopped before sharing credentials | [Report](skeptic/report.md) |
| Mobile | Tested at 375 × 812 with verified slow 3G emulation; blocked at account access | [Report](mobile/report.md) |
| Literal-language | Followed visible labels; no literal next step for obtaining credentials | [Report](literal-language/report.md) |
| Breaker | Exercised exposed form/back/double-click interactions; account gate prevented first success | [Report](breaker/report.md) |

## Scope and limitations

- The mobile run verified CDP slow 3G emulation on the active page: 400 ms latency, 50,000 bytes/second download and upload, `cellular3g`. Browser state reported `3g`, RTT 450 ms and downlink 0.4 Mbps; an uncached same-origin login fetch returned 209,677 bytes in about 4,627 ms. The initial attempt had attached to the wrong tab and was discarded as slow-network evidence; the persona repeated the landing visit after correcting it. See the mobile report for verification and repeat screenshots. Desktop device emulation does not reproduce a physical phone's radio, CPU or native keyboard.
- The local development server and shared server caches are not a production performance benchmark. No production-speed conclusion is drawn.
- The local launcher started the app and applied its pending local database migrations. No app source changes or fixes were made for this QA.
- No authenticated title tracking, connection setup, imports, or account recovery flow was completed. No success claim is made for inaccessible steps.
- Installation itself was not performed. Expected input validation and absence of a translated interface are not counted as defects.
- Screenshots are the primary evidence. The impatient video includes automation delays and a command-targeting mistake, documented in its report; it is not timing evidence.

**Retest gate:** Make the intended access path visible, rerun all five personas through their first tracked title, then assess the authenticated launch journey. Current evidence supports an entry-flow launch blocker, not a complete end-to-end pass.
