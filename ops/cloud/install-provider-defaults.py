#!/usr/bin/env python3
"""Reviewed administrator bootstrap helper; never a CI gateway operation.

Read a JSON object of metadata defaults on stdin. Values never enter argv/logs.
Only the preview backend environment and units are updated; no database access.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

KEYS = {"TMDB_API_KEY", "TVDB_API_KEY", "TVDB_SUBSCRIBER_PIN", "IGDB_CLIENT_ID",
        "IGDB_CLIENT_SECRET", "HARDCOVER_API_KEY", "RAWG_API_KEY", "ITAD_API_KEY"}
ETC = Path("/etc/anylist-preview")
INSTALL = Path("/opt/anylist-preview")
UNITS = Path("/etc/systemd/system")
COMMON = "EnvironmentFile=-/etc/anylist-preview/providers.env"


def validate(values):
    if not isinstance(values, dict) or not values or set(values) - KEYS:
        raise ValueError("Only metadata defaults are accepted")
    for value in values.values():
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError("Invalid metadata credential encoding")
    return values


def updated_unit(text, branch):
    marker = f"EnvironmentFile=/etc/anylist-preview/{branch}.env"
    if text.count(marker) != 1:
        raise ValueError("Unexpected backend unit; refusing modification")
    if COMMON in text:
        return text
    return text.replace(marker, COMMON + "\n" + marker)


def write_private(path, content, mode):
    if path.is_symlink():
        raise ValueError("Refusing symlink destination")
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    if os.geteuid() != 0:
        raise ValueError("Administrator bootstrap requires root")
    values = validate(json.load(sys.stdin))
    path = ETC / "providers.env"
    if path.exists():
        if path.is_symlink() or path.stat().st_uid != 0:
            raise ValueError("Unexpected provider file ownership")
        previous = dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))
        values = validate({**previous, **values})
    slots = json.loads((INSTALL / "slots.json").read_text())
    prepared = []
    for branch, slot in slots.items():
        if branch != "beta" and not re.fullmatch(r"development-[1-9][0-9]*", branch):
            raise ValueError("Invalid preview branch")
        port = slot["backend_port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid preview port")
        unit = UNITS / f"anylist-preview-{branch}-backend.service"
        if unit.exists():
            if unit.is_symlink() or unit.stat().st_uid != 0:
                raise ValueError("Unexpected unit ownership")
            prepared.append((unit, updated_unit(unit.read_text(), branch), port))
    template = INSTALL / "backend.service.in"
    template_text = updated_unit(template.read_text(), "@BRANCH@")
    # Validate every destination before changing any service/configuration.
    if template.is_symlink() or template.stat().st_uid != 0:
        raise ValueError("Unexpected template ownership")
    write_private(path, "".join(f"{key}={value}\n" for key, value in sorted(values.items())), 0o600)
    write_private(template, template_text, 0o644)
    for unit, content, _ in prepared:
        write_private(unit, content, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    for unit, _, port in prepared:
        subprocess.run(["systemctl", "restart", unit.name], check=True)
        for attempt in range(90):
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    if response.status == 200:
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Preview backend readiness failed; inspect private journal")
    print("Private metadata defaults installed; preview backends healthy. No databases changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Even parser/OS errors must never accidentally echo secret input.
        print("Provider-default installation failed; check ownership/configuration and backend health privately.", file=sys.stderr)
        sys.exit(1)
