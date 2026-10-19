# Time-based Docker log retention

Docker's file drivers rotate by size, not age. This optional Ubuntu host setup
routes only the application's and database's Docker output to a dedicated local
rsyslog input and rotates it daily. One active file and 29 daily archives retain
approximately 30 days, including at least a week under normal daily operation.
Empty files rotate too, so inactive logs do not remain indefinitely.

Install on a host with rsyslog and logrotate using `sudo sh ops/logging/install.sh`.
Validate the receiver using a disposable container before changing live services.
The receiver uses a private socket directory and writes files readable by root
and the system's log-reader group. Its ruleset bypasses default syslog files.
The separate timer/state file avoids competing with the host's default logrotate.

Merge the logging blocks in `compose.logging.yaml` into the private Compose
definitions for `app` and `database` (adapt service names for another deployment).
Keep them in the base Compose file when a deployment controller rewrites the image
override. Validate Compose, then recreate only the affected services using their
existing pinned images. Configure a short maintenance window for database recreation.
No Docker daemon restart or changes to unrelated containers are needed.

The dual-logging cache is disabled: leaving it enabled would retain another copy
outside the age-based policy. Consequently `docker logs` and Docker log viewers
are unavailable for these two containers. Read `/var/log/anylist/application.log`
and `/var/log/anylist/database.log` with appropriate host permissions instead.
Do not read or modify Docker's internal log files directly.

Before replacing existing containers, preserve the last 30 days using the supported
`docker logs --timestamps --since 720h` API. Partition records by UTC day into the
new current/numbered archive files, maintaining their original age. Do not copy all
historical output into today's file or retain an extra unrotated export. Validate
that a test message reaches only its intended file and simulate the rotation policy
on disposable files before applying it. Initial log migration is a private host
operation; do not commit logs or host-specific audit findings.

This controls the two selected containers. Audit gateway/provider logs separately;
do not promise that every infrastructure log has the same retention period.
