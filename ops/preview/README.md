# AnyList private source previews

**Normal development must not require VPS SSH.** GitHub is the control plane;
the VPS executes installed preview commands. Local PCs, local Codex, future
Codex Cloud, and other machines all deploy by pushing the same GitHub branches.
This implementation does not configure Codex Cloud or change Docker publishing.

## Stage boundary

Stage 1 supplies scripts, systemd/SSH templates, workflows, tests, documentation,
and branches. It intentionally leaves the Tailscale client ID/audience and actual
private hostname unpopulated. Tailscale SSH needs no CI private key or pinned
known-hosts secret. Deployment jobs are skipped until repository variable
`PREVIEW_ENABLED` is exactly `true`.

Stage 2 installs infrastructure and configures access. Linux/systemd/PostgreSQL/
Tailscale behavior must be exercised there; local unit tests do not substitute for
the end-to-end GitHub → Tailscale → SSH → VPS test.

Stage 2 was exercised on 2026-09-30 with all three private source previews running
simultaneously. GitHub deployment is enabled. The initial beta database is empty
and migrated; development databases were copied from it. Private addresses,
identity values, and the deployment verification report are kept outside Git.

Use **Actions → Preview Operations → Run workflow** for status, restart, redeploy,
development DB reset and runtime reconstruction. Push `beta` for single-stream
work, or either development branch for parallel work; **Preview Deploy** handles
deployment automatically. Rebase a development branch onto beta, force-push with
lease, and its disposable DB is refreshed from beta. Test there, integrate into
beta and test the combined state. Production promotion remains your separate
beta-to-main process.

## Slots and runtime

`slots.json` is the single privileged mapping. Branch names are slot names.

| Branch | Frontend | Backend | Database | Persistence |
| --- | --- | --- | --- | --- |
| beta | 127.0.0.1:8001 | 127.0.0.1:8101 | anylist_preview_beta | canonical, persistent |
| development-1 | 127.0.0.1:8002 | 127.0.0.1:8102 | anylist_preview_development_1 | disposable |
| development-2 | 127.0.0.1:8003 | 127.0.0.1:8103 | anylist_preview_development_2 | disposable |

Each slot has `/srv/anylist-preview/<branch>/checkout`, `.venv`, `data`, its
checkout's frontend `node_modules`, a separate Unix user and PostgreSQL login/DB,
and `/etc/anylist-preview/<branch>.env`. Backend/frontend systemd services execute
Uvicorn/Astro source directly and are explicitly restarted. PostgreSQL is shared
and is never restarted by deployment. Public URLs are
`https://<machine>.<tailnet>.ts.net:8001` (or 8002/8003).

The controller lives in root-owned `/opt/anylist-preview`, outside all branch
checkouts. Root only validates inputs, manages fixed preview services/databases,
and starts commands as the slot user. Git, npm, uv, Alembic, and all application
Python/Node code execute as that slot user. CI authenticates with Tailscale SSH as
`anylist-preview-ci`. Its root-owned login shell accepts only validated preview
commands, refuses interactive/PTY sessions, and never evaluates shell expressions.
Its home is root-owned and it has sudo access only to the isolated Python gateway.
OpenSSH explicitly denies this account. It has no interactive root/production shell.

Only trusted repository contributors should push deployable branches. Application
migrations run with their slot's DB owner permissions: a malicious migration could
modify its own DB, including beta. Persistence guarantees describe controller
operations, not protection against deliberately destructive application code.
Keep production credentials, database roles, files, and service names separate.

## Automatic deployment

`Preview Deploy` triggers only on beta/development-1/development-2 pushes. It sends
the exact pushed SHA, branch, and GitHub forced flag. A shared concurrency group
serializes each slot across automatic/manual/provisioning workflows with
`cancel-in-progress: false`. Different slots can run concurrently. GitHub can
supersede pending runs; active deployments finish. VPS file locks provide a second
serialization layer. A detached transient systemd job survives runner cancellation
or SSH loss; the next operation waits for its slot lock.

The controller fetches the explicit branch ref and compares its head to the requested
SHA before stopping/resetting anything. A stale request logs `SUPERSEDED` and exits
successfully without deployment. There is no `git pull`. A push arriving after this
check is handled by the next serialized deployment. Checkout uses `checkout -B`,
`reset --hard`, and `clean -fd`; runtime/data live outside tracked source.

Dependency manifests are hashed against root-owned stamps from the last successful
dependency sync. uv uses the slot `.venv`, Python 3.13, and `sync --frozen --no-dev`.
`npm ci` runs only when manifests changed or the Astro import probe fails. Backend
imports probe missing/broken environments. Normal source changes skip reinstalling.
`recreate-preview` forces dependency synchronization.

Beta stops its own runtime, inspects DB revisions, and takes a timestamped custom
`pg_dump` backup before changed or pending migrations. Migration/snapshot operations
share `beta-database.lock`. Only forward `alembic upgrade heads` runs. Beta is never
reset, dropped, cloned over, downgraded, or automatically restored. Incompatibility
or migration failure leaves its DB intact and services stopped; fix the migration
in code, then redeploy. The most recent 20 completed migration backups remain in
`/var/lib/anylist-preview/backups`; older matching dump files are removed only
after a new dump completes. Off-host backups remain optional infrastructure
maintenance. Deployment never restores a backup automatically.

Ordinary development pushes preserve their DB and apply forward migrations. Forced
pushes, non-fast-forward changes relative to the last successfully deployed SHA,
first deployment, recreation, or a DB revision absent from the branch graph cause
an explicit rebuild from beta. Arbitrary migration SQL/connectivity failures do
**not** trigger a silent reset. Multiple heads and merge revisions are supported;
deployment does not manufacture merge revisions. Conflicts remain a code task.

Cloning locks beta only while taking a consistent live `pg_dump`; beta's app stays
running. Then only the target development services stop, its DB is dropped/recreated,
and the dump is restored with target ownership/no source ACLs. The target branch's
forward migrations run, services restart, and health checks run. Temporary dumps
are removed on success/failure. The destructive primitive itself refuses beta.

## GitHub operations

Open **Actions → Preview Operations → Run workflow**, choose `main` for the workflow,
then the target preview and operation. Main contains only the orchestration workflows;
the shared workflow reads validated control configuration from beta. GitHub requires
dispatch workflows on the default branch, so main remains the default. Production's
manual `Publish AnyList container` workflow stays unchanged.

| Operation | Behavior |
| --- | --- |
| status | Checkout branch/SHA, freshly fetched remote SHA, service state/logs, separate frontend/backend health, DB connectivity/revisions/heads, URL |
| restart | Restart only selected frontend/backend, then health-check; no code/DB deployment |
| redeploy | Deploy freshly fetched current branch head through the same controller |
| reset-db-from-beta | Fresh snapshot → replace target development DB → target checkout migrations → restart/health; beta is refused |
| recreate-preview | Recreate checkout/venv/node_modules/services, fetch current branch; development also rebuilds DB; beta always preserves DB/data/env |

Health failure produces a failed Action. Redeploy/restart are the normal recovery
controls after fixing code/configuration. Disconnected job output remains in
systemd journal; status shows recent application logs. Full controller job journals
are a break-glass diagnostic when the runner lost its connection.
Recreation retains old checkout/venv files in the slot's `retired-<timestamp>`
directory for diagnosis; these can be removed during reviewed disk maintenance.

`Preview Provision` is also available in Actions. It first synchronizes validated
**additive** slot configuration from beta, then provisions the requested configured
slot. It creates runtime user/checkouts/DB/env/services and a private Serve listener.
Unknown/unconfigured slots fail. Existing slots may be provisioned idempotently;
no existing beta DB is dropped. A partially failed first creation of a DB role/env
requires administrator inspection rather than guessing/destructive repair.

## Development and rebases

Single-stream work happens on beta. Parallel changes happen on development-1/2.
Rebase a development branch onto latest beta as needed, use `--force-with-lease`,
test its rebuilt preview, merge into beta, then test the combined state. Resolve
parallel migration conflicts/merge revisions in code before integration. Never
force-push beta. When satisfactory, separately integrate beta into main and run the
existing production workflow yourself. Preview deployment does not perform that step.

Recommend a beta branch ruleset that blocks force pushes/deletion while allowing
normal direct development pushes. No branch protections are changed by this setup;
review desired bypass/PR policies before enabling a ruleset.

## GitHub configuration (Stage 2 only)

| Type | Exact name | Value |
| --- | --- | --- |
| Secret | TS_OAUTH_CLIENT_ID | Tailscale federated identity Client ID |
| Secret | TS_AUDIENCE | Tailscale federated identity Audience |
| Secret | PREVIEW_VPS_HOST | Same private hostname, stored as a secret for automatic log masking |
| Variable | PREVIEW_VPS_HOST | Actual `<machine>.<tailnet>.ts.net`, no scheme/port |
| Variable | PREVIEW_ENABLED | `true` only after bootstrap/configuration; unset or `false` during Stage 1 |

No OAuth client secret, reusable auth key, GitHub PAT, production secret, or DB
password is required in the repository. `PREVIEW_SSH_PRIVATE_KEY` and
`PREVIEW_SSH_KNOWN_HOSTS` are no longer required. `tailscale ssh` verifies host keys
through Tailscale rather than a committed or manually pinned known-hosts file.
The hostname variable remains available as configuration, but workflows read its
secret copy so step environments are masked before execution. Public Git fetches
use the public repository.
The actual hostname is written to `/etc/anylist-preview/hostname` during bootstrap.
Per-slot app secrets/DB passwords are generated only on the VPS. Optional app settings
belong in those root-owned env files; never commit populated files.

## Stage 2 runbook

1. Choose the preview VPS, Tailscale machine hostname, and initial beta data (empty
   bootstrap is supported). If importing existing data, arrange a reviewed one-time
   import and schema validation before deploying beta. Nothing copies production.
2. In Tailscale create `tag:ci` and `tag:anylist-preview` with administrative owners.
   Configure GitHub workload identity federation: GitHub issuer
   `https://token.actions.githubusercontent.com`, `auth_keys` scope, `tag:ci`, and
   repository/subject restrictions for `antipixelhd/AnyList` and the main/beta/
   development workflow refs. Never authorize arbitrary repositories or PR subjects.
   Copy Client ID/Audience to the two named GitHub secrets. The official Action is
   `tailscale/github-action@v4` with `id-token: write` and `tags: tag:ci`.
3. Add minimum network grants: CI → preview VPS **TCP 22 only**. Review existing broad
   grants/ACLs: adding a restrictive rule does not override an existing allow-all rule.
   Allow your reviewer identities → preview tag TCP 8001/8002/8003 separately. Add a
   Tailscale SSH rule accepting only `tag:ci` → `tag:anylist-preview` → the explicit
   `anylist-preview-ci` Unix account. Use `accept`, since tagged CI cannot use
   interactive `check` mode. Do not allow root or `autogroup:nonroot` for CI.
   Example fragment (merge into your existing policy):

   ```json
   {"tagOwners":{"tag:ci":["autogroup:admin"],"tag:anylist-preview":["autogroup:admin"]},
    "grants":[{"src":["tag:ci"],"dst":["tag:anylist-preview"],"ip":["tcp:22"]}],
    "ssh":[{"action":"accept","src":["tag:ci"],"dst":["tag:anylist-preview"],"users":["anylist-preview-ci"]}]}
   ```

4. Bootstrap using direct administrator access. Supported baseline: Ubuntu 24.04,
   systemd, PostgreSQL 16, Git/OpenSSH, Python 3, Node 22.12+ at `/usr/bin/node`, npm,
   uv at `/usr/local/bin/uv`, and current Tailscale with workload identity support.
   PostgreSQL must listen only on loopback/socket, use peer auth for local postgres
   administration and password auth for slot TCP logins. Preview DB roles must not
   be superusers/CREATEDB/CREATEROLE or members of production roles. Enable Tailscale
   HTTPS/MagicDNS, tag the VPS `tag:anylist-preview`, and allow private Serve on the
   selected HTTPS ports. Do not use Funnel. Firewall public SSH off or bind SSH to
   private management/Tailscale interfaces; preserve a tested break-glass admin path.
   Python 3.13 for each app environment can be installed by uv.

   On that VPS, the first repository command is:

   ```bash
   git clone --branch beta https://github.com/antipixelhd/AnyList.git anylist-preview-bootstrap
   cd anylist-preview-bootstrap
   sudo bash ops/preview/bootstrap.sh
   tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' | sudo tee /etc/anylist-preview/hostname
   ```

5. Enable Tailscale SSH on the VPS after the reviewed network/SSH rules are in place:

   ```bash
   sudo tailscale set --ssh
   sudo sshd -t
   sudo systemctl reload ssh
   gh variable set PREVIEW_VPS_HOST --repo antipixelhd/AnyList --body '<actual-private-FQDN>'
   ```

   Run the GitHub variable command on your authenticated admin machine. No SSH key
   generation, authorized-key installation, or known-hosts secret is needed.
   Bootstrap installs `/usr/local/bin/anylist-preview-ci` as the CI account's login
   shell, with isolated Python (`-I`), root-owned code/home, and a deny rule for
   ordinary OpenSSH. Tailscale SSH runs that shell with `-c <remote-command>`;
   the wrapper parses arguments and calls only the validated sudo gateway. It does
   not use `SSH_ORIGINAL_COMMAND` or OpenSSH `ForceCommand`.

   Keep SSH `acceptEnv` empty for CI; do not forward interpreter/loader environment
   variables. Test the installed wrapper using Ubuntu's normal login/su path.
   Tailscale's SFTP subsystem has an internal fallback that can bypass login shells:
   do not assume OpenSSH subsystem/forwarding settings restrict Tailscale SSH.
   The CI account therefore has no writable home, no slot-user group membership,
   and no access to slot env files or production secrets. Verify file-transfer and
   forwarding behavior and filesystem permissions during Stage 2; neither is used
   by preview deployment. Interactive/PTY/exec requests must remain restricted.

   Bootstrap also installs `tailscaled.conf` as a daemon systemd drop-in setting
   `TS_SSH_DISABLE_FORWARDING=true`. Restart `tailscaled` once in the bootstrap
   maintenance window to activate it. This disables TCP and Unix socket SSH
   forwarding for **all Tailscale SSH users on this VPS**, including administrators;
   regular admin commands and the existing Serve/Funnel configuration remain
   available. The restricted login shell alone cannot block forwarding. The GitHub
   status operation probes PostgreSQL's protocol through an SSH forwarding request
   and fails unless forwarding is explicitly denied. Recheck this after Tailscale
   upgrades; this daemon setting comes from Tailscale's implementation rather than
   a per-user SSH policy field.

6. On the VPS, provision beta first, then development slots:

   ```bash
   sudo python3 /opt/anylist-preview/controller.py provision beta
   sudo python3 /opt/anylist-preview/controller.py provision development-1
   sudo python3 /opt/anylist-preview/controller.py provision development-2
   tailscale serve status
   ```

   Provision calls `tailscale serve --bg --https=<frontend-port>
   http://127.0.0.1:<frontend-port>` per slot and enables the two services. It never
   resets unrelated Serve configuration or restarts PostgreSQL.

7. Enable and test through GitHub:

   ```bash
   gh variable set PREVIEW_ENABLED --repo antipixelhd/AnyList --body true
   gh workflow run preview-operations.yml --repo antipixelhd/AnyList --ref main -f preview=beta -f operation=status
   gh workflow run preview-operations.yml --repo antipixelhd/AnyList --ref main -f preview=development-1 -f operation=redeploy
   ```

   Verify all three HTTPS previews, logins on different ports, secure cookies,
   browser POST origin checks, API proxy, redirects, and HMR WebSocket through Serve.
   Test a normal development push, a rebase/force-with-lease push, stale queued SHA,
   restart, explicit reset, and beta reset refusal. Record branch SHA and DB revision
   before/after. Test valid multiple heads/merge revisions using disposable data.
   Verify that disconnecting SSH leaves the systemd deployment running safely.
   Confirm CI cannot reach production services or get an interactive shell. Test
   arbitrary commands, shell injection, PTY requests, beta reset, SFTP/scp and
   forwarding attempts. Confirm the CI account cannot read app/production secrets
   or write installed controller/home files. Validate successful `tailscale ssh`
   commands from the actual ephemeral GitHub runner, without interactive prompts.

## Adding development-3 later

Add `development-3` to beta's `slots.json`, e.g. ports 8004/8104 and DB
`anylist_preview_development_3`, `persistent: false`. Create its branch from beta.
Add its branch to the automatic workflow filter and manual preview choices on main
and your development branches; add reviewer access for its HTTPS port in Tailscale.
Provisioning refuses a port already owned by an unrelated Serve listener or one
with Funnel enabled, before creating the slot's resources. Existing slots are
immutable through configuration synchronization.
Run **Preview Provision** with `development-3`. The workflow installs only additive,
validated mapping changes and provisions everything through the stable controller.
No routine VPS SSH is needed. Existing port/DB/persistence mappings are immutable
through configuration sync to prevent accidental beta replacement.

Controller/template upgrades remain reviewed infrastructure maintenance: bootstrap
installs a trusted version, and ordinary branch deploys cannot replace privileged
code. Such upgrades or repairing broken SSH/Tailscale/GitHub/PostgreSQL infrastructure
are legitimate break-glass administrator operations.

## Actual-code assumptions and limits

Astro is SSR (`@astrojs/node`), not a static frontend; browser API traffic goes
through Astro's `/api/proxy`, and SSR calls FastAPI internally. Source previews use
`process.env.BACKEND_PORT` and explicit IPv4 loopback. `SERVER_URL` remains the
public HTTPS origin used by existing origin checks and backend CORS; OIDC callback
is generated per port. Existing allowed development hosts remain. Preview session
and OIDC cookies are namespaced because browser cookies ignore port numbers; HTTPS
source previews set Secure. Local/default production cookie names remain unchanged.
The existing `/dev` playground remains loopback-only by its existing middleware.

Backend `/health` already checks DB connectivity. Startup runs metadata `create_all`,
cleans job/session records, and starts provider/background jobs. A DB clone includes
beta's external integration tokens/settings and API keys, but not filesystem uploads
or cache data; these live in each slot's separate `data` directory. Separate JWT
secrets prevent cross-slot JWT reuse; cloned database API keys retain their existing
semantics. Use preview/test integration accounts in beta if you do not want parallel
previews to act on real provider accounts. This implementation preserves application
behavior rather than altering provider/scheduler business logic.

The one intentional migration spelling difference is `upgrade heads` instead of
`upgrade head`, needed for the requested parallel migration graphs. The existing
Windows launcher and production Docker workflow are unchanged.

## Checks and upstream documentation

Run `python -m unittest discover -s ops/preview/tests -v`, `python -m compileall -q
ops/preview`, `bash -n ops/preview/bootstrap.sh`, and `actionlint`. Frontend changes
are covered by `npm test`, `npm run check`, and `npm run build` in frontend.
`Preview Infrastructure Checks` runs Python/shell/workflow checks on GitHub.

Implementation APIs were verified with Context7 against official docs:
[Tailscale Action](https://tailscale.com/docs/integrations/github/github-action),
[workload identity](https://tailscale.com/docs/features/workload-identity-federation),
[Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh),
[SSH CLI](https://tailscale.com/docs/reference/tailscale-cli),
[Astro configuration](https://docs.astro.build/en/reference/configuration-reference/),
[Astro environment migration](https://docs.astro.build/en/guides/upgrade-to/v6/),
[Alembic branch graphs](https://alembic.sqlalchemy.org/en/latest/branches.html),
[uv sync](https://docs.astral.sh/uv/concepts/projects/sync/), and
[GitHub manual workflows](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
