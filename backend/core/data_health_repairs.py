"""Bounded additive repairs: no duplicate merges or personal-history edits."""
import asyncio
from datetime import timedelta

from sqlalchemy import select, text, update

from core import catalogue, catalogue_normalize, settings_store, statistics_snapshots
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.catalogue_additive import fill_document
from core.character_backfill import backfill_screen_characters, backfill_tvmaze_images
from core.country_backfill import backfill_catalogue_countries, backfill_legacy_countries
from core.data_health import COHORT, HAS_GENRES, JOB_TYPE, LOCK_KEY, NAMED_ROLE
from core.runtime_backfill import backfill_history_runtimes
from db import AsyncSessionLocal, engine
from models.catalogue import CatalogueCredit, CatalogueEntity, MetadataSnapshot
from models.statistics import UserStatsState
from models.sync import SyncJob, SyncStatus

STATS_BATCH = 20


async def fill_screen_metadata(db, providers, *, limit=10):
    """Verified, tracked screen titles only; retain provider retry backoff."""
    rows = (await db.execute(text(COHORT + f"""
      SELECT * FROM titles t WHERE coalesce(root_count,0)<=1 AND
      (tmdb_id IS NOT NULL OR tvdb_id IS NOT NULL) AND
      (entity_id IS NULL OR NOT EXISTS (SELECT 1 FROM tracked_credits c WHERE c.work_id=t.entity_id AND c.role='actor')
       OR NOT EXISTS (SELECT 1 FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id AND p.kind='person' WHERE c.work_id=t.entity_id AND c.role<>'actor')
       OR NOT EXISTS (SELECT 1 FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id AND p.kind='organization' WHERE c.work_id=t.entity_id AND c.role='producer')
       OR NOT {HAS_GENRES}
       OR (nullif(image_url,'') IS NULL AND nullif(poster_path,'') IS NULL))
      AND (attrs->>'health_metadata_attempted_at' IS NULL OR attrs->>'health_metadata_attempted_at'<:cutoff)
      ORDER BY attrs->>'health_metadata_attempted_at' NULLS FIRST,id LIMIT :limit
    """), {'cutoff': (statistics_snapshots.utcnow() - timedelta(days=7)).isoformat(), 'limit': limit})).mappings().all()
    result = {'examined': len(rows), 'updated': 0, 'failed': 0, 'deferred': 0}
    for row in rows:
        succeeded = False
        for provider in ('tmdb', 'tvdb'):
            key = getattr(providers, provider + '_key')
            external_id = row[provider + '_id']
            if not external_id:
                continue
            kind = row['media_type'].value if hasattr(row['media_type'], 'value') else row['media_type']
            try:
                snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == provider,
                    MetadataSnapshot.kind == kind, MetadataSnapshot.external_id == str(external_id)))
                now = statistics_snapshots.utcnow()
                if snapshot and ((snapshot.next_attempt_at and snapshot.next_attempt_at > now) or
                    (snapshot.lease_token and snapshot.lease_until and snapshot.lease_until > now)):
                    result['deferred'] += 1
                    continue
                if snapshot and snapshot.payload and not snapshot.error_code:
                    doc = catalogue_normalize.normalize_tmdb(snapshot.payload, kind) if provider == 'tmdb' else catalogue_normalize.normalize_tvdb(snapshot.payload)
                elif key:
                    receipt = await catalogue.refresh_metadata(db, providers, provider, kind, str(external_id),
                        additive=True, expected_entity_id=row['entity_id'])
                    if receipt['status'] != 'updated':
                        result['failed' if receipt.get('error_code') else 'deferred'] += 1
                        continue
                    work = await db.get(CatalogueEntity, receipt['entity_id'])
                    work.attributes = {**work.attributes, 'health_metadata_attempted_at': now.isoformat()}
                    await db.commit()
                    succeeded = True
                    continue
                else:
                    result['deferred'] += 1
                    continue
                # Never accept a wrong provider reply, even if its name matches.
                if not any(i['namespace'] == f'{provider}.{kind}' and i['external_id'] == str(external_id) for i in doc['work']['identities']):
                    raise catalogue.IdentityConflict('Provider reply identity mismatch')
                async with db.begin_nested():
                    work = await fill_document(db, doc, provider)
                    if row['entity_id'] and work.id != row['entity_id']:
                        raise catalogue.IdentityConflict('Existing title bridge requires review')
                    work.attributes = {**work.attributes, 'health_metadata_attempted_at': now.isoformat()}
                await db.commit()
                succeeded = True
            except Exception:
                await db.rollback()
                result['failed'] += 1
        if succeeded:
            result['updated'] += 1
    return result


async def refresh_statistics(factory):
    """One-off request for up to 20 accounts. Normal daily scheduling is unchanged."""
    now = statistics_snapshots.utcnow()
    async with factory() as db:
        await db.execute(text("""INSERT INTO user_stats_state (user_id)
            SELECT DISTINCT user_id FROM tracked_entries UNION SELECT DISTINCT user_id FROM watch_events
            ON CONFLICT (user_id) DO NOTHING"""))
        await db.execute(update(UserStatsState).where(UserStatsState.lease_token.is_not(None),
            (UserStatsState.lease_until.is_(None)) | (UserStatsState.lease_until <= now)
        ).values(lease_token=None, lease_until=None))
        ids = list(await db.scalars(select(UserStatsState.user_id).where(
            (UserStatsState.lease_token.is_(None)) | (UserStatsState.lease_until <= now)
        ).order_by(UserStatsState.next_due_at, UserStatsState.user_id).limit(STATS_BATCH)))
        await db.execute(update(UserStatsState).where(UserStatsState.user_id.in_(ids)).values(dirty=True, next_due_at=now))
        await db.commit()
    completed = await statistics_snapshots.run_due(factory, limit=len(ids), user_ids=ids)
    return {'requested': len(ids), 'published': completed, 'pending': len(ids) - completed}


async def run_job(job_id, *, factory=AsyncSessionLocal, bind=engine):
    # A session advisory lock survives per-step commits and serializes workers.
    # The pool connection is explicitly unlocked before it can be reused.
    async with bind.connect() as lock:
        acquired = await lock.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_KEY})
        await lock.commit()
        if not acquired:
            return
        try:
            async with factory() as db:
                job = await db.scalar(select(SyncJob).where(SyncJob.id == job_id, SyncJob.job_type == JOB_TYPE).with_for_update())
                if job is None or job.status != SyncStatus.pending:
                    return
                action = job.stats['action']
                job.status = SyncStatus.running
                job.updated_at = statistics_snapshots.utcnow()
                await db.commit()

            async def step(label, task):
                async with factory() as db:
                    await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(current_step=label, updated_at=statistics_snapshots.utcnow()))
                    await db.commit()
                result = await task()
                async with factory() as db:
                    job = await db.get(SyncJob, job_id)
                    job.stats = {**job.stats, 'results': {**job.stats.get('results', {}), label: result}}
                    job.processed_items += 1
                    job.errors += result.get('failed', 0)
                    job.updated_at = statistics_snapshots.utcnow()
                    await db.commit()

            async with asyncio.timeout(600):
                async with factory() as db:
                    global_settings = await settings_store.get_global_settings(db, cached=False)
                    tmdb_key = settings_store.get_server_tmdb_key(global_settings)
                    tvdb_key, pin = settings_store.get_server_tvdb_credentials(global_settings)
                    if tvdb_key:
                        from core import tvdb
                        tvdb.set_subscriber_pin(tvdb_key, pin)
                    providers = CatalogueProviders(ProviderHTTP(session_factory=factory), tmdb_key=tmdb_key, tvdb_key=tvdb_key)
                    if action == 'metadata':
                        await step('Screen metadata', lambda: fill_screen_metadata(db, providers))
                        await step('Watched runtimes', lambda: backfill_history_runtimes(db, tmdb_key, tvdb_api_key=tvdb_key, limit=50))
                        async def countries():
                            legacy = await backfill_legacy_countries(db, tmdb_key, tvdb_api_key=tvdb_key, limit=50)
                            canonical = await backfill_catalogue_countries(db, providers, limit=25, missing_only=True)
                            return {key: legacy.get(key, 0) + canonical.get(key, 0) for key in ('examined', 'recovered', 'unresolved', 'failed')}
                        await step('Country metadata', countries)
                        await step('Character artwork', lambda: backfill_tvmaze_images(db, providers, limit=10))
                    if action in ('metadata', 'characters'):
                        await step('Character links', lambda: backfill_screen_characters(db, limit=100))
                if action == 'characters':
                    await step('Unmapped roles', lambda: repair_unmapped_roles(factory))
                if action == 'statistics':
                    await step('Statistics', lambda: refresh_statistics(factory))
            async with factory() as db:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.completed,
                    current_step='Completed', updated_at=statistics_snapshots.utcnow()))
                await db.commit()
        except (Exception, asyncio.CancelledError):
            async with factory() as db:
                await db.execute(update(SyncJob).where(SyncJob.id == job_id).values(status=SyncStatus.failed,
                    error_message='repair_incomplete', updated_at=statistics_snapshots.utcnow()))
                await db.commit()
            # Cancellation must propagate to server shutdown; completed work survives.
            if asyncio.current_task().cancelling():
                raise
        finally:
            await lock.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_KEY})
            await lock.commit()


async def repair_unmapped_roles(factory):
    # Version stamps alone cannot detect later, direct credit edits.
    from core.screen_characters import project_characters
    async with factory() as db:
        ids = list(await db.scalars(select(CatalogueCredit.work_id).join(CatalogueEntity,
            CatalogueEntity.id == CatalogueCredit.work_id).where(CatalogueCredit.role == 'actor',
            CatalogueCredit.screen_character_id.is_(None), CatalogueCredit.character_label.is_not(None),
            text(NAMED_ROLE.replace('c.character_label','catalogue_credits.character_label')),
            CatalogueEntity.kind.in_(['movie', 'series'])).distinct().order_by(CatalogueCredit.work_id).limit(100)))
        await catalogue.write_lock(db)
        count = sum([await project_characters(db, id) for id in ids])
        await db.commit()
        return {'examined': len(ids), 'credits_mapped': count}
