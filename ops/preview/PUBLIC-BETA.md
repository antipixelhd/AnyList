# Public beta access

Open **Actions → preview: manual actions → Run workflow**, use `main`, select
`beta`, and choose **Public beta.anylist ON** or **Public beta.anylist OFF**.
`public-beta-status` reports the persistent gateway configuration. Other preview
slots reject these operations. Existing preview `status` checks remain unchanged.

`https://beta.anylist.win` uses Cloudflare's proxied DNS and the existing public
Caddy gateway on port 443. Its own origin certificate is stored privately on the
VPS. The app and database are the existing beta slot. Tailnet HTTPS on port 8001
remains available in both modes. Production's route is outside the managed block.

OFF returns HTTP 503 with “Beta is currently unavailable.” ON serves the beta
app, including its existing login/registration policy. It does not add a tester
allowlist. Responses use `private, no-store` and `noindex, nofollow, noarchive`.
Do not add a Cloudflare cache rule that overrides those headers for this hostname.

The installed, root-owned controller accepts only fixed beta operations through
the existing CI login/gateway. It locks the beta slot, validates the complete
Caddy configuration, updates only its marked beta block, and sends SIGUSR1 to
reload the same file gracefully. Caddy's admin API remains disabled. The controller
verifies the public login response and its ON/OFF header before reporting success.
Failure disables the private socket and attempts to load the OFF configuration;
it never restores public ON access after a failed OFF action.

Caddy connects through `/config/anylist-beta.sock` in its existing config volume.
An isolated systemd socket proxy forwards that Unix socket to `127.0.0.1:8001`.
No additional TCP listener is opened. OFF stops the socket and any active proxy.
The Caddyfile stores the gate state independently of app deployments. The boot
restore service starts the socket only when the persisted route is ON.

## One-time installation

These steps assume the existing `public-gateway` container and its named Docker
volumes, file-mounted `/opt/public-gateway/Caddyfile`, and the beta slot described
in [README.md](README.md). The gateway must run `caddy run --config
/etc/caddy/Caddyfile --adapter caddyfile`, without `--resume` or API-based reloads.
Preserve the Caddyfile inode because it is a single-file bind mount.

1. Using `cf`, issue a separate Cloudflare Origin CA certificate for
   `beta.anylist.win`. Generate the private key on the VPS. Store the certificate
   and key under the gateway data volume's `anylist-origin/` directory as
   `beta.anylist.win.pem` and `beta.anylist.win.key`. Keep the private key mode 600.
2. Run `bash ops/preview/install-public-beta.sh` as root from a reviewed checkout.
   Initial installation backs up the original Caddyfile under
   `/var/lib/anylist-preview/public-beta-before-install.caddy` and loads OFF.
3. Using `cf dns records create`, register a proxied `CNAME` from
   `beta.anylist.win` to `anylist.win`, with automatic TTL. DNS stays registered
   when the gate is OFF. No DNS edit is needed when toggling.
4. Configure beta's canonical `SERVER_URL` as `https://beta.anylist.win` and
   preserve `PREVIEW_HOSTNAME` for tailnet access. Set `ADDITIONAL_PUBLIC_ORIGINS`
   to the original HTTPS tailnet URL including port 8001, so form submissions work
   through either host without accepting one host's origin on the other. Verify password login and
   authenticated writes through both origins. OIDC is currently disabled on beta;
   enabling it later requires callback URLs registered with the identity provider.
   Beta also sets `NEW_ACCOUNTS_GAMES_ACCESS=true` for future accounts. Existing
   environment files survive deployment/recreation; restart beta's backend after
   changing this setting. Initial beta provisioning uses the same games default.
5. Publish the controller sources to `beta`, and the manual workflow changes to
   default branch `main`. The reusable remote workflow checks out `beta` for input
   validation. Installed privileged controllers are updated only by an administrator.
6. Exercise ON and OFF through GitHub, then verify public OFF, tailnet availability,
   and unchanged production behavior. Test boot restoration by restarting only
   `anylist-public-beta-restore.service`, without rebooting the shared VPS.

For direct administrator use:

```sh
python3 -I /opt/anylist-preview/controller.py public-beta-status beta
python3 -I /opt/anylist-preview/controller.py public-beta-on beta
python3 -I /opt/anylist-preview/controller.py public-beta-off beta
```

If a public check fails, inspect the gateway logs and socket units privately.
Re-run OFF to confirm the gate. The initial backup is for administrator recovery;
do not restore the entire shared gateway file over later unrelated route edits.
