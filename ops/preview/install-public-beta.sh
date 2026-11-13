#!/usr/bin/env bash
set -euo pipefail
# Administrator-only gateway setup. DNS and the beta origin certificate must exist.
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test -f /opt/public-gateway/Caddyfile
test -f /opt/anylist-preview/controller.py
test -S /var/run/docker.sock
test -f /var/lib/docker/volumes/public-gateway_caddy_data/_data/anylist-origin/beta.anylist.win.pem
test -f /var/lib/docker/volumes/public-gateway_caddy_data/_data/anylist-origin/beta.anylist.win.key
test -d /var/lib/docker/volumes/public-gateway_caddy_config/_data
for name in controller.py public_beta.py public-beta.caddy.in; do
    install -o root -g root -m 644 "$source_dir/$name" "/opt/anylist-preview/$name"
done
for kind in socket service; do
    install -o root -g root -m 644 "$source_dir/public-beta.$kind" "/etc/systemd/system/anylist-public-beta.$kind"
done
install -o root -g root -m 644 "$source_dir/public-beta-restore.service" /etc/systemd/system/anylist-public-beta-restore.service
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/anylist-public-beta{.socket,.service,-restore.service}
python3 -I - <<'PY'
import sys
sys.path.insert(0, '/opt/anylist-preview')
import public_beta
if public_beta.BEGIN not in public_beta.CADDYFILE.read_text():
    public_beta.install()
print('Public beta.anylist ' + public_beta.configured_mode())
PY
systemctl enable anylist-public-beta-restore.service
