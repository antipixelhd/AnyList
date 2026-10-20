# Install prompt overlap fix

Verified on beta, 20 October 2026, at the local sign-in page in real Chromium.

The shared install-prompt markup now renders in normal document flow below account forms. Other pages keep the existing floating placement. Prompt eligibility, install handling, iOS instructions and dismissal persistence use the unchanged runtime.

| Check | Result |
| --- | --- |
| Desktop, 1264 × 569 | Prompt begins at y=492.03, form ends at y=476.03: 16px gap. Sign In passes pointer hit-testing. |
| Mobile, 375 × 812 | Prompt begins at y=645.67, form ends at y=629.67: 16px gap. Sign In passes pointer hit-testing; Install remains visible. |
| Narrow page width | Document width equals viewport width, 375px; no horizontal overflow. |
| Prompt instance | Exactly one banner rendered on sign-in. |
| Dismiss and reload | Banner remains hidden and dismissal timestamp persists. |
| Browser errors | None reported after final checks. |
| Frontend production build | Passed after syncing existing lockfile dependencies. No dependency manifests changed. |

Screenshots: [desktop](install-fix-desktop.png), [375px mobile](install-fix-mobile.png).

The actual browser install offer appeared during verification. Installation itself was not accepted. No database schema or app behavior outside prompt placement was changed.
