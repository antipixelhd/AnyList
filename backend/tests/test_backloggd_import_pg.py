"""Backloggd upload, catalogue eligibility and additive game-list writes."""
from collections import Counter
import json
import re
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

import test_games_pg as fixture
from core import backloggd_import
from core.catalogue_providers import ProviderError
from models.catalogue import CatalogueIdentity
from models.games import GameEntry


PATH = '/tracking/games/import/backloggd'


def row(native, title, status='', **log):
    return {'id': str(native), 'name': title, 'game_log': {'status': status, **log}}


@unittest.skipUnless(fixture.URL, 'Requires disposable Games PostgreSQL')
class BackloggdImportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.GamesDatabaseTests.asyncSetUp
    cleanup = fixture.GamesDatabaseTests.cleanup

    async def test_preview_and_import_are_additive(self):
        records = [
            row(8101, 'First Game', 'completed', rating=8, total_hours=103),
            row(8102, 'Second Game', 'played', rating=0),
            row(8103, 'Third Game', 'shelved'),
            row(8104, 'Fourth Game', 'retired'),
            row(8105, 'Fifth Game', 'completed', is_playing=True),
            row(8106, 'Sixth Game', 'completed', is_wishlist=True),
            row(8101, 'First Game', 'played'),
            row(8107, 'Wrong Name', 'played'),
            row(8108, 'Mobile Game', 'completed'),
            row(8109, 'Expansion', 'completed'),
            row(8110, 'Missing Game', 'completed'),
            row(8111, 'Unknown Status', 'unknown'),
            row(8112, 'Abandoned Game', 'abandoned', is_play=True),
        ]
        provider = {int(r['id']): {'id': int(r['id']), 'name': r['name'], 'game_type': 0} for r in records[:6]}
        provider.update({8107: {'id': 8107, 'name': 'Different Name', 'game_type': 0},
            8108: {'id': 8108, 'name': 'Mobile Game', 'game_type': 0, 'platforms': [{'id': 34, 'name': 'Android'}]},
            8109: {'id': 8109, 'name': 'Expansion', 'game_type': 2},
            8112: {'id': 8112, 'name': 'Abandoned Game', 'game_type': 0}})

        async def catalogue_rows(endpoint, query):
            self.assertEqual(endpoint, 'games')
            ids = re.search(r'where id=\(([0-9,]+)\)', query)[1].split(',')
            return [provider[int(native)] for native in ids if int(native) in provider]

        with patch.object(backloggd_import.GameBrowseSources, 'igdb', side_effect=catalogue_rows) as igdb:
            response = await self.client.post(f'{PATH}/preview', content=b'\xef\xbb\xbf'+json.dumps(records).encode())
        self.assertEqual(response.status_code, 200, response.text)
        igdb.assert_awaited_once()
        result = response.json()
        self.assertEqual(Counter(g['status'] for g in result['games']),
            {'completed': 1, 'played': 1, 'paused': 1, 'dropped': 2, 'playing': 1, 'planning': 1})
        self.assertEqual(len(result['ignored']), 6)

        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(GameEntry.id).where(GameEntry.user_id == self.user.id)))
            # Simulate a user adding one game on another device while reviewing.
            second = await db.scalar(select(CatalogueIdentity.entity_id).where(CatalogueIdentity.external_id == '8102'))
            db.add(GameEntry(user_id=self.user.id, work_id=second, status='planning', score=5))
            await db.commit()

        selected = [{key: g[key] for key in ('work_id', 'status', 'score', 'total_seconds')} for g in result['games']]
        selected[0]['score'] = 8.5
        response = await self.client.post(PATH, json={'games': selected})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['games']), 6)
        self.assertEqual(response.json()['ignored'], [{'title': 'Second Game', 'reason': 'Already on your list'}])
        repeated = await self.client.post(PATH, json={'games': selected})
        self.assertEqual(repeated.json()['games'], [])
        self.assertEqual(len(repeated.json()['ignored']), 7)

        async with self.Session() as db:
            first = await db.scalar(select(GameEntry).where(GameEntry.work_id == selected[0]['work_id']))
            self.assertEqual((first.score, first.total_seconds, first.finish_date, first.completion_source),
                             (8.5, 370800, None, 'unknown'))
            existing = await db.scalar(select(GameEntry).where(GameEntry.work_id == second))
            self.assertEqual((existing.status, existing.score), ('planning', 5))

        with patch.object(backloggd_import.GameBrowseSources, 'igdb', new_callable=AsyncMock) as igdb:
            response = await self.client.post(f'{PATH}/preview', json=records[:6])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['games'], [])
        self.assertEqual(len(response.json()['ignored']), 6)
        igdb.assert_not_awaited()

    async def test_optional_logs_and_port_evidence(self):
        export = backloggd_import.parse_export(b'[{"id":"8203","name":"Unlogged","playthroughs":{},"most_recent_playthrough_id":null}]')
        parent = {'id': 8201, 'name': 'Phone Original', 'game_type': 0, 'platforms': [{'id': 34, 'name': 'Android'}]}
        port = {'id': 8202, 'name': 'PC Port', 'game_type': 11, 'parent_game': parent,
                'platforms': [{'id': 6, 'name': 'PC (Microsoft Windows)'}]}
        async with self.Session() as db:
            missing = await backloggd_import.preview(db, self.user.id, export)
            self.assertEqual([(r.title, r.reason) for r in missing.ignored],
                             [('Unlogged', 'No supported Backloggd status')])
            with patch.object(backloggd_import.GameBrowseSources, 'igdb', new_callable=AsyncMock,
                              return_value=[parent, port]):
                result = await backloggd_import.preview(db, self.user.id, [
                    row(8202, 'PC Port', 'played'), row(8201, 'Phone Original', 'completed')])
            self.assertEqual([(g.title, g.status) for g in result.games], [('Phone Original', 'played')])
            self.assertEqual([r.title for r in result.ignored], ['Phone Original'])

    async def test_rejects_invalid_files_and_reports_provider_outage(self):
        response = await self.client.post(f'{PATH}/preview', content=b'not json')
        self.assertEqual(response.status_code, 422, response.text)
        with patch.object(backloggd_import.GameBrowseSources, 'igdb', side_effect=ProviderError('bad_response')):
            response = await self.client.post(f'{PATH}/preview', json=[row(9000, 'Retry', 'played')])
        self.assertEqual(response.status_code, 503, response.text)
        self.viewer.games_access = False
        response = await self.client.post(f'{PATH}/preview', json=[])
        self.assertEqual(response.status_code, 403, response.text)
