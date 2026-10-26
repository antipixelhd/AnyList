#!/usr/bin/env python3
"""Reviewed administrator bootstrap helper; never a CI gateway operation.

Read a JSON object of metadata defaults on stdin. Values never enter argv/logs.
Only the preview backend environment and units are updated; no database access.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

KEYS = {
    "TMDB_API_KEY",
    "TVDB_API_KEY",
    "TVDB_SUBSCRIBER_PIN",
    "IGDB_CLIENT_ID",
    "IGDB_CLIENT_SECRET",
    "HARDCOVER_API_KEY",
    "RAWG_API_KEY",
    "ITAD_API_KEY",
    "STEAM_WEB_API_KEY",
    "OPENLIBRARY_CONTACT_EMAIL",
    "HARDCOVER_DAILY_BUDGET",
    "RAWG_MONTHLY_BUDGET",
}
ETC = Path("/etc/anylist-preview")
INSTALL = Path("/opt/anylist-preview")
UNITS = Path("/etc/systemd/system")
COMMON = "EnvironmentFile=-/etc/anylist-preview/providers.env"


def validate(values):
    if not isinstance(values, dict) or not values or set(values) - KEYS:
        raise ValueError("Only metadata defaults are accepted")
    for key, value in values.items():
        pattern = (
            r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
            if key == "OPENLIBRARY_CONTACT_EMAIL"
            else r"[0-9]+"
            if key in {"HARDCOVER_DAILY_BUDGET", "RAWG_MONTHLY_BUDGET"}
            else r"[A-Za-z0-9._-]+"
        )
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("Invalid metadata credential encoding")
    if (
        "HARDCOVER_DAILY_BUDGET" in values
        and int(values["HARDCOVER_DAILY_BUDGET"]) > 5000
    ):
        raise ValueError("Daily budget exceeds provider limit")
    if "RAWG_MONTHLY_BUDGET" in values and int(values["RAWG_MONTHLY_BUDGET"]) > 20000:
        raise ValueError("Monthly budget exceeds provider limit")
    return values


def updated_unit(text, branch):
    marker = f"EnvironmentFile=/etc/anylist-preview/{branch}.env"
    if text.count(marker) != 1:
        raise ValueError("Unexpected backend unit; refusing modification")
    if COMMON in text:
        return text
    return text.replace(marker, COMMON + "\n" + marker)


def write_private(path, content, mode, *, gid=None):
    if path.is_symlink():
        raise ValueError("Refusing symlink destination")
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.chmod(name, mode)
        if gid is not None:
            os.chown(name, 0, gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def root_owned(path, *, private=False):
    if path.is_symlink():
        raise ValueError("Refusing symlink configuration")
    info=path.stat()
    if info.st_uid!=0 or info.st_mode&0o022 or private and info.st_mode&0o777 not in (0o600,0o640):
        raise ValueError("Unexpected configuration ownership or mode")
    return info


def scoped_environment(text, values):
    """Preserve unrelated configuration byte-for-byte; never parse its secrets."""
    keep=[line for line in text.splitlines(keepends=True) if line.split('=',1)[0].strip() not in values]
    previous=''.join(keep)
    if previous and not previous.endswith('\n'):previous+='\n'
    return previous+''.join(f'{key}={value}\n' for key,value in sorted(values.items()))


def restart_backends(prepared):
    for unit, port in prepared:
        subprocess.run(["systemctl", "restart", unit.name], check=True)
        for attempt in range(90):
            healthy=False
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    healthy=response.status==200
            except (OSError,TimeoutError):
                healthy=False
            if healthy:break
            time.sleep(1)
        else:
            raise RuntimeError("Preview backend readiness failed; inspect private journal")


def install_scoped(values, branch, slots):
    if not isinstance(branch,str) or not re.fullmatch(r'beta|development-[1-9][0-9]*',branch) or branch not in slots:
        raise ValueError("Unknown preview scope")
    port=slots[branch]['backend_port']
    if type(port) is not int or not 1024<=port<=65535:
        raise ValueError("Invalid preview port")
    path=ETC/(branch+'.env')
    unit=UNITS/f'anylist-preview-{branch}-backend.service'
    info=root_owned(path,private=True);root_owned(unit)
    marker=f'EnvironmentFile=/etc/anylist-preview/{branch}.env'
    lines=[line.strip() for line in unit.read_text().splitlines() if line.strip().startswith('EnvironmentFile=')]
    if lines.count(marker)!=1 or lines[-1]!=marker:
        raise ValueError("Slot environment is not the final backend override")
    with path.open(encoding='utf-8',newline='') as stream:
        content=scoped_environment(stream.read(),validate(values))
    write_private(path,content,info.st_mode&0o777,gid=info.st_gid)
    restart_backends([(unit,port)])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',help='Update only this preview slot; leave shared defaults and other services unchanged')
    args=parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("Administrator bootstrap requires root")
    values = validate(json.load(sys.stdin))
    slots = json.loads((INSTALL / "slots.json").read_text())
    if args.preview is not None:
        install_scoped(values,args.preview,slots)
        print("Private metadata defaults installed for selected preview; backend healthy. No databases changed.")
        return
    path = ETC / "providers.env"
    if path.exists():
        root_owned(path)
        previous = dict(
            line.split("=", 1)
            for line in path.read_text().splitlines()
            if line and not line.startswith("#")
        )
        values = validate({**previous, **values})
    prepared = []
    for branch, slot in slots.items():
        if branch != "beta" and not re.fullmatch(r"development-[1-9][0-9]*", branch):
            raise ValueError("Invalid preview branch")
        port = slot["backend_port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("Invalid preview port")
        unit = UNITS / f"anylist-preview-{branch}-backend.service"
        if unit.exists():
            root_owned(unit)
            prepared.append((unit, updated_unit(unit.read_text(), branch), port))
    template = INSTALL / "backend.service.in"
    template_text = updated_unit(template.read_text(), "@BRANCH@")
    # Validate every destination before changing any service/configuration.
    root_owned(template)
    write_private(
        path,
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        0o600,
    )
    write_private(template, template_text, 0o644)
    for unit, content, _ in prepared:
        write_private(unit, content, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    restart_backends([(unit,port) for unit,_,port in prepared])
    print(
        "Private metadata defaults installed; preview backends healthy. No databases changed."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- Error details can contain secret input; fail with a fixed message.
        # Even parser/OS errors must never accidentally echo secret input.
        print(
            "Provider-default installation failed; check ownership/configuration and backend health privately.",
            file=sys.stderr,
        )
        sys.exit(1)
