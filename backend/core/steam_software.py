"""Shared positive software exclusions; absence/failure never proves an app is a game."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core import steam_cache
from core.catalogue_providers import ProviderError
from core.config import settings
from core.steam import SteamError, SteamSource
from models.catalogue import MetadataSnapshot

KIND = 'steam_software_list'


async def classify(factory, games, *, source=None):
    if not games or (source is None and not settings.steam_web_api_key):
        return
    stamp = steam_cache.now()
    async with factory() as db:
        snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
            MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == 'all'))
        apps = set((snapshot.payload or {}).get('apps', [])) if snapshot else set()
        fresh = snapshot and snapshot.expires_at and snapshot.expires_at > stamp
        retry = snapshot and snapshot.next_attempt_at and snapshot.next_attempt_at > stamp
    if not fresh and not retry:
        try:
            apps = await (source or SteamSource(factory)).software()
        except (ProviderError, SteamError):
            # Keep old positive evidence; an outage must not hide real games.
            async with factory() as db:
                statement = insert(MetadataSnapshot).values(provider='steam', kind=KIND, external_id='all',
                    payload={'apps': sorted(apps)}, next_attempt_at=stamp + timedelta(minutes=15))
                await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
                    set_={'next_attempt_at': statement.excluded.next_attempt_at}))
                await db.commit()
        else:
            async with factory() as db:
                statement = insert(MetadataSnapshot).values(provider='steam', kind=KIND, external_id='all',
                    payload={'apps': sorted(apps)}, fetched_at=stamp, expires_at=stamp + timedelta(days=1))
                await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot', set_={
                    'payload': statement.excluded.payload, 'fetched_at': stamp,
                    'expires_at': statement.excluded.expires_at, 'next_attempt_at': None}))
                await db.commit()
    excluded = [{'app_id': game['app_id'], 'steam_app_type': 'software'} for game in games if game['app_id'] in apps]
    if excluded:
        await steam_cache.save_public(factory, excluded)
