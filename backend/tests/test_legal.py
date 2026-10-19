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
                      contact_email='contact@example.test', hosting_details='', retention_details='', transfer_details='')
        settings = SimpleNamespace(**{'legal_' + key: value for key, value in values.items()}, secret_key='never-export-this')
        with patch('routers.legal.settings', settings):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/legal/config')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), values)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('never-export-this', response.text)
