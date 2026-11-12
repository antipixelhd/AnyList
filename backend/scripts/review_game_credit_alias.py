"""Preview or explicitly review duplicate IMDb titles without replacing a game's primary identity."""
import argparse
import asyncio
import json

from sqlalchemy import select,func
from core.game_credit_aliases import review_alias
from core.game_imdb_bindings import verified_bindings
from core.game_imdb_source import IMDbGameSource,identifier
from core.game_imdb_person import queue_projection
from core.game_imdb_review import INDEX
from core.catalogue_providers import ProviderHTTP
from db import AsyncSessionLocal
from models.catalogue import MetadataSnapshot


async def run(args):
    identifier(args.title_id);identifier(args.primary_title_id)
    source=IMDbGameSource(ProviderHTTP(session_factory=AsyncSessionLocal))
    headers={native:await source.title(native) for native in (args.title_id,args.primary_title_id)}
    async with AsyncSessionLocal() as db:
        primary=(await db.execute(verified_bindings([args.primary_title_id]))).one_or_none()
        if not primary or primary.work_id!=args.work_id:raise ValueError('The selected primary identity is not active on this game')
        result={'alias':headers[args.title_id],'primary':headers[args.primary_title_id],
            'work_id':args.work_id,'game_title':primary.game_title,'source_url':args.source_url,'reason':args.reason}
        if args.apply:
            await review_alias(db,args.title_id,args.work_id,primary_title_id=args.primary_title_id,
                title=headers[args.title_id]['titleText']['text'],source_url=args.source_url,
                reviewer_id=args.reviewer_id,reason=args.reason,identities=headers)
            people=set(await db.scalars(select(func.split_part(MetadataSnapshot.external_id,':',1)).where(
                MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==INDEX,
                MetadataSnapshot.payload['titles'].contains([{'id':args.title_id}]))))
            for person in people:await queue_projection(db,person)
            await db.commit();result['applied']=True;result['profiles']=len(people)
        print(json.dumps(result,ensure_ascii=True))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title-id',required=True)
    parser.add_argument('--primary-title-id',required=True)
    parser.add_argument('--work-id',required=True,type=int)
    parser.add_argument('--source-url',required=True)
    parser.add_argument('--reason',required=True)
    parser.add_argument('--reviewer-id',type=int)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    if args.apply and not args.reviewer_id:parser.error('--apply requires an administrator --reviewer-id')
    asyncio.run(run(args))


if __name__=='__main__':main()
