"""Repair imported artwork from exact Steam references, outside tracking writes."""
from datetime import datetime, timedelta

from sqlalchemy import select

from core import catalogue, steam_cache
from core.catalogue_normalize import image
from core.catalogue_providers import ProviderError
from core.config import settings
from core.game_browse_sources import GameBrowseSources
from core.game_types import canonical_game
from models.catalogue import CatalogueEntity, CatalogueIdentity


async def refresh(factory, games, *, source=None):
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
    source=source or GameBrowseSources(factory)
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
                    value.update(poster=poster)
            values.append(value)
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
                    catalogue.merge_fields(work,{'image_url':poster},'igdb')
            await db.commit()
        await steam_cache.save_public(factory,values)
