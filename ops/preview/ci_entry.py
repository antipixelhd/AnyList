#!/usr/bin/python3 -I
"""Restricted login shell for the Tailscale SSH deployment account.

Tailscale invokes the user's shell with -c and the raw remote command. Never
evaluate that string in a real shell, or trust SSH_ORIGINAL_COMMAND/environment.
"""
import os
import shlex
import sys

sys.path.insert(0, "/opt/anylist-preview")
from controller import load_slots, validate


def parse_command(shell_args, slots):
    if shell_args[:1] == ["-l"]:
        shell_args = shell_args[1:]
    if len(shell_args) != 2 or shell_args[0] != "-c":
        raise ValueError("Interactive login is disabled; use a preview command")
    command = shell_args[1]
    if len(command) > 4096:
        raise ValueError("Command too long")
    args = shlex.split(command)
    if len(args) not in (3, 5) or args[0] != "preview":
        raise ValueError("Only preview operation branch [sha forced] is permitted")
    operation, branch = args[1:3]
    sha, forced = args[3:] if len(args) == 5 else (None, "false")
    validate(slots, branch, operation, sha, forced)
    if operation != "deploy" and len(args) != 3:
        raise ValueError("Unexpected arguments")
    return args[1:]


def main(shell_args=None):
    if sys.stdin.isatty() or sys.stdout.isatty():
        raise ValueError("PTY sessions are disabled for preview CI")
    args = parse_command(sys.argv[1:] if shell_args is None else shell_args, load_slots())
    os.execv("/usr/bin/sudo", ["sudo", "-n", "/usr/bin/python3",
                             "-I", "/opt/anylist-preview/gateway.py", *args])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Preview command refused: {error}", file=sys.stderr)
        sys.exit(1)
