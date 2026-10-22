"""Verified or reviewed IMDb bindings on existing IGDB catalogue games."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from core import catalogue
from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_browse_sources import GameBrowseSources
from core.game_credit_sources import GameCreditSources
from core.game_imdb_mapping import game_mapping
from core.game_imdb_source import IMDbGameSource, identifier, game_header
from core.game_types import capabilities
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueCredit, MetadataSnapshot


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def context(db,work_id):
    work=await db.get(CatalogueEntity,work_id)
    if not work or work.kind!='game' or not capabilities(work.attributes).searchable:
        raise catalogue.IdentityConflict('A searchable IGDB game or expansion is required')
    identities=list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id==work_id)))
    game_ids=[row for row in identities if row.namespace=='igdb.game']
    native={row.external_id for row in game_ids}
    primary=(work.attributes or {}).get('igdb_id')
    if primary is None:
        roots={(row.evidence or {}).get('cross_reference',{}).get('external_id') for row in game_ids
            if (row.evidence or {}).get('cross_reference',{}).get('namespace')=='igdb.game'}
        primary=next(iter(roots)) if len(roots)==1 else None
    if primary is not None and str(primary) in native:
        native={str(primary)}  # Explicit edition aliases retain their IGDB parent root.
    titles=[row for row in identities if row.namespace=='imdb.title']
    if len(native)!=1 or len(titles)>1:
        raise catalogue.IdentityConflict('Conflicting game identities require review')
    return work,next(iter(native)),titles[0] if titles else None,[row.external_id for row in identities if row.namespace=='wikidata.item']


def binding_view(binding):
    return {'id':binding.external_id,'source':binding.source,'reviewed':binding.source=='reviewed',
            'evidence':binding.evidence,'url':f'https://www.imdb.com/title/{binding.external_id}/'}


class GameIMDbBindings:
    def __init__(self,sessions,*,source=None,wiki=None):
        self.sessions=sessions
        self.source=source or IMDbGameSource(ProviderHTTP(session_factory=sessions))
        self.wiki=wiki or GameCreditSources(GameBrowseSources(sessions))
        self.cache=self.wiki.sources.cache

    async def resolve(self,work_id):
        async with self.sessions() as db:
            work,native,binding,items=await context(db,work_id)
            if binding:
                # A reviewed selection is never remapped by background lookup.
                return {'status':'bound','binding':binding_view(binding)}
            slug=(work.attributes or {}).get('slug') or (work.attributes or {}).get('igdb_slug')
        key=hashlib.sha256(json.dumps([native,slug,sorted(items)]).encode()).hexdigest()
        async def lookup():
            candidates=items
            if not candidates:
                bridge=await self.wiki.game_item(int(native),slug)
                candidates=[bridge['data']['item']] if bridge['data']['item'] else []
            if not candidates:return {'status':'unmapped','imdb_id':None}
            fetched=await self.wiki.entities(candidates)
            entities=fetched['data']['entities']
            if set(entities)!=set(candidates):raise ProviderError('bad_response')
            mappings=[game_mapping(raw,native,slug) for raw in entities.values()]
            canonical={raw['id'] for raw in entities.values()}
            titles={row['imdb_id'] for row in mappings if row['status']=='verified'}
            if len(canonical)!=1 or any(row['status']=='conflict' for row in mappings) or len(titles)>1:
                return {'status':'conflict','imdb_id':None,'mappings':mappings}
            if not titles:return {'status':'unmapped','imdb_id':None,'mappings':mappings}
            if any(row['status']!='verified' for row in mappings):
                return {'status':'conflict','imdb_id':None,'mappings':mappings}
            return mappings[0]
        cached=await self.cache.get('wikidata','game_imdb_mapping',key,lookup,
            ttl=timedelta(days=7),max_stale=timedelta(days=30))
        mapped=cached['data']
        if mapped['status']!='verified':return mapped
        title_id=mapped['imdb_id']
        header=await self.cache.get('imdb','game_imdb_identity',title_id,
            lambda:self.source.title(title_id),ttl=timedelta(days=30),max_stale=timedelta(days=90))
        game_header(header['data'],title_id)
        return await self.save(work_id,title_id,'wikidata',{
            'title_type':'videoGame','igdb_id':native,'wikidata':mapped['evidence'],
            'mapping_fetched_at':cached['fetched_at'],'identity_fetched_at':header['fetched_at'],
            'imdb_identity':header['data']},expected_igdb=native)

    async def review(self,work_id,title_id,reviewer_id,reason):
        identifier(title_id)
        if not isinstance(reason,str) or not 3<=len(reason.strip())<=1000:
            raise ProviderError('invalid_request')
        async with self.sessions() as db:
            _,native,_,_=await context(db,work_id)
        header=await self.source.title(title_id)
        game_header(header,title_id)
        return await self.save(work_id,title_id,'reviewed',{
            'title_type':'videoGame','igdb_id':native,'reviewer_id':reviewer_id,'reason':reason.strip(),
            'validated_at':now().isoformat(),'imdb_identity':header},expected_igdb=native)

    async def save(self,work_id,title_id,source,evidence,*,expected_igdb):
        identifier(title_id)
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            _,native,old,_=await context(db,work_id)
            if native!=expected_igdb:
                raise catalogue.IdentityConflict('Game identity changed during verification')
            if old and old.external_id!=title_id and source!='reviewed':
                raise catalogue.IdentityConflict('IMDb mapping conflict requires review')
            if old and old.source=='reviewed' and source!='reviewed':
                return {'status':'bound','binding':binding_view(old)}
            changed=source=='reviewed' and (not old or old.external_id!=title_id)
            if source=='reviewed':
                db.add(MetadataSnapshot(provider='imdb',kind='game_binding_review',external_id=uuid.uuid4().hex,
                    entity_id=work_id,payload={'previous':binding_view(old) if old else None,
                        'selection':title_id,'review':evidence},fetched_at=now()))
            if old and old.external_id!=title_id:
                await db.execute(update(CatalogueCredit).where(CatalogueCredit.work_id==work_id,
                    CatalogueCredit.provider=='imdb').values(
                        attributes=CatalogueCredit.attributes.op('||')({'source_active':False}))
                    .execution_options(synchronize_session=False))
                await db.delete(old)
                await db.flush()
                old=None
            if old:
                if source=='reviewed':
                    old.source=source
                    old.evidence=evidence
                binding=old
            else:
                binding=await catalogue.bind_identity(db,work_id,'imdb.title',title_id,source,evidence)
            if changed:
                await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
                    MetadataSnapshot.kind=='game_credit_job',MetadataSnapshot.entity_id==work_id)
                    .values(payload={},coverage={},expires_at=None,next_attempt_at=now(),lease_token=None,lease_until=None,
                        error_code=None,attempts=0).execution_options(synchronize_session=False))
            await db.commit()
            return {'status':'bound','binding':binding_view(binding)}
