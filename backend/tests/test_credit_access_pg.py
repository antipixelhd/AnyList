"""Regression checks for credit projection consistency and bounded access."""
import importlib
import asyncio
import unittest
from copy import deepcopy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, event, func, select, text

import test_statistics_pg as fixture
from core import catalogue
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.contributor_search import local_matches, known_principal_status
from core.screen_characters import character_images
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, ContributorSearch, ScreenCharacter


@unittest.skipUnless(fixture.URL, 'Requires disposable PostgreSQL')
class CreditAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    def document(self):
        return normalize_tmdb({'id':923, 'title':'Projection fixture', 'credits':{'cast':[
            {'id':991,'name':'Projection Actor','character':'Named Hero','credit_id':'first'},
            {'id':991,'name':'Projection Actor','character':'Other Hero','credit_id':'second'}],
            'crew':[{'id':992,'name':'Projection Director','job':'Director','credit_id':'director'}]}}, 'movie')

    async def test_search_reads_only_summary_and_tracks_updates_deletes_and_backfill(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(db,self.document(),'tmdb')
            await db.commit()
            queries=[]
            def record(conn,cursor,statement,parameters,context,executemany):
                queries.append(statement)
            event.listen(self.engine.sync_engine,'before_cursor_execute',record)
            try:
                found = await local_matches(db,'Projection','all')
            finally:
                event.remove(self.engine.sync_engine,'before_cursor_execute',record)
            self.assertEqual(len(queries),1)
            self.assertNotIn('catalogue_credits',queries[0])
            self.assertEqual((len(found['actor']),len(found['staff'])),(1,1))
            actor=int(found['actor'][0]['key'].split(':')[1])
            self.assertEqual((await db.get(ContributorSearch,actor)).work_count,1)
            credits=list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.work_id==work.id,
                CatalogueCredit.role=='actor')))
            for credit in credits:
                credit.attributes={**credit.attributes,'principal':False}
            await db.commit()
            self.assertEqual((await local_matches(db,'Projection','actor'))['actor'],[])
            self.assertEqual(await known_principal_status(db,{actor}),{actor:False})
            await db.execute(delete(CatalogueCredit).where(CatalogueCredit.work_id==work.id))
            await db.commit()
            self.assertEqual(await known_principal_status(db,{actor}),{})
        async with self.engine.begin() as conn:
            def migrate(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    migration=importlib.import_module('migrations.versions.mt050_contributor_search')
                    migration.downgrade(); migration.upgrade()
            await conn.run_sync(migrate)
        async with self.Session() as db:
            self.assertEqual(len((await local_matches(db,'Fixture','actor'))['actor']),1)
            person=await db.scalar(select(CatalogueEntity.id).where(CatalogueEntity.name=='Fixture Actor'))
            works=[CatalogueEntity(kind='movie',name=f'Concurrent {i}',attributes={}) for i in range(2)]
            db.add_all(works);await db.commit()
            work_ids=[w.id for w in works]
        async def add(work_id):
            async with self.Session() as db:
                db.add(CatalogueCredit(work_id=work_id,contributor_id=person,provider='tmdb',
                    source_key='concurrent',role='actor',character_label='Named',attributes={'principal':True}))
                await db.flush();await asyncio.sleep(.02);await db.commit()
        await asyncio.gather(*(add(work_id) for work_id in work_ids))
        async with self.Session() as db:
            self.assertEqual((await db.get(ContributorSearch,person)).work_count,3)

    async def test_refresh_preserves_credit_ids_links_and_skips_equal_credit_updates(self):
        async with self.Session() as db:
            doc=self.document()
            work=await catalogue.ingest_document(db,doc,'tmdb');await db.commit()
            def state_query():
                return text('SELECT id,screen_character_id,xmin::text FROM catalogue_credits WHERE work_id=:id ORDER BY id')
            before=(await db.execute(state_query(),{'id':work.id})).all()
            await catalogue.ingest_document(db,deepcopy(doc),'tmdb');await db.commit()
            self.assertEqual((await db.execute(state_query(),{'id':work.id})).all(),before)
            await fill_document(db,deepcopy(doc),'tmdb');await db.commit()
            self.assertEqual((await db.execute(state_query(),{'id':work.id})).all(),before)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit)
                .where(CatalogueCredit.work_id==work.id)),3)
            # Changed performer must invalidate the old composite FK before
            # rebuilding its actor/role link, while preserving source identity.
            changed=deepcopy(doc)
            changed['credits'][0]['contributor']['identities'][0]['external_id']='993'
            await catalogue.ingest_document(db,changed,'tmdb');await db.commit()
            after=(await db.execute(state_query(),{'id':work.id})).all()
            self.assertEqual([r.id for r in before],[r.id for r in after])
            self.assertNotEqual(before[0].screen_character_id,after[0].screen_character_id)

    async def test_repeated_identity_records_merge_in_source_order_and_protect_fields(self):
        async with self.Session() as db:
            doc=self.document()
            first=doc['credits'][0]
            middle=deepcopy(first);middle['source_key']='middle';middle['contributor']['name']='Intervening Name'
            final=deepcopy(first);final['source_key']='final'
            doc['credits']=[first,middle,final]
            work=await catalogue.ingest_document(db,doc,'tmdb');await db.commit()
            person=await db.scalar(select(CatalogueEntity).join(CatalogueCredit,
                CatalogueCredit.contributor_id==CatalogueEntity.id).where(CatalogueCredit.work_id==work.id).limit(1))
            self.assertEqual(person.name,'Projection Actor')
            person.protected_fields=['name'];person.name='Reviewed Name';await db.commit()
            await catalogue.ingest_document(db,doc,'tmdb');await db.commit();await db.refresh(person)
            self.assertEqual(person.name,'Reviewed Name')
            before=await db.scalar(select(func.count()).select_from(CatalogueEntity))
            conflicting=deepcopy(doc)
            for credit in conflicting['credits']:
                credit['contributor']['identities'][0]['external_id']='1999'
            await fill_document(db,conflicting,'tmdb');await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity)),before)

    async def test_image_candidates_are_bounded_distinct_and_filter_before_limit(self):
        async with self.Session() as db:
            work=await catalogue.ingest_document(db,self.document(),'tmdb');await db.commit()
            credit=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==work.id,
                CatalogueCredit.role=='actor').order_by(CatalogueCredit.id))
            character=await db.get(ScreenCharacter,credit.screen_character_id)
            anime=CatalogueEntity(kind='movie',name='Anime',attributes={'genres':[{'name':'Animation'}],
                'original_language':'ja','unrelated': 'x'*10000})
            ordinary=CatalogueEntity(kind='movie',name='Ordinary',attributes={'unrelated':'x'*10000})
            db.add_all([anime,ordinary]);await db.flush()
            db.add_all([CatalogueCredit(work_id=anime.id if i<20 else ordinary.id,
                contributor_id=character.actor_id,screen_character_id=character.id,provider='tvdb',
                source_key=f'image-{i}',role='actor',character_label='Named Hero',
                character_image_url=f'https://example.test/{i//2}.jpg') for i in range(200)])
            await db.commit()
            images=await character_images(db,{character.id},show_anime=False)
            rows=images[character.id]
            self.assertEqual(len(rows),4)
            self.assertEqual(len({r['url'] for r in rows}),4)
            self.assertTrue(all(r['work_id']==ordinary.id for r in rows))
            self.assertTrue(all('unrelated' not in r['attributes'] for r in rows))
            anime.attributes={'genres':['Animation'],'origin_countries':['JPN']}
            await db.commit()
            self.assertTrue(all(r['work_id']==ordinary.id for r in (await character_images(
                db,{character.id},show_anime=False))[character.id]))
            anime.attributes={'genres':['Animation'],'origin_countries':['US'],'production_countries':[{'iso_3166_1':'JP'}]}
            await db.commit()
            self.assertTrue(all(r['work_id']==anime.id for r in (await character_images(
                db,{character.id},show_anime=False))[character.id]))

    async def test_mixed_tvdb_bridges_do_not_disable_bulk_identity_resolution(self):
        async with self.Session() as db:
            doc=self.document()
            work=await catalogue.ingest_document(db,doc,'tmdb');await db.commit()
            root=await db.scalar(select(CatalogueCredit.contributor_id).where(CatalogueCredit.work_id==work.id,
                CatalogueCredit.source_key=='first'))
            alias=CatalogueEntity(kind='person',name='Verified Variant',attributes={})
            db.add(alias);await db.flush()
            db.add(CatalogueIdentity(entity_id=alias.id,namespace='tvdb.person',external_id='5991',source='tvdb'))
            await db.commit()
            mixed=deepcopy(doc)
            mixed['credits']=[deepcopy(doc['credits'][0])]
            mixed['credits'][0]['contributor']['identities'].append({'namespace':'tvdb.person','external_id':'5991'})
            for index in range(100):
                credit=deepcopy(doc['credits'][0]);credit['source_key']=f'mixed-{index}'
                credit['contributor']['name']=f'Single TVDB person {index}'
                credit['contributor']['identities']=[{'namespace':'tvdb.person','external_id':str(7000+index)}]
                mixed['credits'].append(credit)
            queries=[]
            def record(conn,cursor,statement,parameters,context,executemany):queries.append(statement)
            revision=await db.scalar(text('SELECT revision FROM stats_metadata_revision WHERE id=1'))
            event.listen(self.engine.sync_engine,'before_cursor_execute',record)
            try:
                await catalogue.ingest_document(db,mixed,'tvdb');await db.commit()
            finally:
                event.remove(self.engine.sync_engine,'before_cursor_execute',record)
            self.assertLess(len(queries),80)
            # Counts statements actually fired by PostgreSQL, including those
            # hidden inside a driver's executemany event.
            self.assertLess((await db.scalar(text('SELECT revision FROM stats_metadata_revision WHERE id=1')))-revision,60)
            await db.refresh(alias)
            self.assertEqual(alias.canonical_person_id,root)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit).where(
                CatalogueCredit.work_id==work.id,CatalogueCredit.provider=='tvdb')),101)
