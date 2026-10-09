import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("provider_installer", Path(__file__).parents[1] / "install-provider-defaults.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ProviderInstallerTests(unittest.TestCase):
    def test_only_metadata_keys_are_accepted(self):
        installer.validate({"TMDB_API_KEY": "test.jwt-token"})
        installer.validate({"IGDB_CLIENT_ID":"fake-id","IGDB_CLIENT_SECRET":"fake-secret",
                            "HARDCOVER_API_KEY":"hc_pat_fake","RAWG_API_KEY":"fake","ITAD_API_KEY":"fake"})
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
