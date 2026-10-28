"""Fresh person portfolios discover canonical games without opening their pages."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select,func,update

import test_games_pg as fixture
from test_game_imdb_person_pg import credit,page
from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_imdb_discovery import GamePersonDiscovery,JOB,MAP
from core.game_imdb_enrichment import now,snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment,view,COMPONENT
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit,MetadataSnapshot
from models.games import GameEntry


def outcome(native='tt2',igdb=20,kind=0,parent=None):
    raw={'id':igdb,'name':'Discovered game','slug':'discovered-game','game_type':{'id':kind},
        'summary':'Provider synopsis','first_release_date':1704067200}
    if parent:raw['parent_game']={'id':parent,'name':'Base','game_type':{'id':0}}
    return {'id':native,'status':'verified','igdb_id':igdb,'document':normalize_igdb(raw),
        'evidence':{'wikidata_id':'Q20','P345':[],'P5794':[]}}


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class PortfolioDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Existing movie person',description='Keep biography',attributes={})
            db.add(person);await db.flush();self.person=person.id
            work=await db.get(CatalogueEntity,self.base);work.attributes={**work.attributes,'igdb_id':10}
            db.add_all([CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id='nm1',source='tmdb'),
                CatalogueIdentity(entity_id=person.id,namespace='tmdb.person',external_id='42',source='tmdb'),
                CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=work.id,namespace='imdb.title',external_id='tt1',source='reviewed',
                    evidence={'title_type':'videoGame','igdb_id':'10'}),
                GameEntry(user_id=self.user.id,work_id=self.base,status='playing',total_seconds=3600,notes='Pending private state')])
            from core.game_imdb_person import request
            await request(db,person.id);await db.commit()
            self.original_games=await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game'))
        self.connection=SimpleNamespace(credits=AsyncMock(return_value=page([credit(),credit('tt2'),credit('tt3',role='director')],3)))
        self.person_service=GameIMDbPersonEnrichment(self.Session,source=self.connection)
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        self.source=SimpleNamespace(page=AsyncMock(return_value={'outcomes':[outcome(),outcome('tt3',30)],'stale':False}))
        self.service=GamePersonDiscovery(self.Session,source=self.source)

    async def admit(self):
        async with self.Session() as db:
            data=await view(db,'nm1',check_mapping=True);await db.commit()
        return data

    async def resume_policy(self,policy):
        from core.game_imdb_discovery import VERSION
        self.source.page.side_effect=lambda ids:{'outcomes':[{'id':native,'status':'unmapped'} for native in ids],'stale':False}
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'))
            job.payload={**job.payload,'page':0,'offset':2,'processed':2}
            job.coverage={'complete':False,'matcher_version':policy}
            await db.commit()
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'))
            self.assertEqual(job.coverage['matcher_version'],VERSION)
            self.assertEqual(job.payload['processed'],3)
        return self.source.page.await_args.args[0]

    async def test_policy_upgrade_rechecks_the_prefix_before_certifying_current_coverage(self):
        from core.game_imdb_discovery import VERSION
        self.assertEqual(await self.resume_policy(VERSION-1),['tt2','tt3'])

    async def test_current_policy_resumes_its_cursor_without_reacquiring_the_prefix(self):
        from core.game_imdb_discovery import VERSION
        self.assertEqual(await self.resume_policy(VERSION),['tt3'])

    async def automatic_edition(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from core.game_source_cache import GameSourceCache
        from core.game_imdb_person import JOB as PERSON_JOB
        from test_game_parent_proposals import automatic_proof
        proof=automatic_proof();source_credit=credit('tt2')
        source_credit['title'].update(titleText={'text':proof['imdb']['title']},releaseYear={'year':2017})
        self.connection.credits.return_value=page([source_credit],1)
        async with self.Session() as db:
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'));job.next_attempt_at=now();job.expires_at=None;job.payload={};await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async def games(endpoint,query):
            return [proof['variant']['parent']] if 'where (id=(10))' in query else [proof['variant']['child']]
        sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(side_effect=games),
            api=SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={'results':{'bindings':[]}}))))
        self.variant_service=GamePersonDiscovery(self.Session,source=GameIMDbDiscoverySource(self.Session,sources=sources))
        self.assertEqual(await self.variant_service.run('nm1'),'changed')
        self.assertEqual(await self.variant_service.run('nm1'),'ready')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            result=await db.scalar(snapshot(MAP,'tt2',provider='wikidata').with_only_columns(MetadataSnapshot.payload))
        self.assertEqual(result['status'],'verified',result)
        self.assertTrue(result['credit_parent']);self.assertEqual(result['work_id'],self.base)
        return result,sources

    async def test_automatic_edition_projects_one_parent_card_without_replacing_its_identity(self):
        from core.game_contributor_works import works_page
        from core.game_credit_parents import KIND
        result,sources=await self.automatic_edition()
        async with self.Session() as db:
            primary=await db.scalar(select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(primary,'tt1')
            self.assertIsNone(await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2')))
            record=await db.scalar(snapshot(KIND,'tt2'))
            self.assertTrue(record.coverage['automatic']);self.assertNotIn('reviewer_id',record.payload)
            works=await works_page(db,None,self.person,'actor',key='automatic',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(len(works['works']),1)
            self.assertEqual(works['works'][0]['href'],f'/games/{self.base}')
            self.assertEqual(works['works'][0]['credit_sources'],[{'kind':'edition','title':'Main game: Complete Edition'}])
            from core.game_imdb_person import project_mapped
            await project_mapped(db,'nm1',[credit('tt1'),credit('tt2')],'with-main-credit')
            both=await works_page(db,None,self.person,'actor',key='with-main',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(len(both['works']),1);self.assertFalse(both['works'][0].get('credit_sources'))
            entry=await db.scalar(select(GameEntry))
            self.assertEqual((entry.work_id,entry.notes,entry.total_seconds),(self.base,'Pending private state',3600))
        self.assertEqual(sources.igdb.await_count,3)
        self.assertEqual(sources.api.http.request.await_count,1)  # Reverse Wiki check, no cast/store fan-out.

    async def test_stale_automatic_parent_retains_existing_role_but_cannot_create_new_participation(self):
        from core.game_credit_parents import person_bindings,KIND
        from core.game_mapping_lifecycle import invalidate
        from core.game_imdb_person import queue_projection,project_mapped
        await self.automatic_edition()
        async with self.Session() as db:
            await invalidate(db,['igdb:20']);await db.commit()
            self.assertFalse((await db.execute(select(person_bindings(['tt2'])))).all())
            discovery=await db.scalar(snapshot(JOB,'nm1'))
            self.assertIsNone(discovery.expires_at)
            await queue_projection(db,'nm1');await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            old=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.scope=='person',CatalogueCredit.contributor_id==self.person,
                CatalogueCredit.attributes['imdb_title_id'].astext=='tt2'))
            self.assertTrue(old.attributes['source_active']);self.assertEqual(old.attributes['credited_child']['kind'],'edition')
            newcomer=CatalogueEntity(kind='person',name='New performer');db.add(newcomer);await db.flush()
            db.add(CatalogueIdentity(entity_id=newcomer.id,namespace='imdb.person',external_id='nm2',source='imdb'));await db.flush()
            result=await project_mapped(db,'nm2',[credit('tt2')],'new-generation')
            self.assertEqual(result['unmapped_games'],1)
            self.assertFalse(await db.scalar(select(CatalogueCredit.id).where(CatalogueCredit.contributor_id==newcomer.id)))
            await db.commit()

    async def test_automatic_parent_support_change_and_review_race_are_rejected(self):
        from core.game_credit_parents import save_automatic_parent,review_parent,reject_automatic_parents,KIND
        from core.game_source_cache import GameSourceCache
        from core import catalogue
        from models import User
        result,_=await self.automatic_edition();proof=result['evidence']
        async with self.Session() as db:
            candidate=await db.scalar(GameSourceCache.query('igdb','game_title_candidates',proof['support']['candidate_key']))
            saved=candidate.payload;candidate.payload={**saved,'scope_fingerprint':'changed'};await db.commit()
            with self.assertRaises(ProviderError):await save_automatic_parent(db,'tt2',self.base,proof)
            await db.rollback()
            candidate=await db.scalar(GameSourceCache.query('igdb','game_title_candidates',proof['support']['candidate_key']))
            candidate.payload=saved
            user=await db.get(User,self.user.id);user.is_admin=True
            await review_parent(db,'tt2',self.base,title='Reviewed edition',kind='edition',source_url='https://example.test/edition',reviewer_id=user.id)
            await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):await save_automatic_parent(db,'tt2',self.base,proof)
            await db.rollback()
            from core.game_mapping_lifecycle import invalidate
            await invalidate(db,['igdb:20']);await reject_automatic_parents(db,['tt2']);await db.commit()
            record=await db.scalar(snapshot(KIND,'tt2'))
            self.assertTrue(record.coverage['reviewed']);self.assertEqual(record.payload['title'],'Reviewed edition')

    async def test_changed_candidate_scope_invalidates_automatic_parent_but_identical_scope_does_not(self):
        from core.game_candidate_observations import invalidate_observed
        from core.game_credit_parents import person_bindings
        from core.game_source_cache import GameSourceCache
        from core.game_title_candidates import observations
        result,_=await self.automatic_edition();key=result['evidence']['support']['candidate_key']
        async with self.Session() as db:
            row=await db.scalar(GameSourceCache.query('igdb','game_title_candidates',key));data=dict(row.payload)
            await invalidate_observed(db,key,data);await db.commit()
            self.assertTrue((await db.execute(select(person_bindings(['tt2'])))).all())
            await invalidate_observed(db,'different-query',data);await db.commit()
            self.assertTrue((await db.execute(select(person_bindings(['tt2'])))).all())
            data={**data,'scope_fingerprint':'new','rows':[dict(r) for r in data['rows']]}
            data['rows'][0]['version_parent']=99;data['scope_observations']=observations(data['rows'])
            await invalidate_observed(db,'different-query',data);await db.commit()
            self.assertFalse((await db.execute(select(person_bindings(['tt2'])))).all())

    async def test_source_generation_changes_invalidate_automatic_parent_participation(self):
        from core import game_mapping_index as index
        from core.game_credit_parents import person_bindings,KIND
        await self.automatic_edition()
        generation=await index.begin(self.Session,'imdb')
        await index.write(self.Session,generation,[{'id':'tt2','title':'Corrected source title','year':2024,
            'data':{'id':'tt2','title':'Corrected source title','year':2024,'aliases':[]}}])
        await index.publish(self.Session,generation,{'complete':True,'rows':1})
        async with self.Session() as db:
            self.assertFalse((await db.execute(select(person_bindings(['tt2'])))).all())
            record=await db.scalar(snapshot(KIND,'tt2'));self.assertEqual(record.coverage['mapping_state'],'stale')

    async def test_changed_parent_root_cannot_be_saved_or_used_for_new_participation(self):
        from core.game_credit_parents import save_automatic_parent,person_bindings
        from core import catalogue
        result,_=await self.automatic_edition()
        async with self.Session() as db:
            parent=await db.get(CatalogueEntity,self.base);parent.attributes={**parent.attributes,'igdb_id':99};await db.commit()
            self.assertFalse((await db.execute(select(person_bindings(['tt2'])))).all())
            with self.assertRaises(catalogue.IdentityConflict):await save_automatic_parent(db,'tt2',self.base,result['evidence'])
            await db.rollback()

    async def test_confirmed_conflict_retires_automatic_participation(self):
        from core.game_credit_parents import reject_automatic_parents,KIND,person_bindings
        await self.automatic_edition()
        async with self.Session() as db:
            await reject_automatic_parents(db,['tt2']);await db.commit()
            self.assertFalse((await db.execute(select(person_bindings(['tt2'])))).all())
            record=await db.scalar(snapshot(KIND,'tt2'));self.assertEqual(record.coverage['mapping_state'],'quarantined')
            role=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.scope=='person',CatalogueCredit.contributor_id==self.person,
                CatalogueCredit.attributes['imdb_title_id'].astext=='tt2'))
            self.assertFalse(role.attributes['source_active'])

    async def test_reviewed_update_uses_parent_without_overwriting_primary_identity(self):
        from core.game_credit_parents import review_parent
        from core.game_contributor_works import works_page
        from core.game_imdb_person import queue_projection
        async with self.Session() as db:
            user=await db.get(__import__('models',fromlist=['User']).User,self.user.id)
            user.is_admin=True
            await review_parent(db,'tt2',self.base,title='Main game: Update',kind='update',
                source_url='https://example.test/official-update',reviewer_id=user.id)
            await queue_projection(db,'nm1');await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            state=await view(db,'nm1')
            self.assertEqual(state['mapping']['unmapped_games'],1)  # tt3 remains unknown.
            identity=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.entity_id==self.base))
            self.assertEqual(identity.external_id,'tt1')
            # tt1 already credits this actor: one main-game row, no duplicate update tag.
            result=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(len(result['works']),1)
            self.assertFalse(result['works'][0].get('credit_sources'))
            from sqlalchemy import delete
            await db.execute(delete(CatalogueCredit).where(CatalogueCredit.contributor_id==self.person,
                CatalogueCredit.attributes['imdb_title_id'].astext=='tt1'))
            only_update=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(only_update['works'][0]['credit_sources'],[{'kind':'update','title':'Main game: Update'}])
            self.assertEqual(only_update['works'][0]['href'],f'/games/{self.base}')
            from core.game_imdb_review import review_titles
            review=await review_titles(db,'nm1',scope='all')
            self.assertEqual(next(r for r in review['results'] if r['imdb_id']=='tt2')['status'],'bound')

    async def test_reviewed_edition_uses_parent_and_preserves_the_variant_evidence(self):
        from core.game_credit_parents import review_parent,KIND
        from core.game_contributor_works import works_page
        from core.game_imdb_person import queue_projection
        from models import User
        async with self.Session() as db:
            user=await db.get(User,self.user.id);user.is_admin=True
            await review_parent(db,'tt2',self.base,title='Main game: Complete Edition',kind='edition',
                source_url='https://www.igdb.com/games/complete-edition',reviewer_id=user.id,
                evidence={'method':'unique_title_year_parent','child':{'id':100},'parents':[{'id':10}]})
            await queue_projection(db,'nm1');await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            from sqlalchemy import delete
            await db.execute(delete(CatalogueCredit).where(CatalogueCredit.contributor_id==self.person,
                CatalogueCredit.attributes['imdb_title_id'].astext=='tt1'))
            result=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(result['works'][0]['credit_sources'],[{'kind':'edition','title':'Main game: Complete Edition'}])
            self.assertEqual(result['works'][0]['id'],self.base)
            record=await db.scalar(snapshot(KIND,'tt2'))
            self.assertEqual(record.payload['evidence']['child']['id'],100)
            primary=await db.scalar(select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(primary,'tt1')

    async def test_title_fallback_publishes_person_work_and_character_without_opening_the_game(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from core.game_imdb_person import JOB as PERSON_JOB
        from test_game_imdb_discovery_source import raw
        from core.game_source_cache import GameSourceCache
        rows=[credit(),credit('tt2')]
        rows[1]['title'].update(titleText={'text':'Spacelords'},releaseYear={'year':2017})
        rows[1]['characters']=[{'name':'Explorer'}]
        self.connection.credits.return_value=page(rows,2)
        async with self.Session() as db:
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'))
            job.next_attempt_at=now();job.expires_at=None;job.payload={};await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(side_effect=[
            [raw(20,name='Spacelords',first_release_date=1506038400)],[]]),
            api=SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={'results':{'bindings':[]}}))))
        service=GamePersonDiscovery(self.Session,source=GameIMDbDiscoverySource(self.Session,sources=sources,
            wiki=SimpleNamespace(entities=AsyncMock())))
        self.assertEqual(await service.run('nm1'),'changed')
        self.assertEqual(await service.run('nm1'),'ready')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2'))
            self.assertIsNotNone(binding,await db.scalar(snapshot(MAP,'tt2',provider='wikidata').with_only_columns(MetadataSnapshot.payload)))
            self.assertEqual(binding.source,'matched')
            role=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==binding.entity_id,
                CatalogueCredit.scope=='person',CatalogueCredit.attributes['imdb_person_id'].astext=='nm1'))
            self.assertEqual(role.character_label,'Explorer')
            entry=await db.scalar(select(GameEntry))
            self.assertEqual((entry.work_id,entry.notes,entry.total_seconds),(self.base,'Pending private state',3600))

    async def test_annual_sports_title_creates_verified_participation_without_manual_binding(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from core.game_imdb_person import JOB as PERSON_JOB
        from core.game_source_cache import GameSourceCache
        from test_game_title_match import game
        row=credit('tt2');row['title'].update(titleText={'text':'Annual Basketball'},releaseYear={'year':2001})
        self.connection.credits.return_value=page([row],1)
        async with self.Session() as db:
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'));job.next_attempt_at=now();job.expires_at=None;job.payload={};await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        older=game(30,'Annual Basketball',year=2003,genres=[{'name':'Sport'}])
        annual=game(20,'Annual Basketball 2001',year=2001,genres=[{'name':'Sport'}])
        sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(side_effect=[[older],[older],[annual],[annual]]),
            api=SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={'results':{'bindings':[]}}))))
        service=GamePersonDiscovery(self.Session,source=GameIMDbDiscoverySource(self.Session,sources=sources))
        self.assertEqual(await service.run('nm1'),'changed')
        self.assertEqual(await service.run('nm1'),'ready')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            identity=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2'))
            self.assertEqual(identity.source,'matched');self.assertEqual(identity.evidence['igdb_id'],'20')
            self.assertEqual(identity.evidence['matcher_version'],8)
            self.assertTrue(next(r for r in identity.evidence['matching']['candidates'] if r['id']==20)['annual_title_scope'])
            credit_id=await db.scalar(select(CatalogueCredit.id).where(CatalogueCredit.work_id==identity.entity_id,
                CatalogueCredit.contributor_id==self.person,CatalogueCredit.attributes['source_active'].astext=='true'))
            self.assertIsNotNone(credit_id)
            entry=await db.scalar(select(GameEntry))
            self.assertEqual((entry.work_id,entry.notes,entry.total_seconds),(self.base,'Pending private state',3600))
        self.assertEqual(sources.igdb.await_count,4)
        self.assertEqual(sources.api.http.request.await_count,1)

    async def test_retryable_gap_publishes_ready_matches_and_retains_queued_continuation(self):
        self.source.page.return_value={'outcomes':[outcome(),{'id':'tt3','status':'unresolved','reason':'timeout','retry_after':60}],'stale':False}
        self.assertEqual(await self.service.run('nm1'),'partial')
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2'))
            self.assertIsNotNone(binding)
            job=await db.scalar(snapshot(JOB,'nm1'))
            self.assertFalse(job.coverage['complete']);self.assertIsNotNone(job.next_attempt_at)
            self.assertIsNone(job.lease_token);self.assertEqual(job.payload['page'],0)
            gap=await db.scalar(snapshot(MAP,'tt3',provider='wikidata'))
            self.assertLess((gap.expires_at-now()).total_seconds(),61)

    async def test_decorated_child_title_preserves_participation_and_parent(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from core.game_imdb_person import JOB as PERSON_JOB
        from core.game_source_cache import GameSourceCache
        rows=[credit(),credit('tt2')]
        rows[1]['title'].update(titleText={'text':'Small Town: Alice & Bob in The Long Dark Night'},releaseYear={'year':2017})
        self.connection.credits.return_value=page(rows,2)
        async with self.Session() as db:
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'));job.next_attempt_at=now();job.expires_at=None;job.payload={};await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        child={'id':30,'name':'Small Town: The Long Dark Night','game_type':{'id':1},'first_release_date':1506038400,
            'parent_game':{'id':100,'name':'Small Town','game_type':{'id':0}}}
        sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(side_effect=[[],[],[child],[child],
            [{'id':100,'name':'Small Town','game_type':{'id':0}}]]),
            api=SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={'results':{'bindings':[]}}))))
        service=GamePersonDiscovery(self.Session,source=GameIMDbDiscoverySource(self.Session,sources=sources))
        self.assertEqual(await service.run('nm1'),'changed');self.assertEqual(await service.run('nm1'),'ready')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            result=await db.scalar(snapshot(MAP,'tt2',provider='wikidata'))
            self.assertEqual((result.payload['status'],result.payload['igdb_id']),('verified',30))
            self.assertEqual(result.payload['evidence']['candidates'][0]['parent_title_scope']['parent'],100)
            self.assertIsNotNone(await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2')))
            state=await view(db,'nm1');self.assertEqual(state['mapping']['unmapped_games'],0)
            entry=await db.scalar(select(GameEntry));self.assertEqual((entry.notes,entry.total_seconds),('Pending private state',3600))

    async def test_confirmed_episode_is_automatic_and_projects_one_parent_card(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from core.game_imdb_person import JOB as PERSON_JOB
        from core.game_source_cache import GameSourceCache
        from core.game_contributor_works import works_page
        rows=[credit('tt2',role='archive_sound')]
        rows[0]['title'].update(titleText={'text':'Small Town: The Stories - Episode III'},releaseYear={'year':2017})
        self.connection.credits.return_value=page(rows,1)
        async with self.Session() as db:
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'));job.next_attempt_at=now();job.expires_at=None;job.payload={};await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        parent={'id':100,'name':'Small Town: The Stories','game_type':{'id':0}}
        child={'id':30,'name':'Small Town: Episode III','game_type':{'id':6},'first_release_date':1506038400,'parent_game':parent}
        sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(side_effect=[[],[],[child],[],[parent]]),
            api=SimpleNamespace(http=SimpleNamespace(request=AsyncMock(return_value={'results':{'bindings':[]}}))))
        service=GamePersonDiscovery(self.Session,source=GameIMDbDiscoverySource(self.Session,sources=sources))
        self.assertEqual(await service.run('nm1'),'changed')
        status=await service.run('nm1')
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'))
            self.assertEqual(status,'ready',job.error_code)
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            mapped=await db.scalar(snapshot(MAP,'tt2',provider='wikidata'))
            self.assertEqual((mapped.payload['status'],mapped.payload['igdb_id']),('verified',30))
            result=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            from schemas_contributors import ContributorPage
            response=ContributorPage.model_validate({**result,'kind':'actor','key':'test','name':'Actor'})
            self.assertEqual(response.works[0].credit_sources[0].kind,'episode')
            self.assertEqual(len(result['works']),1)
            card=result['works'][0]
            self.assertEqual(card['title'],parent['name'])
            self.assertEqual(card['credit_sources'],[{'kind':'episode','title':child['name']}])
            self.assertEqual(card['href'],f'/games/{card["id"]}')
            self.assertNotEqual(card['id'],mapped.entity_id)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)),1)
            db.add(CatalogueCredit(work_id=card['id'],contributor_id=self.person,provider='imdb',
                source_key='main-game-participation',scope='person',role='actor',attributes={'source_active':True}))
            await db.flush()
            main=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(len(main['works']),1)
            self.assertFalse(main['works'][0].get('credit_sources'))

    async def test_episode_migration_preserves_edges_and_refuses_lossy_downgrade(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import text
        from migrations.versions import mt073_game_episode_credits as migration
        def verify(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()
                conn.execute(text("INSERT INTO catalogue_relationships(source_id,target_id,relation,provider,attributes) VALUES (:source,:target,'episode','igdb','{}')"),
                    {'source':self.pack,'target':self.base})
                with self.assertRaisesRegex(RuntimeError,'existing data was preserved'):
                    migration.downgrade()
                self.assertEqual(conn.scalar(text("SELECT count(*) FROM catalogue_relationships WHERE relation='episode'")),1)
                conn.execute(text("DELETE FROM catalogue_relationships WHERE relation='episode'"))
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(verify)

    async def test_fresh_actor_and_crew_work_is_added_to_the_existing_catalogue_without_private_tracking(self):
        # Completing the initial source fetch already schedules discovery;
        # neither a second page visit nor a game-details visit is required.
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'));self.assertIsNotNone(job.next_attempt_at)
            poll=await view(db,'nm1',include_profile=False);self.assertTrue(poll['pending'])
        state=await self.admit();self.assertTrue(state['pending'])
        self.assertEqual(await self.service.run('nm1'),'ready')
        self.source.page.assert_awaited_once_with(['tt2','tt3'])
        # Reuse the complete saved IMDb connection, including its original fetch time.
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        self.connection.credits.assert_awaited_once()
        async with self.Session() as db:
            state=await view(db,'nm1')
            self.assertEqual((state['mapping']['source_games'],state['mapping']['mapped_games'],state['mapping']['unmapped_games']),(3,3,0))
            rows=list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.scope=='person',
                CatalogueCredit.attributes['source_active'].astext=='true')))
            self.assertEqual({row.role for row in rows},{'actor','director'});self.assertEqual(len(rows),3)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)),1)
            entry=await db.scalar(select(GameEntry));self.assertEqual((entry.total_seconds,entry.notes),(3600,'Pending private state'))
            self.assertEqual((await db.get(CatalogueEntity,self.person)).description,'Keep biography')
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueIdentity).where(CatalogueIdentity.namespace=='igdb.game')),3)

    async def test_failures_retry_without_certifying_an_empty_portfolio_or_touching_saved_source(self):
        await self.admit();self.source.page.side_effect=ProviderError('rate_limited',60)
        self.assertEqual(await self.service.run('nm1'),'retry')
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'));self.assertEqual(job.error_code,'rate_limited')
            self.assertGreater(job.next_attempt_at,now());self.assertEqual(job.payload['processed'],0)
            self.assertTrue((await db.scalar(snapshot(COMPONENT,'nm1'))).coverage['source_complete'])
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==MAP)),0)
        self.assertEqual(await self.service.run('nm1'),'cached');self.source.page.assert_awaited_once()

    async def test_complete_typed_dlc_exclusions_are_not_unresolved_or_displayed_as_games(self):
        self.source.page.return_value={'outcomes':[{'id':'tt2','status':'excluded','reason':'dlc','igdb_id':20},
            {'id':'tt3','status':'excluded','reason':'pack','igdb_id':30}],'stale':False}
        await self.admit();self.assertEqual(await self.service.run('nm1'),'ready')
        await self.person_service.run('nm1')
        async with self.Session() as db:
            data=await view(db,'nm1');coverage=data['mapping']
            self.assertEqual((coverage['mapped_games'],coverage['excluded_games'],coverage['unmapped_games']),(1,2,0))
            self.assertEqual(coverage['role_coverage']['staff']['source'],0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),self.original_games)

    async def test_repeated_discovery_and_concurrent_admission_are_idempotent(self):
        await asyncio.gather(self.admit(),self.admit())
        self.assertEqual(await self.service.run('nm1'),'ready');await self.person_service.run('nm1')
        before=self.source.page.await_count
        await self.admit();self.assertEqual(await self.service.run('nm1'),'cached')
        self.assertEqual(self.source.page.await_count,before)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title')),3)

    async def test_reviewed_mapping_is_reused_and_never_remapped_by_discovery(self):
        async with self.Session() as db:
            existing=CatalogueEntity(kind='game',name='Reviewed work',attributes={'game_class':'game','igdb_id':40});db.add(existing);await db.flush()
            db.add_all([CatalogueIdentity(entity_id=existing.id,namespace='igdb.game',external_id='40',source='igdb'),
                CatalogueIdentity(entity_id=existing.id,namespace='imdb.title',external_id='tt2',source='reviewed',
                    evidence={'title_type':'videoGame','igdb_id':'40'})]);await db.commit();reviewed=existing.id
        self.source.page.return_value={'outcomes':[outcome('tt3',30)],'stale':False}
        await self.admit();await self.service.run('nm1')
        self.source.page.assert_awaited_once_with(['tt3'])
        async with self.Session() as db:
            mapping=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt2'))
            self.assertEqual((mapping.entity_id,mapping.source),(reviewed,'reviewed'))

    async def test_new_source_generation_restarts_discovery_without_publishing_stale_participation(self):
        await self.admit()
        async with self.Session() as db:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.kind==COMPONENT).values(
                payload=MetadataSnapshot.payload.op('||')({'generation':'new'})))
            await db.commit()
        self.assertEqual(await self.service.run('nm1'),'changed');self.source.page.assert_not_awaited()
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'));self.assertEqual(job.payload['generation'],'new')

    async def test_unresolved_identities_remain_partial_after_a_complete_discovery_attempt(self):
        self.source.page.return_value={'outcomes':[{'id':'tt2','status':'conflict'},{'id':'tt3','status':'unmapped'}],'stale':False}
        await self.admit();await self.service.run('nm1');await self.person_service.run('nm1')
        async with self.Session() as db:
            state=await view(db,'nm1',check_mapping=True);await db.commit()
            self.assertFalse(state['pending']);self.assertEqual(state['mapping']['unmapped_games'],2)
            self.assertEqual(state['mapping']['state'],'partial')

    async def test_cancelled_provider_work_releases_lease_and_preserves_cursor(self):
        entered=asyncio.Event()
        async def load(ids):entered.set();await asyncio.Event().wait()
        self.source.page.side_effect=load
        await self.admit();task=asyncio.create_task(self.service.run('nm1'));await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'))
            self.assertIsNone(job.lease_token);self.assertIsNone(job.lease_until)
            self.assertEqual(job.payload['processed'],0);self.assertIsNone(job.error_code)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==MAP)),0)

    async def test_incomplete_or_malformed_frames_and_stale_evidence_never_complete_a_job(self):
        await self.admit()
        for result in ({'outcomes':[outcome()],'stale':False},
            {'outcomes':[{'id':[],'status':'unmapped'},{'id':'tt3','status':'unmapped'}],'stale':False},
            {'outcomes':[outcome(),outcome('tt3',30)],'stale':True}):
            with self.subTest(result=result):
                self.source.page.return_value=result
                self.assertEqual(await self.service.run('nm1'),'retry')
                async with self.Session() as db:
                    job=await db.scalar(snapshot(JOB,'nm1'));self.assertFalse(job.coverage.get('complete'))
                    self.assertEqual(job.payload['processed'],0)
                    self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==MAP)),0)
                    job.next_attempt_at=now()-timedelta(seconds=1);await db.commit()

    async def test_large_portfolio_progresses_in_bounded_batches_and_reuses_prior_outcomes(self):
        # Full source traversal survives the smaller fresh-acquisition budget.
        self.connection.credits.return_value=page([credit(f'tt{i}') for i in range(1,54)],53)
        async with self.Session() as db:
            from core.game_imdb_person import JOB as PERSON_JOB
            job=await db.scalar(snapshot(PERSON_JOB,'nm1'));job.next_attempt_at=now()-timedelta(seconds=1)
            job.expires_at=now()-timedelta(seconds=1);await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async def load(ids):return {'outcomes':[{'id':value,'status':'unmapped'} for value in ids],'stale':False}
        self.source.page.side_effect=load
        await self.admit()
        self.assertEqual(await self.service.run('nm1'),'changed')
        for expected in (*range(5,50,4),50,53):
            result=await self.service.run('nm1')
            self.assertEqual(result,'ready' if expected==53 else 'partial')
            async with self.Session() as db:
                job=await db.scalar(snapshot(JOB,'nm1'));self.assertEqual(job.payload['processed'],expected)
                if expected<53:job.next_attempt_at=now()-timedelta(seconds=1)
                await db.commit()
        calls=[call.args[0] for call in self.source.page.await_args_list]
        self.assertEqual(list(map(len,calls)),[4]*12+[1,3])
        self.assertEqual(len({value for ids in calls for value in ids}),52)
