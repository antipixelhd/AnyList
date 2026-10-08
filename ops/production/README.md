# Production container deployment

`Publish AnyList container` builds and publishes main, then calls `anylist deploy
<commit-sha>` through the existing preview Tailscale workload identity secrets.
Only the current public main commit is accepted. Old queued runs skip deployment.
The deploy job calls `preview-remote.yml`, preserving the reusable workflow identity
allowed by the existing Tailscale federation policy.
The controller pulls only `ghcr.io/antipixelhd/anylist:sha-<commit-sha>`, verifies
its embedded version, and pins its digest in `/opt/anylist/compose.override.yaml`.

The root-owned controller is installed from `ops/preview/production.py` at
`/opt/anylist-preview/production.py`. Ordinary pushes cannot replace it. To update
the controller, an administrator installs reviewed files through Ubuntu SSH.
`bootstrap.sh` installs it and the boot recovery service on new hosts.

No new long-lived credential or Docker socket access is granted to CI. The existing
`anylist-preview-ci` account can call its existing preview commands plus this fixed
production command. It cannot choose another image repository, container, Compose
file, shell command, mount, or Docker option. The SSH/network policy continues to
allow only the CI account over TCP 22; no privileged account, forwarding or SFTP.
Trusted main code runs in the app container with its existing app permissions.

Deployments serialize both in Actions and with a VPS file lock. The root gateway
starts a systemd job, so runner cancellation/disconnection cannot interrupt it.
The existing database service and other VPS containers are never recreated.

The controller stops the old app before dumping the production database, then
starts the replacement. This creates a short maintenance window. Docker backend
health and an HTTP response from the frontend must both remain good for 15 seconds,
within a 180-second timeout. The candidate's entrypoint applies migrations normally.

On failure the controller stops the candidate, recreates **only the AnyList
database** from the backup (including removing objects added by new migrations),
and recreates the previous app image. The Action fails even when rollback succeeds.
Writes made during a failed candidate's health-check window are rolled back too.
The app data volume is retained; it is not part of the database snapshot.

After success the temporary backup and previous image are removed. Image removal
never forces deletion or prunes other VPS images; Docker retains shared/in-use images.
After a failed rollback, recovery files remain in the root-only
`/var/lib/anylist-production` directory. The next deployment retries recovery first;
`anylist-production-recovery.service` also retries interrupted transactions on boot.
Administrators can retry with:

```sh
sudo python3 -I /opt/anylist-preview/production.py --recover
sudo journalctl -u 'anylist-production-job-*' --no-pager
```

Only startup deployment health is monitored here; later failures use Docker's
existing restart policy and the VPS monitoring system.
