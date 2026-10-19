#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
test -x /usr/sbin/rsyslogd
test -x /usr/sbin/logrotate
test -x /usr/lib/rsyslog/rsyslog-rotate
getent passwd syslog >/dev/null
getent group adm >/dev/null
install -d -o syslog -g adm -m 0750 /var/log/anylist
install -d -o root -g root -m 0700 /var/lib/anylist-logging
install -d -o syslog -g adm -m 0700 /var/spool/rsyslog/anylist
target=/etc/rsyslog.d/35-anylist.conf
previous=$(mktemp)
had_previous=false
if [ -f "$target" ]; then cp "$target" "$previous"; had_previous=true; fi
install -m 0644 "$source_dir/rsyslog.conf" "$target"
if ! rsyslogd -N1; then
    if "$had_previous"; then cp "$previous" "$target"; else rm "$target"; fi
    rm "$previous"
    exit 1
fi
rm "$previous"
install -m 0644 "$source_dir/logrotate.conf" /etc/logrotate-anylist.conf
install -m 0644 "$source_dir/anylist-logrotate.service" /etc/systemd/system/
install -m 0644 "$source_dir/anylist-logrotate.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl restart rsyslog
attempt=0
until test -S /var/spool/rsyslog/anylist/docker.sock; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 25 ] || { echo 'Log receiver socket did not become ready.' >&2; exit 1; }
    sleep 0.2
done
logrotate --debug --state /var/lib/anylist-logging/logrotate.status /etc/logrotate-anylist.conf
systemctl enable --now anylist-logrotate.timer
