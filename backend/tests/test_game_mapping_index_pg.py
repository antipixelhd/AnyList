"""Real generation, dependency, reviewed-binding and matching boundaries."""
import asyncio
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from sqlalchemy import select, func

import test_games_pg as fixture
from core import game_mapping_index as index
from core.game_imdb_bindings import GameIMDbBindings, verified_bindings
from core.game_mapping_import import import_jsonl
from core.game_imdb_enrichment import now
from models.catalogue import CatalogueIdentity, CatalogueEntity, CatalogueCredit, MetadataSnapshot
from models.games import GameEntry
from models.game_mapping import GameMappingGeneration
from core.game_title_match import VERSION as MATCHER_VERSION


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable Games PostgreSQL')
class MappingIndexTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)

    def record(self, title='Example'):
        return {'id': 'tt1', 'title': title, 'year': 2020, 'aliases': ['Alternative'], 'imdb_ids': ['tt1'],
                'data': {'id': 'tt1', 'title': title, 'year': 2020, 'aliases': ['Alternative']}}

    async def generation(self, title='Example', publish=True):
        generation = await index.begin(self.Session, 'imdb')
        await index.write(self.Session, generation, [self.record(title)])
        if publish:
            await index.publish(self.Session, generation, {'complete': True, 'rows': 1})
        return generation

    async def test_staging_is_invisible_and_atomic_publication_retains_old_generation_until_complete(self):
        first = await self.generation()
        staged = await self.generation('New title', publish=False)
        self.assertEqual((await index.lookup(self.Session, 'imdb', native='tt1'))[0]['generation'], first)
        with self.assertRaises(ValueError):
            await index.publish(self.Session, staged, {'complete': False, 'rows': 1})
        self.assertEqual((await index.lookup(self.Session, 'imdb', native='tt1'))[0]['generation'], first)
        await index.publish(self.Session, staged, {'complete': True, 'rows': 1})
        self.assertEqual((await index.lookup(self.Session, 'imdb', names=['New title']))[0]['generation'], staged)
        self.assertEqual(await index.lookup(self.Session, 'imdb', names=['Example']), [])

    async def test_changed_dependencies_mark_only_automatic_bindings_stale(self):
        await self.generation()
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'))
            db.add(CatalogueIdentity(entity_id=self.base, namespace='imdb.title', external_id='tt1', source='matched',
                evidence={'igdb_id': '10', 'title_type': 'videoGame', 'matcher_version':MATCHER_VERSION,'dependencies': ['imdb:tt1'], 'mapping_state': 'active'}))
            db.add(CatalogueIdentity(entity_id=self.pack, namespace='imdb.title', external_id='tt2', source='reviewed',
                evidence={'igdb_id': '20', 'title_type': 'videoGame', 'dependencies': ['imdb:tt1'], 'mapping_state': 'active'}))
            await db.commit()
        async with self.Session() as db:
            self.assertEqual(len(list(await db.scalars(verified_bindings(['tt1'])))),1)
        await self.generation('Corrected title')
        async with self.Session() as db:
            values = {row.external_id: row for row in await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.namespace == 'imdb.title'))}
            self.assertEqual(values['tt1'].evidence['mapping_state'], 'stale')
            self.assertEqual(values['tt2'].evidence['mapping_state'], 'active')
            self.assertEqual(list(await db.scalars(verified_bindings(['tt1']))), [])

    async def test_cached_wikidata_projection_omits_credit_payload_and_reuses_unchanged_generation(self):
        from core.game_mapping_import import refresh_cached_wikidata
        from test_game_credit_sources import item,claim
        raw=item('Q1','Example',{'P345':[claim('tt1')],'P9043':[claim('10')],
            'P725':[claim('Q2') for _ in range(1000)]})
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='wikidata',kind='game_credit_items',external_id='v1:test',
                payload={'entities':{'Q1':raw}},fetched_at=now(),expires_at=now()+timedelta(days=1)))
            await db.commit()
        first=await refresh_cached_wikidata(self.Session)
        self.assertEqual(first['rows'],1)
        row=(await index.lookup(self.Session,'wikidata',igdb_id=10))[0]
        self.assertNotIn('P725',row['value']['claims'])
        self.assertEqual(row['value']['claims']['P345'][0]['mainsnak']['datavalue']['value'],'tt1')
        self.assertTrue((await refresh_cached_wikidata(self.Session))['skipped'])

    async def test_confirmed_quarantine_retires_only_public_evidence_and_preserves_tracking(self):
        from core.game_mapping_lifecycle import quarantine
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Exact source actor');db.add(person);await db.flush()
            binding=CatalogueIdentity(entity_id=self.base,namespace='imdb.title',external_id='tt1',source='matched',
                evidence={'mapping_state':'active','igdb_id':'10','title_type':'videoGame'})
            db.add_all([binding,GameEntry(user_id=self.user.id,work_id=self.base,status='playing',total_seconds=3600,notes='Private draft'),
                CatalogueCredit(work_id=self.base,contributor_id=person.id,provider='imdb',source_key='test',role='actor',
                    attributes={'source_active':True})]);await db.flush()
            await quarantine(db,binding,'confirmed_changed_identity');await db.commit()
        async with self.Session() as db:
            self.assertEqual((await db.scalar(select(CatalogueCredit))).attributes['source_active'],False)
            entry=await db.scalar(select(GameEntry));self.assertEqual((entry.total_seconds,entry.notes),(3600,'Private draft'))
            self.assertEqual((await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title'))).evidence['mapping_state'],'quarantined')

    async def test_identical_evidence_does_not_invalidate_a_binding_for_a_new_generation(self):
        await self.generation()
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base, namespace='imdb.title', external_id='tt1', source='matched',
                evidence={'dependencies': ['imdb:tt1'], 'mapping_state': 'active'}))
            await db.commit()
        await self.generation()
        async with self.Session() as db:
            row = await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.external_id == 'tt1'))
            self.assertEqual(row.evidence['mapping_state'], 'active')

    async def test_published_generations_are_immutable_and_late_old_imports_cannot_replace_newer_data(self):
        first=await self.generation()
        with self.assertRaises(ValueError):await index.write(self.Session,first,[self.record('Changed')])
        old=await index.begin(self.Session,'imdb',observed_at=now()-timedelta(days=1))
        await index.write(self.Session,old,[self.record('Old')])
        with self.assertRaisesRegex(ValueError,'newer reference'):await index.publish(self.Session,old,{'complete':True,'rows':1})
        self.assertEqual((await index.lookup(self.Session,'imdb',native='tt1'))[0]['generation'],first)
        await self.generation('Next')
        with self.assertRaises(ValueError):await index.write(self.Session,first,[self.record('Changed inactive')])
        with self.assertRaises(ValueError):await index.publish(self.Session,first,{'complete':True,'rows':1})

    async def test_truncated_export_cannot_replace_a_good_index(self):
        first = await self.generation()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'partial.jsonl'
            path.write_text(json.dumps(self.record()['data']) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'completion marker'):
                await import_jsonl(self.Session, 'imdb', path)
        self.assertEqual((await index.lookup(self.Session, 'imdb', native='tt1'))[0]['generation'], first)
        async with self.Session() as db:
            self.assertEqual(len(list(await db.scalars(select(GameMappingGeneration.id)))), 1)

    async def test_automatic_refresh_cannot_overwrite_reviewed_evidence(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base, namespace='igdb.game', external_id='10', source='igdb'))
            db.add(CatalogueIdentity(entity_id=self.base, namespace='imdb.title', external_id='tt1', source='reviewed',
                evidence={'igdb_id': '10', 'title_type': 'videoGame', 'reviewer_id': 42, 'reason': 'Pinned decision'}))
            await db.commit()
        await GameIMDbBindings(self.Session).save(self.base, 'tt1', 'matched',
            {'igdb_id': '10', 'title_type': 'videoGame'}, expected_igdb='10')
        async with self.Session() as db:
            row = await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace == 'imdb.title'))
            self.assertEqual(row.source, 'reviewed')
            self.assertEqual(row.evidence['reason'], 'Pinned decision')
            self.assertNotIn('matching', row.evidence)

    async def test_reverse_lookup_uses_the_local_imdb_index_and_checks_the_current_title(self):
        await self.generation()
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'))
            await db.commit()
        source=SimpleNamespace(title=AsyncMock(return_value={'id':'tt1','titleType':{'id':'videoGame'},
            'titleText':{'text':'Example'},'releaseYear':{'year':2020}}))
        raw={'id':10,'name':'Example','game_type':{'id':0},'first_release_date':1577836800}
        from core.game_source_cache import GameSourceCache
        wiki=SimpleNamespace(sources=SimpleNamespace(cache=GameSourceCache(self.Session),igdb=AsyncMock(return_value=[raw])))
        service=GameIMDbBindings(self.Session,source=source,wiki=wiki)
        result=await service.reverse_lookup(self.base,'10',{'status':'unmapped'})
        self.assertEqual((result['status'],result['binding']['source']),('bound','matched'))
        source.title.assert_awaited_once_with('tt1')

    async def test_reverse_lookup_rejects_competing_igdb_namesake(self):
        await self.generation()
        from core.game_source_cache import GameSourceCache
        raw={'id':10,'name':'Example','game_type':{'id':0},'first_release_date':1577836800}
        source=SimpleNamespace(title=AsyncMock(return_value={'id':'tt1','titleType':{'id':'videoGame'},
            'titleText':{'text':'Example'},'releaseYear':{'year':2020}}))
        wiki=SimpleNamespace(sources=SimpleNamespace(cache=GameSourceCache(self.Session),
            igdb=AsyncMock(side_effect=[[raw],*[[raw,{**raw,'id':20}]]*4])))
        result=await GameIMDbBindings(self.Session,source=source,wiki=wiki).reverse_lookup(self.base,'10',{'status':'unmapped'})
        self.assertEqual(result['status'],'unresolved')

    async def test_partial_wikidata_index_cannot_hide_an_unindexed_competitor(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        from test_game_credit_sources import item,claim
        from core.game_mapping_import import wiki_record
        generation=await index.begin(self.Session,'wikidata')
        await index.write(self.Session,generation,[wiki_record(item('Q1','Example',{'P345':[claim('tt1')],'P9043':[claim('10')]}))])
        await index.publish(self.Session,generation,{'complete':True,'rows':1,'scope':'retrieved_game_evidence'})
        service=GameIMDbDiscoverySource(self.Session)
        service.sources=SimpleNamespace(cache=service.sources.cache,api=SimpleNamespace(http=SimpleNamespace(
            request=AsyncMock(return_value={'results':{'bindings':[
                {'title':{'value':'tt1'},'item':{'value':'http://www.wikidata.org/entity/'+qid}} for qid in ('Q1','Q2')]}}))))
        result=await service.items(['tt1'])
        self.assertEqual(result['data']['items']['tt1'],['Q1','Q2'])

    async def test_generations_retain_only_active_and_one_previous_publication(self):
        for number in range(4):await self.generation(str(number))
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameMappingGeneration)),2)

    async def test_inflight_publication_cannot_clear_a_newer_mapping_request(self):
        from core.game_imdb_enrichment import GameIMDbEnrichment,JOB,snapshot
        from core.game_mapping_lifecycle import invalidate
        async with self.Session() as db:
            db.add_all([CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=self.base,namespace='imdb.title',external_id='tt1',source='matched',
                    evidence={'igdb_id':'10','mapping_state':'active','matcher_version':MATCHER_VERSION,'dependencies':['imdb:tt1'],'evidence_revision':'old'}),
                MetadataSnapshot(provider='imdb',kind=JOB,external_id=str(self.base),entity_id=self.base,next_attempt_at=now())])
            await db.commit()
        service=GameIMDbEnrichment(self.Session)
        token,state=await service.claim(self.base)
        state.update(title_id='tt1',binding_revision='old')
        async with self.Session() as db:
            await invalidate(db,['imdb:tt1']);await db.commit()
        self.assertFalse(await service.publish(self.base,token,state,{}))
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base))
            self.assertTrue(job.coverage['mapping_revalidation']);self.assertIsNotNone(job.next_attempt_at)
            self.assertIsNone(job.lease_token)

    async def test_changed_candidate_scope_invalidates_automatic_evidence_and_old_policy_is_not_verified(self):
        from core.game_source_cache import GameSourceCache
        cache=GameSourceCache(self.Session)
        initial={'rows':[],'scope_names':['example'],'scope_fingerprint':'first'}
        await cache.get('igdb','game_title_candidates','scope',AsyncMock(return_value=initial))
        async with self.Session() as db:
            db.add_all([CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=self.base,namespace='imdb.title',external_id='tt1',source='matched',
                    evidence={'igdb_id':'10','title_type':'videoGame','matcher_version':0,'mapping_state':'active',
                        'dependencies':['igdb-query:scope']})])
            row=await db.scalar(cache.query('igdb','game_title_candidates','scope'));row.expires_at=now()-timedelta(seconds=1)
            await db.commit()
            self.assertEqual(list(await db.scalars(verified_bindings(['tt1']))),[])
        await cache.refresh('igdb','game_title_candidates','scope',AsyncMock(return_value={**initial,'scope_fingerprint':'new'}),
            timedelta(days=1),timedelta(days=7))
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(binding.evidence['mapping_state'],'stale')

    async def test_unrelated_third_wikidata_target_is_not_overridden_by_a_proven_rename_pair(self):
        from core.game_source_cache import GameSourceCache
        from test_game_credit_sources import claim
        await self.generation()
        raw={'id':10,'name':'Example','slug':'example','game_type':{'id':0},'first_release_date':1577836800,
            'alternative_names':[{'name':'Older Example'}],'websites':[{'url':'https://store.steampowered.com/app/42/'}]}
        other={**raw,'id':20,'name':'Older Example','slug':'older-example','alternative_names':[{'name':'Example'}]}
        source=SimpleNamespace(title=AsyncMock(return_value={'id':'tt1','titleType':{'id':'videoGame'},
            'titleText':{'text':'Example'},'releaseYear':{'year':2020}}))
        wiki=SimpleNamespace(sources=SimpleNamespace(cache=GameSourceCache(self.Session),
            igdb=AsyncMock(side_effect=[[raw],*[[raw,other]]*4])))
        previous={'status':'conflict','mappings':[{'evidence':{'P345':[claim('tt1')],'P9043':[claim('21')]}}]}
        result=await GameIMDbBindings(self.Session,source=source,wiki=wiki).reverse_lookup(self.base,'10',previous)
        self.assertEqual(result['status'],'conflict')

    async def test_wikidata_publication_between_retrieval_and_save_rejects_obsolete_match(self):
        from core.game_title_match import decide
        from core.catalogue_providers import ProviderError
        from core.game_mapping_import import wiki_record
        from test_game_credit_sources import item,claim
        raw={'id':10,'name':'Example','game_type':{'id':0},'first_release_date':1577836800}
        proof=decide(self.record()['data'],[raw],complete=True,branches=[])['evidence']
        proof['support']={'check_wikidata_generation':True,'wikidata_generation':None}
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'));await db.commit()
        generation=await index.begin(self.Session,'wikidata')
        await index.write(self.Session,generation,[wiki_record(item('Q1','Other',{'P345':[claim('tt1')],'P9043':[claim('21')]}))])
        await index.publish(self.Session,generation,{'complete':True,'rows':1})
        with self.assertRaisesRegex(ProviderError,'stale_mapping'):
            await GameIMDbBindings(self.Session).save(self.base,'tt1','matched',
                {'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10')
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title')))

    async def test_consulted_wikidata_snapshot_remains_locked_until_binding_commit(self):
        from core import catalogue
        from core.game_source_cache import GameSourceCache
        from core.game_title_match import decide
        from core.game_source_cache import utcnow
        stamp=utcnow()
        raw={'id':10,'name':'Example','game_type':{'id':0},'first_release_date':1577836800}
        proof=decide(self.record()['data'],[raw],complete=True,branches=[])['evidence']
        proof['support']={'wiki_snapshots':[{'kind':'game_credit_items','key':'support','fetched_at':stamp.isoformat()}]}
        async with self.Session() as db:
            db.add_all([CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'),
                MetadataSnapshot(provider='wikidata',kind='game_credit_items',external_id='v1:support',payload={},fetched_at=stamp)])
            await db.commit()
            stored=await db.scalar(GameSourceCache.query('wikidata','game_credit_items','support').with_only_columns(MetadataSnapshot.fetched_at))
            self.assertEqual(stored,stamp)
        checked,release=asyncio.Event(),asyncio.Event()
        original=catalogue.bind_identity
        async def paused(*args,**kwargs):
            checked.set();await release.wait()
            return await original(*args,**kwargs)
        async def refresh():
            async with self.Session() as db:
                row=await db.scalar(GameSourceCache.query('wikidata','game_credit_items','support').with_for_update())
                row.fetched_at=now()+timedelta(seconds=1);await db.commit()
        with patch.object(catalogue,'bind_identity',side_effect=paused):
            saving=asyncio.create_task(GameIMDbBindings(self.Session).save(self.base,'tt1','matched',
                {'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10'))
            await asyncio.wait_for(checked.wait(),5)
            refreshing=asyncio.create_task(refresh())
            completed,_=await asyncio.wait([refreshing],timeout=.05)
            release.set();await saving;await refreshing
        self.assertFalse(completed,'A source refresh must wait for binding acceptance to commit')

    async def test_independent_support_is_validated_and_changed_support_cannot_reactivate_old_proof(self):
        from core.game_match_support import cached
        from core.game_source_cache import GameSourceCache
        from core.game_title_match import decide
        from core.catalogue_providers import ProviderError
        sources=SimpleNamespace(cache=GameSourceCache(self.Session))
        data,support=await cached(sources,'steam','game_match_store','42',AsyncMock(return_value={'name':'Example'}))
        title={'id':'tt1','title':'Example','year':None}
        raw={'id':10,'name':'Example','game_type':{'id':0}}
        proof=decide(title,[raw],complete=True,branches=[],corroboration={10:{'app':'42',
            'store_title':'Example','developer_agreement':True,'characters':['Chelsea Williams']}})['evidence']
        dependency='support:steam:game_match_store:42'
        proof.update(support={'snapshots':[support]},dependencies=[dependency])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'));await db.commit()
        service=GameIMDbBindings(self.Session)
        self.assertEqual((await service.save(self.base,'tt1','matched',
            {'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10'))['status'],'bound')
        async with self.Session() as db:
            row=await db.scalar(sources.cache.query('steam','game_match_store','42'));row.expires_at=now()-timedelta(seconds=1);await db.commit()
        await sources.cache.refresh('steam','game_match_store','42',AsyncMock(return_value={**data,'fingerprint':'changed'}),
            timedelta(days=7),timedelta(days=30))
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(binding.evidence['mapping_state'],'stale')
        with self.assertRaisesRegex(ProviderError,'stale_mapping'):
            await service.save(self.base,'tt1','matched',{'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10')

    async def test_wikidata_alias_support_is_invalidated_when_the_evidence_changes(self):
        from core.game_match_support import cached
        from core.game_source_cache import GameSourceCache
        from core.game_title_match import decide
        from core.catalogue_providers import ProviderError
        sources=SimpleNamespace(cache=GameSourceCache(self.Session))
        support_value={'wikidata_title':{'imdb_id':'tt1','igdb_id':10,'item':'Q1','revision':1,
            'names':['Example'],'years':[2020]}}
        data,support=await cached(sources,'wikidata','game_match_wiki','alias',AsyncMock(return_value={'proof':support_value}))
        title={'id':'tt1','title':'Example','year':2020}
        raw={'id':10,'name':'Localized Example','game_type':{'id':0},'first_release_date':1546300800}
        decision=decide(title,[raw],complete=True,branches=[],corroboration={10:support_value})
        self.assertEqual(decision['status'],'verified')
        proof=decision['evidence'];proof.update(support={'snapshots':[support]},dependencies=['support:wikidata:game_match_wiki:alias'])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'));await db.commit()
        service=GameIMDbBindings(self.Session)
        await service.save(self.base,'tt1','matched',{'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10')
        async with self.Session() as db:
            row=await db.scalar(sources.cache.query('wikidata','game_match_wiki','alias'));row.expires_at=now()-timedelta(seconds=1);await db.commit()
        await sources.cache.refresh('wikidata','game_match_wiki','alias',AsyncMock(return_value={**data,'fingerprint':'changed'}),timedelta(days=7),timedelta(days=30))
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(binding.evidence['mapping_state'],'stale')
        with self.assertRaisesRegex(ProviderError,'stale_mapping'):
            await service.save(self.base,'tt1','matched',{'igdb_id':'10','title_type':'videoGame','matching':proof},expected_igdb='10')

    async def test_reverse_lookup_does_not_accept_one_of_two_identical_imdb_records(self):
        generation=await index.begin(self.Session,'imdb')
        second=self.record();second.update(id='tt2',imdb_ids=['tt2']);second['data']={**second['data'],'id':'tt2'}
        await index.write(self.Session,generation,[self.record(),second]);await index.publish(self.Session,generation,{'complete':True,'rows':2})
        source=SimpleNamespace(title=AsyncMock())
        wiki=SimpleNamespace(sources=SimpleNamespace(cache=None,igdb=AsyncMock(return_value=[{
            'id':10,'name':'Example','game_type':{'id':0},'first_release_date':1577836800}])))
        result=await GameIMDbBindings(self.Session,source=source,wiki=wiki).reverse_lookup(self.base,'10',{'status':'unmapped'})
        self.assertEqual(result['status'],'unresolved');source.title.assert_not_awaited()
