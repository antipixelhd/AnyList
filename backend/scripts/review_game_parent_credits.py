"""Preview and apply evidence-backed edition/port participation links.

This never replaces a game's primary IMDb identity. Apply is an explicit admin
review operation; ordinary discovery keeps ambiguous primary identities apart.
"""
import argparse
import asyncio
import json

from sqlalchemy import func,or_,select
from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_credit_parents import parent_proposal,person_bindings,review_parent,variant_parent
from core.game_imdb_discovery import GamePersonDiscovery,MAP
from core.game_imdb_discovery_source import GameIMDbDiscoverySource
from core.game_imdb_person import queue_projection
from core.game_imdb_review import INDEX
from core.game_imdb_source import IMDbGameSource,identifier
from core.game_title_candidates import retrieve
from core.game_types import CREDIT_CHILDREN,INDEPENDENT,igdb_game_class
from db import AsyncSessionLocal
from models import User
from models.catalogue import CatalogueEntity,MetadataSnapshot


async def resolve_parent(source,proposal):
    """Follow provider-declared parents only, with a fixed depth/cycle bound."""
    native=proposal['parent_igdb_id'];seen={proposal['child_igdb_id']};path=[]
    for _ in range(4):
        if native in seen:raise ProviderError('parent_cycle')
        seen.add(native)
        raw=(await source.games({native})).get(native)
        if not raw:raise ProviderError('missing_parent')
        path.append({'id':native,'name':raw['name'],'version_parent':raw.get('version_parent'),'parent_game':raw.get('parent_game')})
        variant=variant_parent(raw)
        if variant:native=variant['parent_igdb_id'];continue
        kind=igdb_game_class(raw)
        if kind in INDEPENDENT-{'port'}:return raw,path
        parent=raw.get('parent_game')
        parent=parent.get('id') if isinstance(parent,dict) else parent
        if kind not in CREDIT_CHILDREN or type(parent) is not int or parent<=0:raise ProviderError('unverified_parent')
        native=parent
    raise ProviderError('parent_depth')


async def run(args):
    for title_id in args.title:identifier(title_id)
    if args.apply:
        async with AsyncSessionLocal() as db:
            reviewer=await db.get(User,args.reviewer_id) if args.reviewer_id else None
            if not reviewer or not reviewer.is_admin:raise ValueError('--apply requires an administrator --reviewer-id')
    async with AsyncSessionLocal() as db:
        bound=person_bindings(None)
        query=select(MetadataSnapshot.external_id,MetadataSnapshot.payload).where(MetadataSnapshot.provider=='wikidata',
            MetadataSnapshot.kind==MAP,or_(MetadataSnapshot.payload['reason'].astext=='edition_or_port',
                MetadataSnapshot.payload['evidence']['candidates'].contains([{'edition':True}])),
            ~select(bound.c.imdb_id).where(bound.c.imdb_id==MetadataSnapshot.external_id).exists())
        if args.title:query=query.where(MetadataSnapshot.external_id.in_(args.title))
        records=(await db.execute(query.order_by(MetadataSnapshot.external_id).limit(args.limit))).all()
    source=GameIMDbDiscoverySource(AsyncSessionLocal)
    service=GamePersonDiscovery(AsyncSessionLocal)
    for native,saved in records:
        async with AsyncSessionLocal() as db:
            bindings=person_bindings([native])
            if await db.scalar(select(bindings.c.work_id)):continue
        try:
            title=(saved.get('evidence') or {}).get('imdb')
            if not title or title.get('id')!=native:
                header=await IMDbGameSource(source.sources.api.http).title(native)
                title={'id':native,'title':header['titleText']['text'],'year':(header.get('releaseYear') or {}).get('year')}
            explicit=await source.explicit_page([native])
            if explicit.get('stale'):raise ProviderError('stale_mapping')
            outcome=explicit['outcomes'][0]
            if outcome.get('reason')=='explicit_identity_conflict' or outcome['status']=='conflict':
                raise ProviderError('identity_conflict')
            proposal=None
            if outcome.get('reason')=='edition_or_port':
                child=(await source.games({outcome['igdb_id']}))[outcome['igdb_id']]
                proposal=variant_parent(child)
                evidence={'method':'explicit_wikidata_parent','wikidata':outcome.get('evidence')}
            else:
                acquired=await retrieve(source.sources,title)
                if acquired['stale']:raise ProviderError('stale_mapping')
                proposal=parent_proposal(title,acquired['data']['rows'],complete=acquired['data']['complete'])
                if proposal:child=next(row for row in acquired['data']['rows'] if row['id']==proposal['child_igdb_id'])
                evidence=proposal['evidence'] if proposal else {}
            if not proposal:raise ProviderError('unresolved_variant')
            root,path=await resolve_parent(source,proposal)
            evidence={**evidence,'child':{key:child.get(key) for key in ('id','name','game_type','version_parent','parent_game','first_release_date')},'parents':path}
            result={'id':native,'title':title['title'],'kind':proposal['kind'],'child':child['id'],'parent':root['id'],'parent_title':root['name']}
            if args.apply:
                work_id=await service.ingest(normalize_igdb(root))
                async with AsyncSessionLocal() as db:
                    work=await db.get(CatalogueEntity,work_id)
                    if str((work.attributes or {}).get('igdb_id'))!=str(root['id']):raise ProviderError('canonical_parent_conflict')
                    await review_parent(db,native,work_id,title=title['title'],kind=proposal['kind'],
                        source_url='https://www.igdb.com/games/'+child['slug'],reviewer_id=args.reviewer_id,evidence=evidence)
                    people=set(await db.scalars(select(func.split_part(MetadataSnapshot.external_id,':',1)).where(
                        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==INDEX,
                        MetadataSnapshot.payload['titles'].contains([{'id':native}]))))
                    for person in people:await queue_projection(db,person)
                    await db.commit()
                result['applied']=True
            print(json.dumps(result,ensure_ascii=True),flush=True)
        except (ProviderError,KeyError) as error:
            print(json.dumps({'id':native,'status':'unresolved','reason':getattr(error,'code','incomplete_evidence')}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--title',action='append',default=[])
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--reviewer-id',type=int)
    args=parser.parse_args()
    if not 1<=args.limit<=100:parser.error('--limit must be between 1 and 100')
    asyncio.run(run(args))


if __name__=='__main__':main()
