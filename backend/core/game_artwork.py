"""Optional SteamGridDB artwork, shared durably and fetched only on exposure."""
import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from core.catalogue_providers import ProviderHTTP, ProviderError
from core.config import settings
from models.catalogue import CatalogueIdentity

VERSION=2


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def asset_url(value):
    if not isinstance(value,str):return None
    parsed=urlsplit(value)
    return value if parsed.scheme=='https' and parsed.hostname=='cdn2.steamgriddb.com' and not parsed.username else None


def candidates(rows,kind):
    """Keep provider vote order (the API ranks by score), including tied scores."""
    animated=kind=="heroes_animated"
    kind="heroes" if animated else kind
    result=[]
    for row in rows:
        if not isinstance(row,dict):continue
        width,height=row.get('width'),row.get('height')
        if (row.get('style')!='alternate' or row.get('language') not in ('en',None)
            or any(row.get(flag) for flag in (('humor','nsfw','epilepsy') if animated else ('humor','nsfw','epilepsy','animated')))
            or row.get('mime') not in ('image/png','image/jpeg','image/webp')
            or not all(type(value) is int and value>0 for value in (width,height))):continue
        if kind=='grids' and not .6<=width/height<=.75:continue
        if kind=='heroes' and not 1.5<=width/height<=4:continue
        url=asset_url(row.get('url'))
        if url and url not in [value['url'] for value in result]:
            result.append({'url':url,'thumbnail':asset_url(row.get('thumb')),'id':row.get('id'),'upvotes':row.get('upvotes',0),'score':row.get('score',0)})
    return result


def cycling(rows):
    # Some API responses redact every vote counter to zero. In that case
    # retain the provider's ranked order rather than interpreting it as dislike.
    rated=any((row.get('upvotes') or 0)!=0 or (row.get('score') or 0)!=0 for row in rows)
    eligible=[row for row in rows if (row.get('upvotes') or 0)>=2 or (row.get('score') or 0)>=2] if rated else rows
    return [row['url'] for row in eligible[:6]]


async def retrieve(http,app,kind):
    animated=kind=='heroes_animated'
    endpoint='heroes' if animated else kind
    params={'styles':'alternate','types':'animated' if animated else 'static','nsfw':'false','humor':'false',
        'epilepsy':'false','languages':'en','mimes':'image/png,image/jpeg,image/webp','limit':50}
    if kind=='grids':params['dimensions']='600x900,342x482'
    try:
        raw=await http.request('steamgriddb','GET',f'https://www.steamgriddb.com/api/v2/{endpoint}/steam/{app}',
            headers={'Authorization':'Bearer '+settings.steamgriddb_api_key},params=params,attempts=1)
    except ProviderError as error:
        if error.code=='not_found':return []
        raise
    if not isinstance(raw,dict) or raw.get('success') is not True or not isinstance(raw.get('data'),list):
        raise ProviderError('bad_response')
    return candidates(raw['data'],kind)


async def images(db,work,kind,*,http=None):
    from core.game_source_cache import GameSourceCache,session_factory
    if not settings.steamgriddb_api_key:return [],{'state':'unavailable'}
    identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work.id,
        CatalogueIdentity.namespace=='steam.app')))
    apps=sorted({row.external_id for row in identities if row.external_id.isdecimal() and 0<int(row.external_id)<=4294967295},key=int)
    primary=str((work.attributes or {}).get('steam_primary_app_id') or '')
    if primary in apps:apps=[primary]
    if not apps or len(apps)>8:return [],{'state':'unavailable'}
    factory=session_factory(db.bind)
    cache=GameSourceCache(factory,max_records=1024)
    http=http or ProviderHTTP(session_factory=factory)
    key=f'{VERSION}:'+','.join(apps)
    async def load():
        values=[]
        async with asyncio.timeout(20):
            for app in apps:values.extend(await retrieve(http,app,kind))
        return {'images':list({value['url']:value for value in values}.values())}
    # The shared cache owns its sessions, durable leases, concurrent flights,
    # cancellation and retry cooldowns. No page transaction spans provider I/O.
    await db.commit()
    try:
        result=await cache.get('steamgriddb','game_art_'+kind,key,load,
            ttl=timedelta(days=14),max_stale=timedelta(days=90))
        values=result['data']['images']
        state=({'state':'deferred','error':'unavailable','retry_after':60} if result['error'] else
            {'state':'pending','retry_after':2} if result['stale'] else {'state':'ready'})
        expiry=datetime.fromisoformat(result['fetched_at'])+timedelta(days=14)
    except ProviderError as error:
        values=[];state={'state':'deferred','error':'unavailable','retry_after':max(60,error.retry_after or 0)}
        expiry=now()
    if kind=='heroes':
        await db.refresh(work,with_for_update=True)
        previous=(work.attributes or {}).get('steamgriddb_hero') or {}
        projection={'image':values[0]['url'] if values else previous.get('image') if state.get('error') else None,
            'expires_at':expiry.isoformat(),'version':VERSION,'retry':bool(state.get('error'))}
        if projection!=previous:
            work.attributes={**(work.attributes or {}),'steamgriddb_hero':projection}
        await db.commit()
    return values,state


def background_status(work):
    if not settings.steamgriddb_api_key:return {'state':'unavailable'}
    cache=(work.attributes or {}).get('steamgriddb_hero') or {}
    try:fresh=cache.get('version')==VERSION and datetime.fromisoformat(cache.get('expires_at',''))>now()
    except (ValueError,TypeError):fresh=False
    return {'state':'ready' if fresh and not cache.get('retry') else 'pending'}


def primary_background(work):
    """The detail hero policy, without constructing galleries or doing I/O."""
    from core.catalogue_normalize import image, GAME_ARTWORK_VERSION

    def first_image(values):
        return next((url for value in values or [] if (url := image(value))), None)

    attrs = work.attributes or {}
    selected = image(attrs.get('backdrop_path'))
    if not selected and 'attributes.artwork' in (work.protected_fields or []):
        selected = first_image(attrs.get('artwork'))
    scenes = 'scene_artwork' if attrs.get('artwork_policy_version') in range(1, GAME_ARTWORK_VERSION + 1) else 'artwork'
    scene = first_image(attrs.get(scenes))
    hero = asset_url((attrs.get('steamgriddb_hero') or {}).get('image'))
    return {'backdrop': selected or hero or scene, 'backdrop_mobile': selected or scene or hero,
            'background_status': background_status(work)}
