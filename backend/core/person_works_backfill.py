"""Save a verified person's credited filmography in the shared catalogue only."""
from datetime import datetime
from sqlalchemy import select
from core import catalogue, tmdb
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.contributor_details import positive_id
from core.credit_roles import work_credit
from core.media_credits import VERSION
from models.catalogue import MetadataSnapshot


async def backfill_person_works(db, providers, native, *, kind='staff'):
    profile = await tmdb.get_person(native, api_key=providers.tmdb_key)
    if positive_id(profile.get('id')) != native:
        raise catalogue.IdentityConflict('Provider person identity mismatch')
    fields = ('cast', 'crew') if kind == 'all' else ('cast',) if kind == 'actor' else ('crew',)
    wanted = set()
    for field in fields:
        for raw in (profile.get('combined_credits') or {}).get(field) or []:
            media = {'movie':'movie', 'tv':'series'}.get(raw.get('media_type'))
            external = positive_id(raw.get('id'))
            if media and external and not raw.get('adult') and work_credit('Actor' if field=='cast' else raw.get('job'), raw.get('character')):
                wanted.add((media, external))
    result = {'examined':len(wanted), 'updated':0, 'deferred':0, 'failed':0}
    for media, external in sorted(wanted):
        try:
            snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='tmdb',
                MetadataSnapshot.kind==media, MetadataSnapshot.external_id==str(external)))
            if snapshot and snapshot.payload and catalogue.cached_refresh_status(snapshot,datetime.utcnow())=='cached':
                await catalogue.write_lock(db)
                await db.refresh(snapshot,with_for_update=True)
                if catalogue.cached_refresh_status(snapshot,datetime.utcnow())!='cached':
                    await db.commit()
                    result['deferred']+=1
                    continue
                doc = normalize_tmdb(snapshot.payload,media)
                if not any(i['namespace']==f'tmdb.{media}' and i['external_id']==str(external) for i in doc['work']['identities']):
                    raise catalogue.IdentityConflict('Cached work identity mismatch')
                async with db.begin_nested():
                    work = await fill_document(db,doc,'tmdb')
                    if snapshot.entity_id and snapshot.entity_id!=work.id:
                        raise catalogue.IdentityConflict('Cached work bridge mismatch')
                    snapshot.entity_id=work.id
                    snapshot.coverage={**(snapshot.coverage or {}),'detail_credits_version':VERSION}
                await db.commit()
                result['updated']+=1
            else:
                receipt=await catalogue.refresh_metadata(db,providers,'tmdb',media,str(external),additive=True)
                result['updated']+=int(receipt['status']=='updated')
                result['failed']+=int(receipt['status']=='failed')
                result['deferred']+=int(receipt['status'] not in ('updated','cached','failed'))
        except Exception:
            await db.rollback()
            result['failed']+=1
    return result
