"""Real contributor routes: game identity, bounded paging and private projections."""
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

import test_contributor_details_pg as fixture
from dependencies import get_current_user_or_api_key
from models import User
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueRelationship
from models.games import GameChildState, GameEntry


@unittest.skipUnless(fixture.fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class GameContributorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ContributorDetailsTests.asyncSetUp(self)
        async with self.Session() as db:
            viewer = await db.get(User, self.user_id)
            viewer.games_access = True
            await db.commit()
            self.viewer = viewer
    asyncTearDown = fixture.ContributorDetailsTests.asyncTearDown
    request = fixture.ContributorDetailsTests.request

    async def seed(self, count=30):
        async with self.Session() as db:
            alias = CatalogueEntity(kind='person', name='Verified alias', canonical_person_id=self.person_id)
            company = CatalogueEntity(kind='organization', name='Game company', image_url='https://example.test/company.jpg')
            db.add_all([alias, company]); await db.flush()
            games = [CatalogueEntity(kind='game', name=f'Game {i:03}', image_url='https://example.test/cover.jpg',
                attributes={'game_class': 'game', 'release_date': f'{2000+i}-01-01',
                    'artwork':['https://images.igdb.com/igdb/image/upload/t_thumb/ar123.jpg'], 'source_payload': 'large '*1000}) for i in range(count)]
            db.add_all(games); await db.flush()
            for i, game in enumerate(games):
                db.add_all([CatalogueCredit(work_id=game.id, contributor_id=alias.id if i % 2 else self.person_id,
                    provider='wikidata', source_key=f'voice-{i}', role='voice_actor', role_label='Voice actor',
                    character_label=f'Character {i}', attributes={'statement': 'evidence '*1000}),
                    CatalogueCredit(work_id=game.id, contributor_id=company.id, provider='igdb',
                    source_key=f'developer-{i}', role='developer')])
            db.add(CatalogueCredit(work_id=games[0].id, contributor_id=self.person_id,
                provider='wikidata', source_key='composer', role='composer'))
            db.add(GameEntry(user_id=self.user_id, work_id=games[0].id, status='completed', score=9, notes='private game note'))
            await db.commit()
            return games, alias.id, company.id

    async def test_complete_paging_aliases_and_domain_boundaries_without_editor_seeds(self):
        games, alias, company = await self.seed()
        first = await self.request(media_type='game')
        self.assertEqual(first.status_code, 200, first.text)
        one = first.json()
        two = (await self.request(media_type='game', page=2, cursor=one['next_cursor'], source='local')).json()
        self.assertEqual((one['known_works'], one['total'], one['list_count']), (30, 30, 1))
        self.assertEqual((len(one['works']), len(two['works'])), (24, 6))
        works = one['works'] + two['works']
        self.assertEqual([row['id'] for row in works], [g.id for g in reversed(games)])
        self.assertTrue(all(row['href'] == f'/games/{row["id"]}' and row['entry'] is None for row in works))
        self.assertNotIn('private game note', first.text + str(two))
        self.assertNotIn('source_payload', first.text)
        self.assertNotIn('statement', first.text)
        self.assertEqual(works[-1]['score'], 9)
        self.assertEqual(works[0]['backdrop'], 'https://images.igdb.com/igdb/image/upload/t_thumb/ar123.jpg')
        self.assertEqual((await self.request(media_type='all')).json()['works'][0]['type'], 'movie')
        self.assertEqual((await self.request('actor', f'catalogue:{alias}', media_type='game')).json()['known_works'], 30)
        staff = (await self.request('staff', media_type='game')).json()
        self.assertEqual([row['roles'] for row in staff['works']], [['Composer']])
        studio = (await self.request('studio', f'catalogue:{company}', media_type='game')).json()
        self.assertEqual((studio['known_works'], studio['roles']), (30, ['Developer']))
        self.assertIsNone(studio['banner'], 'An opaque JPEG must not become a recolored square banner')
        movies = await self.request('studio', f'catalogue:{company}', media_type='all')
        self.assertEqual(movies.status_code, 200, movies.text)
        self.assertEqual(movies.json()['works'], [])

    async def test_private_counts_status_and_anonymous_projection(self):
        await self.seed(3)
        listed = (await self.request(media_type='game', list_scope='in', status='completed')).json()
        self.assertEqual((listed['total'], listed['list_count'], len(listed['works'])), (1, 1, 1))
        self.assertEqual((await self.request(media_type='game', status='playing')).json()['total'], 0)
        async with self.Session() as db:
            other = User(email='other-game@example.test', username='other-game', api_key='other-game-key', games_access=True)
            db.add(other); await db.commit(); self.viewer = other
        other = (await self.request(media_type='game')).json()
        self.assertEqual(other['list_count'], 0)
        self.assertTrue(all(row['score'] is None and row['list_status'] is None for row in other['works']))
        self.viewer = None
        self.app.dependency_overrides[get_current_user_or_api_key] = lambda: self.viewer
        response = await self.request(media_type='game')
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual((await self.request(media_type='game', list_scope='in')).status_code, 401)

    async def test_verified_expansion_membership_retained_data_and_ports(self):
        games, _, _ = await self.seed(2)
        async with self.Session() as db:
            expansion = CatalogueEntity(kind='game', name='Expansion', attributes={'game_class': 'expansion'})
            port = CatalogueEntity(kind='game', name='Platform port', attributes={'game_class': 'port'})
            excluded = [CatalogueEntity(kind='game', name=kind, attributes={'game_class': kind}) for kind in ('dlc','pack','update','unknown','bundle')]
            retired = CatalogueEntity(kind='game', name='Retired credit', attributes={'game_class': 'game'})
            db.add_all([expansion, port, retired, *excluded]); await db.flush()
            db.add_all([CatalogueRelationship(source_id=expansion.id, target_id=games[0].id, relation='expansion', provider='igdb'),
                CatalogueRelationship(source_id=port.id, target_id=games[1].id, relation='port', provider='igdb')])
            for game in [expansion, port, retired, *excluded]:
                db.add(CatalogueCredit(work_id=game.id, contributor_id=self.person_id, provider='wikidata',
                    source_key=f'content-{game.id}', role='voice_actor', character_label=game.name,
                    attributes={'source_active': False} if game is retired else {}))
            base = await db.scalar(select(GameEntry).where(GameEntry.work_id == games[0].id))
            child = GameChildState(user_id=self.user_id, work_id=expansion.id, base_work_id=games[0].id,
                base_entry_id=base.id, status='paused', score=7)
            db.add(child); await db.commit(); child_id = child.id
        data = (await self.request(media_type='game')).json()
        self.assertEqual(data['known_works'], 3)
        by_id = {row['id']: row for row in data['works']}
        self.assertEqual(by_id[expansion.id]['list_status'], 'paused')
        self.assertEqual(by_id[expansion.id]['characters'], ['Expansion'])
        self.assertIn('Platform port', by_id[games[1].id]['characters'])
        self.assertEqual(data['list_count'], 2)
        async with self.Session() as db:
            child = await db.get(GameChildState, child_id); child.base_entry_id = None
            await db.commit()
        data = (await self.request(media_type='game', list_scope='in')).json()
        self.assertEqual((data['list_count'], data['total']), (1, 1))
        async with self.Session() as db:
            relation=await db.scalar(select(CatalogueRelationship).where(
                CatalogueRelationship.source_id==expansion.id,CatalogueRelationship.relation=='expansion'))
            relation.attributes={'source_active':False};await db.commit()
        data=(await self.request(media_type='game')).json()
        self.assertEqual(data['known_works'],2)
        self.assertNotIn(expansion.id,[row['id'] for row in data['works']])

    async def test_continuations_bind_scope_and_restart_when_collection_changes(self):
        await self.seed()
        first = (await self.request(media_type='game')).json()
        cursor = first['next_cursor']
        self.assertEqual((await self.request('staff', media_type='game', cursor=cursor)).status_code, 422)
        self.assertEqual((await self.request(media_type='game', list_scope='in', cursor=cursor)).status_code, 422)
        self.assertEqual((await self.request(media_type='game', cursor='[]')).status_code, 422)
        async with self.Session() as db:
            game = CatalogueEntity(kind='game', name='New verified work', attributes={'game_class':'game'})
            db.add(game); await db.flush()
            db.add(CatalogueCredit(work_id=game.id, contributor_id=self.person_id, provider='wikidata', source_key='new', role='actor'))
            await db.commit()
        self.assertEqual((await self.request(media_type='game', cursor=cursor)).status_code, 409)
        self.assertEqual((await self.request(media_type='game')).json()['total'], 31)

    async def test_actor_crew_and_company_portfolios_exclude_dlc_before_counts_and_paging(self):
        games, _, company = await self.seed(2)
        async with self.Session() as db:
            expansion = CatalogueEntity(kind='game', name='Verified expansion', attributes={'game_class':'expansion'})
            orphan = CatalogueEntity(kind='game', name='Unverified expansion', attributes={'game_class':'expansion'})
            extras = [CatalogueEntity(kind='game', name=f'Add-on {i}', attributes={'game_class':kind})
                for i, kind in enumerate(('dlc','pack','update','bundle','unknown') * 6)]
            db.add_all([expansion, orphan, *extras]); await db.flush()
            db.add(CatalogueRelationship(source_id=expansion.id, target_id=games[0].id,
                relation='expansion', provider='igdb'))
            for work in [*games, expansion, orphan, *extras]:
                for contributor, role in ((self.person_id,'voice_actor'), (self.person_id,'composer'), (company,'developer')):
                    db.add(CatalogueCredit(work_id=work.id, contributor_id=contributor, provider='igdb',
                        source_key=f'portfolio-{work.id}-{role}', role=role))
            await db.commit()
        expected = {games[0].id, games[1].id, expansion.id}
        for kind, contributor in (('actor',self.person_id), ('staff',self.person_id), ('studio',company)):
            with self.subTest(kind=kind):
                response = await self.request(kind, f'catalogue:{contributor}', media_type='game')
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual((data['known_works'], data['total'], data['has_more']), (3,3,False))
                self.assertEqual({row['id'] for row in data['works']}, expected)
                self.assertEqual(data['list_count'], 1)
                self.assertIsNone(data['next_cursor'])

    async def test_profile_enrichment_does_not_fetch_movie_filmography_or_import(self):
        from models import Media
        from models.base import MediaType
        from models.catalogue import CatalogueIdentity
        await self.seed(2)
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.person_id, namespace='tmdb.person', external_id='77', source='tmdb'))
            db.add(Media(title='Legacy person', media_type=MediaType.person, tmdb_id=77,
                tmdb_data={'birthday':'1970-01-01','combined_credits':{'cast':[{'id':i,'title':'Filmography fixture'} for i in range(5000)]}}))
            await db.commit()
            before = await db.scalar(select(func.count()).select_from(CatalogueEntity))
        self.key_mock.return_value = 'fixture'
        with patch('core.tmdb.get_person', AsyncMock(side_effect=AssertionError('filmography fan-out'))) as full, \
             patch('core.tmdb.get_person_profile', AsyncMock(return_value={'id':77, 'name':'Profile', 'biography':'Verified biography'})) as profile:
            data = (await self.request(media_type='game')).json()
            self.assertEqual(data['description'], 'Verified biography')
            self.assertEqual(data['birthday'], '1970-01-01')
            self.assertNotIn('Filmography fixture', str(data))
            full.assert_not_awaited(); profile.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity)), before)

    async def test_game_contributor_previews_do_not_expose_restricted_works(self):
        from routers.sharing import router
        from core import share_preview
        await self.seed()
        self.app.include_router(router)
        path=f'/actors/catalogue:{self.person_id}?media_type=game'
        with patch.object(share_preview,'fetch_image',AsyncMock()) as fetch:
            for endpoint in ('metadata','image'):
                response=await self.client.get(f'/sharing/{endpoint}',params={'path':path})
                self.assertEqual(response.status_code,403,response.text)
                self.assertNotIn('Known games',response.text)
            fetch.assert_not_awaited()

    async def test_imdb_person_component_is_game_only_and_reads_never_fetch_its_connection(self):
        from models.catalogue import CatalogueIdentity,MetadataSnapshot
        from core.game_imdb_person import COMPONENT
        await self.seed(2)
        async with self.Session() as db:
            person=await db.get(CatalogueEntity,self.person_id)
            person.description=None
            person.attributes={}
            db.add(CatalogueIdentity(entity_id=self.person_id,namespace='imdb.person',external_id='nm1',source='tmdb'))
            db.add(MetadataSnapshot(provider='imdb',kind=COMPONENT,external_id='nm1',entity_id=self.person_id,
                payload={'profile':{'biography':'Restricted game biography','birthday':'1970-01-01'}},
                coverage={'source_complete':True,'state':'partial','unmapped_games':7}))
            await db.commit()
        with patch('core.game_imdb_source.IMDbGameSource.credits',AsyncMock()) as source:
            game=await self.request(media_type='game')
            self.assertEqual(game.status_code,200,game.text)
            self.assertEqual(game.json()['description'],'Restricted game biography')
            self.assertTrue(game.json()['game_person_status']['pending'])
            self.assertNotIn('profile',game.json()['game_person_status'])
            self.viewer.games_access=False
            denied=await self.request(media_type='game')
            self.assertEqual(denied.status_code,403)
            public=await self.request(media_type='movie')
            self.assertEqual(public.status_code,200,public.text)
            self.assertNotIn('Restricted game biography',public.text)
            self.assertIsNone(public.json()['game_person_status'])
            source.assert_not_awaited()

    async def test_title_roles_take_display_precedence_over_sparse_person_connection_evidence(self):
        games,_,_=await self.seed(2)
        async with self.Session() as db:
            db.add(CatalogueCredit(work_id=games[0].id,contributor_id=self.person_id,provider='imdb',
                source_key='person-evidence',role='actor',role_label='Actor',scope='person',
                attributes={'imdb_person_evidence':True,'source_active':True}))
            await db.commit()
        result=(await self.request(media_type='game')).json()
        self.assertEqual(result['roles'],['Voice actor'])
        self.assertTrue(all(row['roles']==['Voice actor'] for row in result['works']))
        async with self.Session() as db:
            # If title evidence is withdrawn, participation remains visible
            # with unknown voice/character details from the person connection.
            detailed=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==games[0].id,
                CatalogueCredit.role=='voice_actor'))
            detailed.attributes={'source_active':False};await db.commit()
        result=(await self.request(media_type='game')).json()
        self.assertIn('Actor',result['roles'])
        self.assertEqual(next(row for row in result['works'] if row['id']==games[0].id)['characters'],[])
