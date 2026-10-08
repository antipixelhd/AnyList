import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).parents[1]
sys.path.insert(0, str(DIRECTORY))
import controller

SPEC = importlib.util.spec_from_file_location("ci_entry", DIRECTORY / "ci_entry.py")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
SLOTS = controller.load_slots(DIRECTORY / "slots.json")
SHA = "a" * 40


class LoginShellTests(unittest.TestCase):
    def test_production_only_accepts_fixed_deploy_and_commit(self):
        self.assertEqual(entry.parse_command(['-c', f'anylist deploy {SHA}'], SLOTS),
                         ['production-deploy', SHA])
        for command in ['anylist deploy latest', 'anylist stop app',
                        f'anylist deploy {SHA}; id', f'anylist deploy {SHA} --privileged',
                        f'anylist deploy {SHA}\nid', 'anylist deploy ' + 'a' * 64]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                entry.parse_command(['-c', command], SLOTS)

    def test_tailscale_remote_commands_are_validated_as_arguments(self):
        self.assertEqual(entry.parse_command(["-c", "preview restart beta"], SLOTS),
                         ["restart", "beta"])
        self.assertEqual(entry.parse_command(["-l", "-c", f"preview deploy development-1 {SHA} true"], SLOTS),
                         ["deploy", "development-1", SHA, "true"])

    def test_interactive_shell_sftp_and_arbitrary_execution_are_refused(self):
        for args in [[], ["-l"], ["-i"], ["-c", "bash"], ["-c", "id"],
                     ["-c", "sudo -i"], ["-c", "internal-sftp"],
                     ["-c", "/usr/sbin/tailscaled be-child sftp"],
                     ["-c", "preview status beta", "extra"]]:
            with self.subTest(args=args), self.assertRaises((ValueError, controller.Refused)):
                entry.parse_command(args, SLOTS)

    def test_injections_beta_reset_and_invalid_arguments_are_refused(self):
        for command in ["preview status beta; id", "preview status $(id)",
                        "preview status `id`", "preview status beta > /tmp/file",
                        "preview status beta\nid", "preview reset-db-from-beta beta",
                        "preview status main", "preview status beta extra",
                        "preview deploy beta HEAD false", f"preview deploy beta {SHA} yes"]:
            with self.subTest(command=command), self.assertRaises((ValueError, controller.Refused)):
                entry.parse_command(["-c", command], SLOTS)

    def test_original_command_environment_cannot_authorize_interactive_login(self):
        with patch.dict(entry.os.environ, {"SSH_ORIGINAL_COMMAND": "preview status beta"}), \
             patch.object(entry, "load_slots", return_value=SLOTS), \
             patch.object(entry.sys.stdin, "isatty", return_value=False), \
             patch.object(entry.sys.stdout, "isatty", return_value=False), \
             patch.object(entry.os, "execv") as execute:
            with self.assertRaises(ValueError):
                entry.main([])
            execute.assert_not_called()

    def test_pty_sessions_are_refused_before_privileged_execution(self):
        with patch.object(entry.sys.stdin, "isatty", return_value=True), \
             patch.object(entry.os, "execv") as execute:
            with self.assertRaises(ValueError):
                entry.main(["-c", "preview status beta"])
            execute.assert_not_called()

    def test_valid_command_executes_only_isolated_python_gateway(self):
        with patch.object(entry, "load_slots", return_value=SLOTS), \
             patch.object(entry.sys.stdin, "isatty", return_value=False), \
             patch.object(entry.sys.stdout, "isatty", return_value=False), \
             patch.object(entry.os, "execv") as execute:
            entry.main(["-c", "preview status beta"])
            execute.assert_called_once_with("/usr/bin/sudo", ["sudo", "-n", "/usr/bin/python3",
                                            "-I", "/opt/anylist-preview/gateway.py", "status", "beta"])

    def test_isolated_login_process_ignores_forwarded_pythonpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            trusted = Path(temporary) / "installed"
            evil = Path(temporary) / "untrusted"
            trusted.mkdir()
            evil.mkdir()
            marker = Path(temporary) / "executed"
            (evil / "controller.py").write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()")
            for name in ("controller.py", "ci_entry.py"):
                (trusted / name).write_text((DIRECTORY / name).read_text().replace(
                    "/opt/anylist-preview", trusted.as_posix()))
            (trusted / "slots.json").write_text((DIRECTORY / "slots.json").read_text())
            result = subprocess.run([sys.executable, "-I", str(trusted / "ci_entry.py"), "-c", "id"],
                                    env={**os.environ, "PYTHONPATH": str(evil),
                                         "SSH_ORIGINAL_COMMAND": "preview status beta"},
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Preview command refused", result.stderr)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
