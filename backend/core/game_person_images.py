"""Optional IMDb portraits, shared by source identity and restricted at reads.

Never publish these into the public person entity or overwrite chosen artwork.
The website endpoint's access is neither a service guarantee nor a licence.
"""
import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import load_only
from sqlalchemy.dialects.postgresql import insert

from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_imdb_enrichment import now
from core.game_imdb_source import IMDbGameSource
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot

COMPONENT='game_person_image'
_flights={}
_slots=asyncio.Semaphore(2)


async def identities(db,ids):
    """Resolve canonical people and protected artwork with bounded projections."""
    fields=[CatalogueEntity.id,CatalogueEntity.kind,CatalogueEntity.image_url,
        CatalogueEntity.protected_fields,CatalogueEntity.canonical_person_id,CatalogueEntity.canonical_company_id]
    people=list(await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(ids),
        CatalogueEntity.kind=='person').options(load_only(*fields))))
    aliases={person.canonical_person_id for person in people if person.canonical_person_id}
    canonical={person.id:person for person in await db.scalars(select(CatalogueEntity).where(
        CatalogueEntity.id.in_(aliases),CatalogueEntity.kind=='person').options(load_only(*fields)))} if aliases else {}
    roots={person.id:canonical.get(person.canonical_person_id,person) for person in people}
    natives={person.id:[] for person in roots.values()}
    for entity_id,native in await db.execute(select(CatalogueIdentity.entity_id,CatalogueIdentity.external_id).where(
        CatalogueIdentity.entity_id.in_(natives),CatalogueIdentity.namespace=='imdb.person')):
        natives[entity_id].append(native)
    return {person.id:{'image':roots[person.id].image_url,
        'protected':'image_url' in (roots[person.id].protected_fields or []),
        'native':natives[roots[person.id].id][0] if len(natives[roots[person.id].id])==1 else None}
        for person in people}


def query(natives):
    return select(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==COMPONENT,MetadataSnapshot.external_id.in_(natives))


def projection(row):
    value=dict(row.payload or {}) if row else {}
    return {'image':value.get('image'),'state':
        'ready' if row and row.fetched_at else 'retry' if row and row.error_code else 'pending'}


class GamePersonImages:
    def __init__(self,sessions,*,source=None):
        self.sessions=sessions
        self.scope=id(getattr(sessions,'kw',{}).get('bind',sessions))
        self.source=source or IMDbGameSource(ProviderHTTP(session_factory=sessions))

    async def get(self,natives):
        natives=list(dict.fromkeys(natives))
        if not natives:return {}
        if len(natives)>16:raise ProviderError('invalid_request')
        from core.game_imdb_source import identifier
        for native in natives:identifier(native,person=True)
        async with self.sessions() as db:
            rows={row.external_id:row for row in await db.scalars(query(natives))}
        result={native:projection(rows.get(native)) for native in natives}
        due=[native for native in natives if not (rows.get(native) and (
            rows[native].expires_at and rows[native].expires_at>now()
            or rows[native].next_attempt_at and rows[native].next_attempt_at>now()))]
        missing=[native for native in due if (self.scope,native) not in _flights]
        if missing and len(_flights)+len(missing)<=64:
            task=asyncio.create_task(self.refresh(missing))
            for native in missing:_flights[self.scope,native]=task
            def finished(done):
                for native in missing:
                    if _flights.get((self.scope,native)) is done:_flights.pop((self.scope,native),None)
                if not done.cancelled():done.exception()
            task.add_done_callback(finished)
        # Stale successful evidence paints immediately; cold callers share the
        # batch without owning its cancellation. Cross-worker leases stay small.
        waiting={_flights[self.scope,native] for native in due
            if not rows.get(native) or not rows[native].fetched_at
            if (self.scope,native) in _flights}
        for response in await asyncio.gather(*(asyncio.shield(task) for task in waiting)):
            for native in natives:
                if native in response:result[native]=response[native]
        return result

    async def refresh(self,natives):
        async with _slots:return await self._refresh(natives)

    async def _refresh(self,natives):
        token=uuid.uuid4().hex
        async with self.sessions() as db:
            await db.execute(insert(MetadataSnapshot).values([
                {'provider':'imdb','kind':COMPONENT,'external_id':native} for native in natives]).on_conflict_do_nothing())
            rows=list(await db.scalars(query(natives).order_by(MetadataSnapshot.external_id).with_for_update()))
            claimed=[]
            for row in rows:
                if (row.expires_at and row.expires_at>now() or row.next_attempt_at and row.next_attempt_at>now()
                    or row.lease_until and row.lease_until>now()):continue
                row.lease_token=token;row.lease_until=now()+timedelta(seconds=45)
                claimed.append(row.external_id)
            await db.commit()
        error=None;values={}
        if claimed:
            try:
                async with asyncio.timeout(30):values=await self.source.portraits(claimed)
            except (ProviderError,TimeoutError) as cause:
                error=cause.code if isinstance(cause,ProviderError) else 'unavailable'
            except Exception:error='unavailable'
        async with self.sessions() as db:
            rows=list(await db.scalars(query(natives).order_by(MetadataSnapshot.external_id).with_for_update()))
            for row in rows:
                if row.lease_token!=token:continue
                row.lease_token=row.lease_until=None
                if error or row.external_id not in values:
                    row.error_code=error or 'bad_response';row.attempts+=1
                    row.next_attempt_at=now()+timedelta(seconds=min(3600,60*2**min(row.attempts-1,6)))
                else:
                    value=values[row.external_id]
                    # A newly absent portrait cannot erase usable older artwork.
                    if value['image'] or not (row.payload or {}).get('image'):row.payload=value
                    row.coverage={'complete':True,'image_supplied':bool(value['image'])}
                    row.fetched_at=now();row.expires_at=now()+timedelta(days=30 if value['image'] else 7)
                    row.next_attempt_at=row.error_code=None;row.attempts=0
            result={row.external_id:projection(row) for row in rows}
            await db.commit()
        return result


async def portraits(db,ids):
    """Called only behind the Games router's approved-account dependency."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    before=await identities(db,ids)
    requested={value['native'] for value in before.values() if value['native'] and not value['image'] and not value['protected']}
    service=GamePersonImages(async_sessionmaker(db.bind,expire_on_commit=False))
    await db.commit()  # Do not hold a connection during optional provider I/O.
    images=await service.get(sorted(requested))
    # Recheck identity and edits after provider I/O; no stale request can undo an
    # image choice or apply a portrait to a newly conflicting person binding.
    db.expire_all()
    current=await identities(db,ids)
    return {'people':[{'id':person_id,'image':value['image'], 'state':'ready'}
        if value['image'] or value['protected'] else {'id':person_id,
            **(images.get(value['native'],{'image':None,'state':'unavailable'})
               if before.get(person_id,{}).get('native')==value['native'] else {'image':None,'state':'unavailable'})}
        for person_id,value in current.items()]}
