# Footer notices and private deployment configuration

The About, Legal notice, Privacy and Credits pages are previewed at `/dev/about`,
`/dev/legal-notice`, `/dev/privacy` and `/dev/credits`. `/dev/cookies` redirects to
Privacy's storage section. The old `/impressum` and `/dev/impressum` paths redirect
to the corresponding English `/legal-notice` route. Previews are public, noindex, uncached and use empty
operator fields, even when production disclosures are configured.

Normal routes and their footer links are disabled by default. `LEGAL_PAGES_ENABLED=true`
and all six nonempty disclosure fields are required to publish them. Failure to
load configuration also hides the pages. The disclosure API returns blank fields
when disabled or incomplete. Published notices are reachable without signing in.
`/cookies` redirects to `/privacy#cookies` when publication is enabled.

## VPS setup

Use the six `LEGAL_*` fields in `.env.example` plus `LEGAL_PAGES_ENABLED`.
Store real values in the VPS's private `.env` beside the Compose file, never in
source files, tracked examples, Docker build arguments or image layers.
The repository ignores `.env` and excludes environment files from Docker builds.
Both supplied deployment Compose files forward these fields at runtime.
For a separate private env file, use `docker compose --env-file /private/path/legal.env up -d --force-recreate scrob`
(and pass the appropriate `-f` option for omnibus). That file must also include
any other variables your deployment requires. Restrict file access to the operator.
For bare-metal operation, use the ignored root `.env` and restart the backend.
Do not print `docker compose config`, environment dumps or real values into CI logs.

Keep `LEGAL_PAGES_ENABLED=false` while reviewing the disclosures. Fill:

- `LEGAL_OPERATOR_NAME`: actual person or legal entity responsible.
- `LEGAL_OPERATOR_ADDRESS`: address for service; quoted multiline text is supported.
- `LEGAL_CONTACT_EMAIL`: monitored public contact address.
- `LEGAL_HOSTING_DETAILS`: actual host, location, CDN/security, mail and SSO providers,
  their roles, and deployment-specific processing or browser storage.
- `LEGAL_RETENTION_DETAILS`: log, backup and post-deletion retention periods or
  concrete deletion criteria. Do not promise immediate backup deletion without verification.
- `LEGAL_TRANSFER_DETAILS`: actual non-EEA countries, safeguards and how to obtain
  a copy; explain the absence of relevant transfers only if verified.

After review, enable publication and recreate the application container (a plain
Compose restart does not reload container environment). No frontend rebuild or
database migration is needed. Frontend configuration refreshes within five seconds.
Six short text fields fit environment configuration better than a database or a
CMS. Text is escaped by Astro; no administrator HTML or Markdown is executed.
These disclosures become public when enabled: keeping them out of Git does not
make a published legal notice private. `/dev` is a preview namespace, not access control.

## Review still required

The code establishes cookie durations (sign-in up to seven days, temporary SSO up
to ten minutes), year-long preference cookies with some automatic regional defaults,
local theme/prompt storage, session navigation storage, external images and
automatically loading video embeds. The English Privacy page includes these facts.
It does not claim that every storage operation is strictly necessary.

Confirm actual providers, infrastructure logs, retention, backup deletion and transfer
arrangements on the VPS. Do not infer operator identity or hosting location from Git
metadata or a developer machine. Conditional legal entity/register/VAT, representative,
DPO or journalistic-editor details need separate assessment if applicable; none are
invented for this installation. The publication check detects missing fields, not
legal adequacy. Automatic embeds and preference storage still need a function-specific
consent assessment before release; merely publishing a notice does not resolve this.

References reviewed: GDPR Article 13 (https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng/),
TDDDG section 25 (https://www.gesetze-im-internet.de/ttdsg/__25.html),
DDG section 5 (https://www.gesetze-im-internet.de/ddg/__5.html).
