"""Repair imported artwork from exact Steam references, outside tracking writes."""
from datetime import datetime, timedelta
import asyncio

import httpx

from sqlalchemy import select

from core import catalogue, steam_cache
from core.catalogue_normalize import image
from core.catalogue_providers import ProviderError
from core.config import settings
from core.game_browse_sources import GameBrowseSources
from core.game_types import canonical_game
from models.catalogue import CatalogueEntity, CatalogueIdentity

_headers=asyncio.Semaphore(4)


async def steam_header(app):
    """Some older apps have only a landscape header, no library portrait."""
    from core.game_achievement_source import native_app
    native_app(app)
    url=f'https://cdn.akamai.steamstatic.com/steam/apps/{app}/header.jpg'
    async with _headers, httpx.AsyncClient(timeout=5) as client:
        try:
            response=await client.head(url)
            if response.status_code==404:
                return None
            if response.status_code==200 and response.headers.get('content-type','').startswith('image/'):
                return url
            raise ProviderError('unavailable')
        except httpx.HTTPError:
            raise ProviderError('unavailable') from None


async def refresh(factory, games, *, source=None, fallback=None):
    if source is None and not (settings.igdb_client_id and settings.igdb_client_secret):
        return
    apps=list(dict.fromkeys(game['app_id'] for game in games))
    if not apps:
        return
    async with factory() as db:
        cached=await steam_cache.public_metadata(db, apps)
    stamp=steam_cache.now()
    due=[]
    for app in apps:
        item=cached.get(app,{})
        # Canonical artwork already wins. Failed acquisition is retryable;
        # successful empty/ambiguous references have a bounded negative cache.
        if item.get('poster') and 'images.igdb.com/' in item['poster']:
            continue
        checked=item.get('artwork_checked_at')
        if not checked or datetime.fromisoformat(checked)<=stamp-timedelta(days=30):
            due.append(app)
    if source is None:
        source=GameBrowseSources(factory)
        fallback=fallback or steam_header
    for offset in range(0,len(due),100):
        batch=due[offset:offset+100]
        try:
            rows=await source.steam_games([int(app) for app in batch])
        except ProviderError:
            continue  # An outage must never become a verified missing cover.
        matches={app:{} for app in batch}
        for row in rows:
            app=str(row.get('uid',''))
            raw=canonical_game(row.get('game'))
            if app in matches and raw:
                matches[app][raw['id']]=raw
        values=[]
        for app, candidates in matches.items():
            value={'app_id':app,'artwork_checked_at':stamp.isoformat()}
            if len(candidates)==1:
                raw=next(iter(candidates.values()))
                poster=image(raw.get('cover'))
                if poster:
                    value.update(poster=poster,poster_source='igdb',poster_verified=True)
            values.append(value)
        async def complete(value):
            if not value.get('poster') and fallback:
                try:
                    poster=await fallback(value['app_id'])
                except ProviderError:
                    value.pop('artwork_checked_at',None)
                    return
                if poster:
                    value.update(poster=poster,poster_source='steam',poster_verified=True)
        await asyncio.gather(*(complete(value) for value in values))
        async with factory() as db:
            await catalogue.write_lock(db)
            works=await db.execute(select(CatalogueIdentity.external_id,CatalogueEntity).join(
                CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(
                CatalogueIdentity.namespace=='steam.app',CatalogueIdentity.external_id.in_(batch))
                .order_by(CatalogueEntity.id).with_for_update(of=CatalogueEntity))
            by_app={value['app_id']:value for value in values}
            for app, work in works:
                poster=by_app[app].get('poster')
                if poster and (not work.image_url or (work.field_sources or {}).get('image_url')=='steam'):
                    # Use the explicit cross-reference's cover without merging
                    # catalogue identities or touching private list state.
                    catalogue.merge_fields(work,{'image_url':poster},by_app[app]['poster_source'])
            await db.commit()
        await steam_cache.save_public(factory,values)
