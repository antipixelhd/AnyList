#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
for service in frontend backend; do
  file="$STATE/$service.pid"
  if [[ -f "$file" ]]; then
    pid=$(cat "$file")
    # Never signal an unrelated process after a cached image reuses a PID.
    if [[ $pid =~ ^[0-9]+$ && -d /proc/$pid ]] && [[ $(readlink "/proc/$pid/cwd") == "$ROOT/$service" ]]; then
      pkill -TERM -P "$pid" || true
      kill "$pid" || true
      for _attempt in {1..30}; do
        kill -0 "$pid" 2>/dev/null || break
        [[ $(ps -o stat= -p "$pid") == Z* ]] && break
        sleep 0.1
      done
      if kill -0 "$pid" 2>/dev/null && [[ $(ps -o stat= -p "$pid") != Z* ]]; then
        echo "Service $service did not stop; refusing overlapping restart" >&2
        exit 1
      fi
    fi
    rm -f -- "$file"
  fi
done
