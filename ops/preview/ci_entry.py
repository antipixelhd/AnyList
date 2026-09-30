#!/usr/bin/python3
"""Forced SSH command: no interactive shell, forwarding, or arbitrary command."""
import os
import shlex

from controller import load_slots, validate


def main():
    args = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if len(args) not in (3, 5) or args[0] != "preview":
        raise ValueError("Only preview operation branch [sha forced] is permitted")
    operation, branch = args[1:3]
    sha, forced = args[3:] if len(args) == 5 else (None, "false")
    validate(load_slots(), branch, operation, sha, forced)
    if operation != "deploy" and len(args) != 3:
        raise ValueError("Unexpected arguments")
    os.execv("/usr/bin/sudo", ["sudo", "-n", "/usr/bin/python3",
                             "/opt/anylist-preview/gateway.py", *args[1:]])


if __name__ == "__main__":
    main()
