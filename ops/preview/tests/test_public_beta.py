import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DIRECTORY = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('public_beta', DIRECTORY / 'public_beta.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class PublicBetaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = Path(self.folder.name) / 'Caddyfile'
        patcher = patch.multiple(gate, INSTALL=DIRECTORY, CADDYFILE=self.config,
                                 BACKUP=Path(self.folder.name) / 'backup')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.production = 'anylist.win {\n    reverse_proxy anylist-app:7330\n}\n\n'
        self.config.write_text(self.production + gate.block(False))

    def test_toggle_preserves_all_other_gateway_routes(self):
        original = self.config.read_text()
        enabled = gate.replace_block(original, True)
        self.assertTrue(enabled.startswith(self.production))
        self.assertIn('reverse_proxy unix//config/anylist-beta.sock', enabled)
        self.assertNotIn('respond "Beta is currently unavailable."', enabled)
        self.assertEqual(gate.replace_block(enabled, False), original)
        self.assertEqual(gate.configured_mode(), 'OFF')
        self.config.write_text(enabled)
        self.assertEqual(gate.configured_mode(), 'ON')

    def test_missing_or_duplicate_managed_block_is_refused(self):
        for config in (self.production, gate.block(False) * 2, gate.BEGIN):
            with self.subTest(config=config), self.assertRaises(RuntimeError):
                gate.replace_block(config, True)

    def test_validation_failure_does_not_change_active_configuration(self):
        original = self.config.read_text()
        with patch.object(gate, 'validate', side_effect=RuntimeError('invalid')), \
             patch.object(gate, 'command') as run:
            with self.assertRaises(RuntimeError):
                gate.set_public(True)
        self.assertEqual(self.config.read_text(), original)
        run.assert_not_called()

    def test_reload_failure_disables_socket_and_restores_off_configuration(self):
        original = self.config.read_text()
        with patch.object(gate, 'validate'), patch.object(gate, 'command') as run, \
             patch.object(gate, 'reload_gateway', side_effect=RuntimeError('reload')):
            with self.assertRaises(RuntimeError):
                gate.set_public(True)
        self.assertEqual(self.config.read_text(), original)
        run.assert_called_once_with('systemctl', 'stop', gate.SOCKET, gate.SERVICE)

    def test_failed_off_never_reopens_a_previously_enabled_route(self):
        self.config.write_text(self.production + gate.block(True))
        with patch.object(gate, 'validate'), patch.object(gate, 'command') as run, \
             patch.object(gate, 'reload_gateway'), \
             patch.object(gate, 'verify_public', side_effect=RuntimeError('unreachable')):
            with self.assertRaises(RuntimeError):
                gate.set_public(False)
        self.assertEqual(gate.configured_mode(), 'OFF')
        self.assertNotIn(('systemctl', 'start', gate.SOCKET), [call.args for call in run.call_args_list])

    def test_success_requires_live_verification_and_keeps_bind_mount_inode(self):
        inode = self.config.stat().st_ino
        with patch.object(gate, 'validate'), patch.object(gate, 'command') as run, \
             patch.object(gate, 'reload_gateway'), patch.object(gate, 'verify_public') as verify:
            gate.set_public(True)
        verify.assert_called_once_with(True)
        run.assert_called_once_with('systemctl', 'start', gate.SOCKET)
        self.assertEqual(gate.configured_mode(), 'ON')
        self.assertEqual(self.config.stat().st_ino, inode)

    def test_boot_restore_uses_persisted_gate_configuration(self):
        unit = (DIRECTORY / 'public-beta-restore.service').read_text()
        self.assertIn("public_beta.configured_mode() == 'ON'", unit)
        self.assertIn('public_beta.disable_socket()', unit)

    @unittest.skipUnless(sys.platform == 'linux', 'Installed controller uses Linux file locks')
    def test_isolated_controller_loads_only_the_installed_public_gate(self):
        trusted = Path(self.folder.name) / 'installed'
        trusted.mkdir()
        for name in ['controller.py', 'public_beta.py', 'public-beta.caddy.in', 'slots.json']:
            source = (DIRECTORY / name).read_text().replace('/opt/anylist-preview', trusted.as_posix())
            source = source.replace('/var/lib/anylist-preview', (trusted / 'state').as_posix())
            source = source.replace('/opt/public-gateway/Caddyfile', self.config.as_posix())
            (trusted / name).write_text(source)
        wrapper = ('import os,runpy,sys; os.geteuid=lambda:0; '
                   f'sys.argv=[{str(trusted / "controller.py")!r},"public-beta-status","beta"]; '
                   f'runpy.run_path({str(trusted / "controller.py")!r},run_name="__main__")')
        result = subprocess.run([sys.executable, '-I', '-c', wrapper], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Public beta.anylist OFF', result.stdout)


if __name__ == '__main__':
    unittest.main()
