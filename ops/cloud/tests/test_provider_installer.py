import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("provider_installer", Path(__file__).parents[1] / "install-provider-defaults.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ProviderInstallerTests(unittest.TestCase):
    def test_only_metadata_keys_are_accepted(self):
        installer.validate({"TMDB_API_KEY": "test.jwt-token"})
        installer.validate({"IGDB_CLIENT_ID":"fake-id","IGDB_CLIENT_SECRET":"fake-secret",
                            "HARDCOVER_API_KEY":"hc_pat_fake","RAWG_API_KEY":"fake","ITAD_API_KEY":"fake",
                            "STEAM_WEB_API_KEY":"fake-steam-key"})
        for values in ({}, {"DATABASE_URL": "postgresql"}, {"TMDB_API_KEY": "token\nOTHER=value"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                installer.validate(values)

    def test_contact_and_local_budget_accept_only_safe_environment_values(self):
        installer.validate({"OPENLIBRARY_CONTACT_EMAIL": "contact@example.org", "HARDCOVER_DAILY_BUDGET": "5000"})
        for key,value in [("OPENLIBRARY_CONTACT_EMAIL", "contact@example.org\nOTHER=value"), ("OPENLIBRARY_CONTACT_EMAIL", "no-contact"), ("HARDCOVER_DAILY_BUDGET", "-1")]:
            with self.assertRaises(ValueError):
                installer.validate({key:value})

    def test_provider_budgets_match_settings_bounds(self):
        for key, maximum in [("HARDCOVER_DAILY_BUDGET", 5000), ("RAWG_MONTHLY_BUDGET", 20000)]:
            for valid in ("0", "19000" if key == "RAWG_MONTHLY_BUDGET" else "4999", str(maximum)):
                with self.subTest(key=key, valid=valid):
                    self.assertEqual(installer.validate({key: valid}), {key: valid})
            for invalid in ("-1", "1.5", "20000\nOTHER=value", str(maximum + 1), 100):
                with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError):
                    installer.validate({key: invalid})

    def test_shared_defaults_precede_slot_override_and_are_idempotent(self):
        original = "[Service]\nEnvironmentFile=/etc/anylist-preview/beta.env\n"
        updated = installer.updated_unit(original, "beta")
        self.assertLess(updated.index(installer.COMMON), updated.index("EnvironmentFile=/etc/anylist-preview/beta.env"))
        self.assertEqual(installer.updated_unit(updated, "beta"), updated)

    def test_unexpected_unit_is_refused(self):
        with self.assertRaises(ValueError):
            installer.updated_unit("EnvironmentFile=/etc/production.env", "beta")

    def test_private_file_replaces_atomically_without_accepting_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "providers.env"
            installer.write_private(path, "TMDB_API_KEY=test\n", 0o600)
            self.assertEqual(path.read_text(), "TMDB_API_KEY=test\n")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                link = Path(temp) / "link"
                link.symlink_to(path)
                with self.assertRaises(ValueError):
                    installer.write_private(link, "replace", 0o600)

    def test_scoped_updates_preserve_unrelated_environment_and_are_idempotent(self):
        original='# private slot\nDATABASE_URL=postgresql://test:literal-value@localhost/fixture\nGAMES_ACCESS_DEFAULT=false\nSTEAM_WEB_API_KEY=old\n'
        values={'STEAM_WEB_API_KEY':'new-safe-key'}
        updated=installer.scoped_environment(original,values)
        self.assertEqual(updated,original.replace('STEAM_WEB_API_KEY=old','STEAM_WEB_API_KEY=new-safe-key'))
        self.assertEqual(installer.scoped_environment(updated,values),updated)
        self.assertEqual(installer.scoped_environment('OTHER=retained',values),
            'OTHER=retained\nSTEAM_WEB_API_KEY=new-safe-key\n')

    def test_only_selected_slot_is_written_and_restarted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            beta=folder/'beta.env';beta.write_text('DATABASE_URL=fixture\n')
            original_bytes=beta.read_bytes()
            other=folder/'development-1.env';other.write_text('UNCHANGED=other\n')
            shared=folder/'providers.env';shared.write_text('UNCHANGED=shared\n')
            unit=folder/'anylist-preview-beta-backend.service'
            unit.write_text('[Service]\n'+installer.COMMON+'\nEnvironmentFile=/etc/anylist-preview/beta.env\n')
            trusted=SimpleNamespace(st_mode=0o640,st_gid=123)
            with patch.object(installer,'ETC',folder),patch.object(installer,'UNITS',folder),patch.object(
                    installer,'root_owned',return_value=trusted),patch.object(installer,'restart_backends') as restart,patch.object(
                    installer.os,'chown',create=True) as ownership:
                installer.install_scoped({'STEAM_WEB_API_KEY':'test-key'},'beta',{
                    'beta':{'backend_port':8101},'development-1':{'backend_port':8102}})
            self.assertEqual(beta.read_text(),'DATABASE_URL=fixture\nSTEAM_WEB_API_KEY=test-key\n')
            self.assertTrue(beta.read_bytes().startswith(original_bytes))
            self.assertEqual(other.read_text(),'UNCHANGED=other\n')
            self.assertEqual(shared.read_text(),'UNCHANGED=shared\n')
            restart.assert_called_once_with([(unit,8101)])
            ownership.assert_called_once()
            self.assertEqual(ownership.call_args.args[1:],(0,123))

    def test_unknown_scope_and_later_environment_override_are_refused_before_writing(self):
        for branch in ('main','../beta','beta/../../main','development-0','missing'):
            with self.assertRaises(ValueError):
                installer.install_scoped({'STEAM_WEB_API_KEY':'test'},branch,{'beta':{'backend_port':8101}})
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'beta.env').write_text('UNCHANGED=yes\n')
            unit=folder/'anylist-preview-beta-backend.service'
            unit.write_text('[Service]\nEnvironmentFile=/etc/anylist-preview/beta.env\nEnvironmentFile=/etc/late.env\n')
            with patch.object(installer,'ETC',folder),patch.object(installer,'UNITS',folder),patch.object(
                    installer,'root_owned'),patch.object(installer,'write_private') as write,patch.object(installer,'restart_backends') as restart:
                with self.assertRaises(ValueError):
                    installer.install_scoped({'STEAM_WEB_API_KEY':'test'},'beta',{'beta':{'backend_port':8101}})
                write.assert_not_called();restart.assert_not_called()

    def test_private_scope_rejects_untrusted_modes_and_owners(self):
        for uid,mode in ((1000,0o600),(0,0o666),(0,0o644),(0,0o660)):
            path=SimpleNamespace(is_symlink=lambda:False,stat=lambda uid=uid,mode=mode:SimpleNamespace(st_uid=uid,st_mode=mode))
            with self.assertRaises(ValueError):installer.root_owned(path,private=True)
        for mode in (0o600,0o640):
            path=SimpleNamespace(is_symlink=lambda:False,stat=lambda mode=mode:SimpleNamespace(st_uid=0,st_mode=mode))
            self.assertEqual(installer.root_owned(path,private=True).st_mode,mode)

    def test_default_shared_mode_still_prepares_all_preview_backends(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            slots={'beta':{'backend_port':8101},'development-1':{'backend_port':8102}}
            (folder/'slots.json').write_text(json.dumps(slots))
            (folder/'providers.env').write_text('TMDB_API_KEY=old-fixture\n')
            (folder/'backend.service.in').write_text('[Service]\nEnvironmentFile=/etc/anylist-preview/@BRANCH@.env\n')
            for branch in slots:
                (folder/f'anylist-preview-{branch}-backend.service').write_text(
                    f'[Service]\nEnvironmentFile=/etc/anylist-preview/{branch}.env\n')
            with patch.object(installer,'ETC',folder),patch.object(installer,'UNITS',folder),patch.object(
                    installer,'INSTALL',folder),patch.object(installer.os,'geteuid',return_value=0,create=True),patch.object(
                    installer,'root_owned'),patch.object(
                    installer.sys,'argv',['installer']),patch.object(installer.sys,'stdin',io.StringIO('{"STEAM_WEB_API_KEY":"test-key"}')),patch.object(
                    installer,'write_private') as write,patch.object(installer.subprocess,'run') as command,patch.object(
                    installer,'restart_backends') as restart,redirect_stdout(io.StringIO()):
                installer.main()
            self.assertEqual(write.call_count,4)
            self.assertEqual(write.call_args_list[0].args[:2],(folder/'providers.env',
                'STEAM_WEB_API_KEY=test-key\nTMDB_API_KEY=old-fixture\n'))
            command.assert_called_once_with(['systemctl','daemon-reload'],check=True)
            self.assertEqual([unit.name for unit,_ in restart.call_args.args[0]],
                ['anylist-preview-beta-backend.service','anylist-preview-development-1-backend.service'])

    def test_empty_explicit_scope_cannot_fall_back_to_shared_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'slots.json').write_text('{"beta":{"backend_port":8101}}')
            with patch.object(installer,'INSTALL',folder),patch.object(installer.os,'geteuid',return_value=0,create=True),patch.object(
                    installer.sys,'argv',['installer','--preview','']),patch.object(installer.sys,'stdin',io.StringIO(
                    '{"STEAM_WEB_API_KEY":"test-key"}')),patch.object(installer,'write_private') as write,patch.object(
                    installer,'restart_backends') as restart:
                with self.assertRaises(ValueError):installer.main()
                write.assert_not_called();restart.assert_not_called()
