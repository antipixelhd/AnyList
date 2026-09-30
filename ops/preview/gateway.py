#!/usr/bin/python3
"""Detach deployment lifetime from the runner/SSH connection; stream journald."""
import subprocess
import sys
import uuid

sys.path.insert(0, "/opt/anylist-preview")
from controller import load_slots, validate


def main():
    args = sys.argv[1:]
    if len(args) not in (2, 4):
        raise ValueError("Invalid argument count")
    operation, branch = args[:2]
    sha, forced = args[2:] if len(args) == 4 else (None, "false")
    validate(load_slots(), branch, operation, sha, forced)
    if operation != "deploy" and len(args) != 2:
        raise ValueError("Unexpected arguments")
    unit = "anylist-preview-job-" + uuid.uuid4().hex
    # No timeout/RuntimeMaxSec. Losing SSH or cancelling a runner cannot kill a migration.
    job = subprocess.Popen(["systemd-run", "--wait", "--unit", unit,
                            "--property=Type=exec", "/usr/bin/python3", "-I", "-u",
                            "/opt/anylist-preview/controller.py", *args])
    logs = subprocess.Popen(["journalctl", "--follow", "--unit", unit, "--output=cat", "--since=now"])
    try:
        code = job.wait()
    finally:
        logs.terminate()
        logs.wait()
    # Replay full job output so short jobs/final lines are never lost in journal follow races.
    subprocess.run(["journalctl", "--unit", unit, "--output=cat", "--no-pager"], check=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
