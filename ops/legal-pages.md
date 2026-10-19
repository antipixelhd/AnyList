# Footer notices and private deployment configuration

The About, Legal notice, Privacy and Credits pages are previewed at `/dev/about`,
`/dev/legal-notice`, `/dev/privacy` and `/dev/credits`. `/dev/cookies` redirects to
Privacy's storage section. The old `/impressum` and `/dev/impressum` paths redirect
to the corresponding English `/legal-notice` route. Previews are public, noindex, uncached and use empty
operator fields, even when production disclosures are configured.

Normal notice routes are disabled by default. The footer itself always renders,
but disabled publication hides notice links in the footer and both desktop and
mobile menus, including on preview deployments and `/dev` pages. Drafts remain
directly accessible through their `/dev` URLs. When enabled, regular pages omit
Legal notice, which remains in the footer on `/dev` pages. `LEGAL_PAGES_ENABLED=true`
and nonempty values for every disclosure field are required to publish them. Failure to
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
- `LEGAL_PRIVACY_EMAIL`: optional separate privacy address; falls back to the contact address.
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
CMS. Text is escaped by Astro; HTTPS links in `[label](https://...)` form are
supported without executing HTML or other Markdown features.
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

The live VPS uses `/opt/anylist/.env.production` through the application's Compose
`env_file`. Store installation-specific legal values there, keeping publication off
while facts are being reviewed. Values have been supplied privately, outside this
repository. They are picked up when the application container is next recreated;
an ordinary restart does not reload Docker's environment. Public `/dev` pages keep
using empty placeholders rather than exposing those values.

Review container log rotation, temporary deployment dump cleanup, preview backup
retention and sign-on destinations on the actual host. Keep audit findings and real
provider details in private operations records rather than tracked templates.
