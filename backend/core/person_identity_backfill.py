"""Resumable authoritative person bridges, including unlisted crew and actors."""
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import or_, select
from core import catalogue, tvdb
from core.person_identities import verified_tvdb_person
from models.catalogue import CatalogueEntity, CatalogueIdentity

VERSION = 1


async def backfill_person_identities(db, api_key, *, limit=50, native=None):
    now = datetime.utcnow()
    attrs = CatalogueEntity.attributes
    query = select(CatalogueIdentity).join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
        CatalogueEntity.kind == 'person', CatalogueIdentity.namespace == 'tvdb.person',
        CatalogueIdentity.external_id == str(native) if native else or_(
            attrs['person_identity_version'].astext.is_(None), attrs['person_identity_version'].astext != str(VERSION)),
        True if native else or_(attrs['person_identity_attempted_at'].astext.is_(None),
                               attrs['person_identity_attempted_at'].astext < (now-timedelta(days=7)).isoformat()),
    ).order_by(CatalogueIdentity.id).limit(limit)
    rows = list(await db.scalars(query)) if api_key else []
    result = {'examined':len(rows), 'bridged':0, 'aliases':0, 'unavailable':0, 'failed':0}
    semaphore = asyncio.Semaphore(6)

    async def fetch(identity):
        async with semaphore:
            try:
                async with asyncio.timeout(20):
                    return await tvdb.get_person(int(identity.external_id), api_key)
            except Exception:
                return None

    replies = await asyncio.gather(*(fetch(row) for row in rows))
    if not rows:
        return result
    await catalogue.write_lock(db)
    for identity, raw in zip(rows, replies):
        await db.refresh(identity)
        person = await db.get(CatalogueEntity, identity.entity_id)
        person.attributes = {**(person.attributes or {}), 'person_identity_attempted_at': now.isoformat()}
        if raw is None:
            result['failed'] += 1
            continue
        try:
            async with db.begin_nested():
                previous = identity.entity_id
                linked = await verified_tvdb_person(db, identity.external_id, raw)
                await db.refresh(identity)
                canonical = await db.get(CatalogueEntity, identity.entity_id)
                canonical.attributes = {**(canonical.attributes or {}), 'person_identity_version': VERSION,
                                        'person_identity_attempted_at': now.isoformat()}
                result['bridged'] += int(linked)
                result['aliases'] += int(previous != identity.entity_id)
                result['unavailable'] += int(not linked)
        except catalogue.IdentityConflict:
            result['failed'] += 1
    await db.commit()
    return result
