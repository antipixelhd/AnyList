import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('SECRET_KEY', 'legal-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI
from routers.legal import router


class LegalDisclosureTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_disclosures_are_explicit_and_never_export_other_settings(self):
        app = FastAPI()
        app.include_router(router)
        values = dict(operator_name='Example Operator', operator_address='Example Street',
                      contact_email='contact@example.test', hosting_details='Host', retention_details='Retention', transfer_details='Transfers')
        settings = SimpleNamespace(legal_pages_enabled=True, legal_privacy_email='privacy@example.test', **{'legal_' + key: value for key, value in values.items()}, secret_key='never-export-this')
        with patch('routers.legal.settings', settings):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/legal/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'enabled':True, **values, 'privacy_email':'privacy@example.test'})
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('never-export-this', response.text)

    async def test_disabled_or_incomplete_notices_do_not_disclose_operator_data(self):
        app = FastAPI()
        app.include_router(router)
        for enabled, retention in [(False, 'Retention'), (True, '')]:
            settings = SimpleNamespace(legal_pages_enabled=enabled, legal_privacy_email='privacy@example.test', legal_operator_name='Private Name',
                legal_operator_address='Private Address', legal_contact_email='private@example.test',
                legal_hosting_details='Host', legal_retention_details=retention, legal_transfer_details='Transfers')
            with self.subTest(enabled=enabled, retention=retention), patch('routers.legal.settings', settings):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    response = await client.get('/legal/config')
                self.assertFalse(response.json()['enabled'])
                self.assertTrue(all(value == '' for key, value in response.json().items() if key != 'enabled'))
                self.assertNotIn('Private', response.text)

    async def test_privacy_contact_falls_back_to_general_contact(self):
        app = FastAPI()
        app.include_router(router)
        settings = SimpleNamespace(legal_pages_enabled=True, legal_privacy_email='',
            legal_operator_name='Example', legal_operator_address='Example Street',
            legal_contact_email='support@example.test', legal_hosting_details='Host',
            legal_retention_details='Retention', legal_transfer_details='Transfers')
        with patch('routers.legal.settings', settings):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/legal/config')
        self.assertTrue(response.json()['enabled'])
        self.assertEqual(response.json()['privacy_email'], 'support@example.test')
