"""Principal-credit SQL selection, cached reprojection and verified studio aliases."""
import importlib
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text

import test_statistics_pg as fixture
from core import catalogue
from core.catalogue_normalize import normalize_tmdb, normalize_tvdb
from core.contributor_search import local_matches, known_principal_status
from core.media_details import work_details
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, MetadataSnapshot
from models import Media


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class PrincipalCreditDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def test_import_keeps_full_credits_while_details_search_and_artwork_use_principals(self):
        raw = {'id':10, 'title':'Selected', 'production_companies':[], 'credits':{
            'cast':[{'id':1000+i,'name':f'Actor {i}','order':i,'character':f'Character {i}'} for i in range(80)],
            'crew':[{'id':2000+i,'name':f'Minor {i}','job':'Production Assistant'} for i in range(100)]+
                   [{'id':3000+i,'name':f'Producer {i}','job':'Producer','popularity':i} for i in range(30)]+
                   [{'id':4000,'name':'Responsible Director','job':'Director'}]}}
        async with self.Session() as db:
            work = await catalogue.ingest_document(db, normalize_tmdb(raw,'movie'),'tmdb')
            await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit)
                .where(CatalogueCredit.work_id==work.id)),211)
            with patch('core.media_details.character_images',AsyncMock(return_value={})) as art:
                details=await work_details(db,work,extras=False)
                self.assertLessEqual(len(art.await_args.args[1]),16)
            self.assertEqual(len([p for p in details['people'] if p['kind']=='actor']),16)
            self.assertEqual(len([p for p in details['people'] if p['kind']=='staff']),3)
            self.assertEqual((await local_matches(db,'Minor','staff'))['staff'],[])
            self.assertEqual(len((await local_matches(db,'Producer','staff'))['staff']),2)
            minor=await db.scalar(select(CatalogueEntity.id).where(CatalogueEntity.name=='Minor 0'))
            self.assertFalse((await known_principal_status(db,{minor}))[minor])

    async def test_cached_reprojection_updates_existing_counts_and_selection_without_provider_io(self):
        from core.media_credits import fill_screen_credits
        async with self.Session() as db:
            media = await db.get(Media,self.media_id)
            media.tmdb_id=777
            work=await db.scalar(select(CatalogueEntity).join(CatalogueLegacyLink,
                CatalogueLegacyLink.entity_id==CatalogueEntity.id).where(CatalogueLegacyLink.media_id==media.id))
            raw={'id':777,'title':'Updated','production_companies':[], 'credits':{'cast':[],
                'crew':[{'id':88,'name':'Editor','job':'Editor','credit_id':'editor'}]}}
            # The old association already exists, but lacks selection evidence.
            doc=normalize_tmdb(raw,'movie')
            doc['work']['identities']=[{'namespace':'tmdb.movie','external_id':'777'}]
            await catalogue.bind_identity(db,work.id,'tmdb.movie','777','tmdb')
            await catalogue.ingest_document(db,doc,'tmdb')
            credit=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.source_key=='editor'))
            credit.attributes={}
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id='777',entity_id=work.id,
                payload=raw,coverage={'detail_credits_version':2},expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            providers=SimpleNamespace(tmdb_key='fixture',tvdb_key=None,detail=AsyncMock())
            self.assertEqual(await fill_screen_credits(db,media,providers=providers),'updated')
            await db.refresh(credit)
            self.assertTrue(credit.attributes['principal'])
            providers.detail.assert_not_awaited()

    async def test_selected_tv_producers_expose_role_counts_without_combining_jobs(self):
        raw={'id':99,'name':'Show','number_of_episodes':60,'created_by':[],
             'aggregate_credits':{'cast':[],'crew':[{'id':80,'name':'Producer','total_episode_count':60,
                 'jobs':[{'job':'Executive Producer','credit_id':'ep','episode_count':12},
                         {'job':'Producer','credit_id':'p','episode_count':8},
                         {'job':'Director','credit_id':'d','episode_count':60}]}]}}
        async with self.Session() as db:
            work=await catalogue.ingest_document(db,normalize_tmdb(raw,'series'),'tmdb')
            await db.commit()
            person=(await work_details(db,work,extras=False))['people'][0]
            self.assertEqual(person['producer_episode_count'],12)
            self.assertEqual(person['episode_count'],60)
            self.assertEqual(person['episode_total'],60)

    async def test_verified_studio_bridge_preserves_history_and_excludes_historical_namesakes(self):
        from core.person_identities import reconcile_company_identities
        from core.contributor_details import known_works
        async with self.Session() as db:
            root=await db.get(CatalogueEntity,self.studio_id)
            root.name='Marvel Studios'
            alias=CatalogueEntity(kind='organization',name='Marvel Studios, LLC',attributes={})
            historical=CatalogueEntity(kind='organization',name='Marvel Films',attributes={})
            db.add_all([alias,historical]); await db.flush()
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            db.add_all([CatalogueIdentity(entity_id=alias.id,namespace='tvdb.company',external_id='88',source='tvdb'),
                       CatalogueIdentity(entity_id=historical.id,namespace='tmdb.company',external_id='2301',source='tmdb'),
                       CatalogueCredit(work_id=work.id,contributor_id=alias.id,provider='tvdb',source_key='alias-studio',role='producer'),
                       CatalogueCredit(work_id=work.id,contributor_id=historical.id,provider='tmdb',source_key='old-studio',role='producer')])
            await db.commit()
            old_credit=await db.scalar(select(CatalogueCredit.id).where(CatalogueCredit.source_key=='alias-studio'))
            await reconcile_company_identities(db,[{'namespace':'tvdb.company','external_id':'88'},
                {'namespace':'tmdb.company','external_id':'99'}],'tvdb')
            await db.commit()
            self.assertEqual(alias.canonical_company_id,root.id)
            self.assertIsNone(historical.canonical_company_id)
            self.assertEqual((await db.get(CatalogueCredit,old_credit)).contributor_id,alias.id)
            studios=(await local_matches(db,'Marvel','studio'))['studio']
            self.assertEqual({p['name'] for p in studios},{'Marvel Studios','Marvel Films'})
            self.assertEqual((await local_matches(db,'LLC','studio'))['studio'][0]['key'],f'catalogue:{root.id}')
            details=await work_details(db,work,extras=False)
            self.assertEqual([p['name'] for p in details['organizations']].count('Marvel Studios'),1)
            self.assertEqual(len(await known_works(db,root,99,'studio',None)),1)
            doc=normalize_tvdb({'id':5,'companies':{'studio':[{'id':88,'name':'Marvel Studios, LLC',
                'remoteIds':[{'sourceName':'TheMovieDB.com','id':'99'}]}]}})
            await catalogue.ingest_document(db,doc,'tvdb'); await db.commit()

    async def test_additive_migrations_preserve_existing_credit_and_tracking_rows(self):
        async with self.engine.begin() as conn:
            before=await conn.scalar(text('SELECT count(*) FROM catalogue_credits'))
            def migrate(connection):
                with Operations.context(MigrationContext.configure(connection)):
                    for revision in ('mt050_contributor_search','mt049_verified_company_aliases','mt048_principal_credits'):
                        importlib.import_module('migrations.versions.'+revision).downgrade()
                    for revision in ('mt048_principal_credits','mt049_verified_company_aliases','mt050_contributor_search'):
                        importlib.import_module('migrations.versions.'+revision).upgrade()
            await conn.run_sync(migrate)
            self.assertEqual(await conn.scalar(text('SELECT count(*) FROM catalogue_credits')),before)
            self.assertEqual(await conn.scalar(text('SELECT count(*) FROM tracked_entries')),1)
