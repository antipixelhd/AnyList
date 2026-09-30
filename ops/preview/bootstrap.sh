#!/usr/bin/env bash
set -euo pipefail
# Stage 2, root, from a reviewed checkout. No network installation or credentials here.
[[ $EUID -eq 0 ]] || { echo 'Run as root during initial bootstrap' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for command in python3 git node npm uv psql pg_dump pg_restore tailscale systemctl systemd-run; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
[[ -x /usr/local/bin/uv && -x /usr/bin/node ]] || { echo 'Install uv at /usr/local/bin/uv and Node at /usr/bin/node' >&2; exit 1; }
install -d -m 755 /opt/anylist-preview /etc/anylist-preview /srv/anylist-preview
install -d -m 700 /var/lib/anylist-preview
for name in controller.py migration_state.py gateway.py ci_entry.py slots.json backend.service.in frontend.service.in; do
  install -o root -g root -m 644 "$source_dir/$name" "/opt/anylist-preview/$name"
done
python3 -c "import sys; sys.path.insert(0, '/opt/anylist-preview'); from controller import load_slots; load_slots()"
install -o root -g root -m 755 "$source_dir/ci_entry.py" /usr/local/bin/anylist-preview-ci
install -d -o root -g root -m 755 /var/empty/anylist-preview-ci
id anylist-preview-ci >/dev/null 2>&1 || useradd --system --no-create-home \
  --home-dir /var/empty/anylist-preview-ci --shell /usr/local/bin/anylist-preview-ci anylist-preview-ci
usermod --home /var/empty/anylist-preview-ci --shell /usr/local/bin/anylist-preview-ci anylist-preview-ci
cat > /etc/sudoers.d/anylist-preview-ci <<'EOF'
anylist-preview-ci ALL=(root) NOPASSWD: /usr/bin/python3 -I /opt/anylist-preview/gateway.py *
EOF
chmod 440 /etc/sudoers.d/anylist-preview-ci
visudo -cf /etc/sudoers.d/anylist-preview-ci
install -o root -g root -m 644 "$source_dir/sshd.conf" /etc/ssh/sshd_config.d/80-anylist-preview-ci.conf
sshd -t
echo 'Controller installed. Configure Tailscale SSH policy + hostname; provision beta first. See README.md.'
