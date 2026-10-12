"""Prefer cached, identity-matched TMDB portraits without provider requests."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from core import catalogue
from core.catalogue_normalize import image
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot


def cached_portraits(snapshot):
    candidates = {}
    if snapshot.kind == 'person':
        rows = [{'id':snapshot.external_id, 'profile_path':snapshot.profile}]
    else:
        rows = [*(snapshot.cast or []), *(snapshot.aggregate_cast or []), *(snapshot.crew or [])]
    for row in rows:
        native = str(row.get('id', ''))
        path = row.get('profile_path')
        if native.isdecimal() and isinstance(path, str) and path.startswith('/') and not path.startswith('//'):
            url = image('https://image.tmdb.org/t/p/original'+path)
            if url and url.startswith('https://image.tmdb.org/t/p/'):
                candidates[native] = url
    return candidates


async def backfill(db, *, cursor=0, limit=25, apply=False):
    rows = (await db.execute(select(MetadataSnapshot.id, MetadataSnapshot.kind, MetadataSnapshot.external_id,
        MetadataSnapshot.payload['profile_path'].astext.label('profile'),
        MetadataSnapshot.payload['credits']['cast'].label('cast'),
        MetadataSnapshot.payload['aggregate_credits']['cast'].label('aggregate_cast'),
        MetadataSnapshot.payload['credits']['crew'].label('crew'))
        .where(MetadataSnapshot.provider=='tmdb',MetadataSnapshot.kind.in_(['movie','series','person']),MetadataSnapshot.id>cursor)
        .order_by(MetadataSnapshot.id).limit(limit))).all()
    updated=0
    for snapshot in rows:
        candidates=cached_portraits(snapshot)
        if not candidates:
            continue
        if apply:
            await catalogue.write_lock(db)
        people=(await db.execute(select(CatalogueEntity,CatalogueIdentity.external_id)
            .join(CatalogueIdentity,CatalogueIdentity.entity_id==CatalogueEntity.id)
            .where(CatalogueEntity.kind=='person',CatalogueIdentity.namespace=='tmdb.person',
                CatalogueIdentity.external_id.in_(candidates)))).all()
        for person,native in people:
            if catalogue.preferred_person_portrait(person,candidates[native],'tmdb'):
                updated+=1
                if apply:
                    catalogue.merge_fields(person,{'image_url':candidates[native]},'tmdb')
    if apply:
        await db.commit()
    return {'cursor':rows[-1].id if rows else cursor,'examined':len(rows),'updated':updated,'has_more':len(rows)==limit}


async def main():
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    cursor=0;examined=updated=0
    async with AsyncSessionLocal() as db:
        while True:
            result=await backfill(db,cursor=cursor,apply=args.apply)
            cursor=result['cursor'];examined+=result['examined'];updated+=result['updated']
            if not args.apply:
                await db.rollback()
            if not result['has_more']:
                break
            await asyncio.sleep(.05)
    print(json.dumps({'applied':args.apply,'snapshots':examined,'portraits':updated}))


if __name__=='__main__':
    asyncio.run(main())
