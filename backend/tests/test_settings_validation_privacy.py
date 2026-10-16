import os
import unittest

os.environ.setdefault('SECRET_KEY', 'validation-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from pydantic import ValidationError
from core.config import Settings


class SettingsValidationPrivacyTests(unittest.TestCase):
    def test_invalid_settings_do_not_print_credential_inputs(self):
        with self.assertRaises(ValidationError) as error:
            Settings(_env_file=None, secret_key='validation-test-only', rawg_api_key='fixture-private-key',
                openlibrary_contact_email='invalid-private-contact')
        message = str(error.exception)
        self.assertIn('openlibrary_contact_email', message)
        self.assertNotIn('fixture-private-key', message)
        self.assertNotIn('invalid-private-contact', message)
        self.assertNotIn('input_value', message)
