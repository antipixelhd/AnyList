"""Verified or reviewed IMDb bindings on existing IGDB catalogue games."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from core import catalogue
from core.catalogue_providers import ProviderError, ProviderHTTP
from core.game_browse_sources import GameBrowseSources
from core.game_credit_sources import GameCreditSources
from core.game_imdb_mapping import game_mapping
from core.game_imdb_source import IMDbGameSource, identifier, game_header
from core.game_types import capabilities
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueCredit, CatalogueRelationship, MetadataSnapshot

BINDING_REVISION='game_binding_revision'


def verified_bindings(title_ids=None):
    """Explicit bindings whose current IGDB root still agrees with their evidence."""
    igdb=aliased(CatalogueIdentity)
    from core.game_title_match import VERSION
    query=select(CatalogueIdentity.external_id.label('imdb_id'),CatalogueIdentity.entity_id.label('work_id'),
        CatalogueEntity.name.label('game_title'),CatalogueIdentity.evidence['igdb_id'].astext.label('igdb_id')).join(
        CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(CatalogueEntity.kind=='game',
        CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.source.in_(('reviewed','wikidata','matched')),
        func.coalesce(CatalogueIdentity.evidence['mapping_state'].astext,'active')=='active',
        or_(CatalogueIdentity.source!='matched',CatalogueIdentity.evidence['matcher_version'].as_integer()==VERSION),
        CatalogueIdentity.evidence['title_type'].astext=='videoGame',
        or_(CatalogueEntity.attributes['igdb_id'].astext.is_(None),
            CatalogueEntity.attributes['igdb_id'].astext==CatalogueIdentity.evidence['igdb_id'].astext),
        select(igdb.id).where(igdb.entity_id==CatalogueEntity.id,igdb.namespace=='igdb.game',
            igdb.external_id==CatalogueIdentity.evidence['igdb_id'].astext).exists())
    return query.where(CatalogueIdentity.external_id.in_(title_ids)) if title_ids is not None else query


async def revision(db):
    return await db.scalar(select(MetadataSnapshot.coverage['revision'].astext).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==BINDING_REVISION,MetadataSnapshot.external_id=='current'))


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def context(db,work_id,*,participation=False):
    work=await db.get(CatalogueEntity,work_id)
    from core.game_types import CREDIT_CHILDREN,INDEPENDENT
    child = bool(work and participation and (work.attributes or {}).get('game_class') in CREDIT_CHILDREN)
    if child:
        parent=aliased(CatalogueEntity)
        parents=(await db.execute(select(CatalogueRelationship.target_id,parent.kind,parent.attributes['game_class'].astext).join(parent,parent.id==CatalogueRelationship.target_id).where(
            CatalogueRelationship.source_id==work_id,CatalogueRelationship.relation==(work.attributes or {})['game_class'],
            CatalogueRelationship.attributes['source_active'].astext.is_distinct_from('false')))).all()
        child=len(parents)==1 and parents[0][1]=='game' and parents[0][2] in INDEPENDENT-{'port'}
    if not work or work.kind!='game' or not (capabilities(work.attributes).searchable or child):
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
            work,native,binding,items=await context(db,work_id,participation=True)
            if binding:
                # A reviewed selection is never remapped by background lookup.
                bound_native=(binding.evidence or {}).get('igdb_id')
                if bound_native is not None and str(bound_native)!=native:
                    if binding.source!='reviewed':
                        from core.game_mapping_lifecycle import quarantine
                        await quarantine(db,binding,'changed_igdb_root');await db.commit()
                    return {'status':'conflict','imdb_id':None}
                from core.game_title_match import VERSION
                pending=binding.source!='reviewed' and ((binding.evidence or {}).get('mapping_state','active')!='active'
                    or binding.source=='matched' and (binding.evidence or {}).get('matcher_version')!=VERSION)
                if not pending:return {'status':'bound','binding':binding_view(binding)}
                retained={'id':binding.external_id,'source':binding.source,'evidence':dict(binding.evidence or {})}
            else:retained=None
            slug=(work.attributes or {}).get('slug') or (work.attributes or {}).get('igdb_slug')
        if retained:return await self.revalidate(work_id,native,retained)
        from core.game_mapping_index import wikidata_generation
        wiki_generation=await wikidata_generation(self.sessions)
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
        if cached.get('stale'):return {'status':'unresolved','reason':'mapping_support_stale'}
        mapped={**mapped,'_support':{'check_wikidata_generation':True,'wikidata_generation':wiki_generation,
            'wiki_snapshots':[{'kind':'game_imdb_mapping','key':key,'fetched_at':cached['fetched_at']}] if cached.get('fetched_at') else []}}
        if mapped['status']!='verified':return await self.reverse_lookup(work_id,native,mapped)
        title_id=mapped['imdb_id']
        header=await self.cache.get('imdb','game_imdb_identity',title_id,
            lambda:self.source.title(title_id),ttl=timedelta(days=30),max_stale=timedelta(days=90))
        game_header(header['data'],title_id)
        return await self.save(work_id,title_id,'wikidata',{
            'title_type':'videoGame','igdb_id':native,'wikidata':mapped['evidence'],
            'mapping_fetched_at':cached['fetched_at'],'identity_fetched_at':header['fetched_at'],
            'imdb_identity':header['data']},expected_igdb=native)

    async def reverse_lookup(self,work_id,native,previous):
        from core.game_mapping_index import lookup,wikidata_generation
        from core.game_title_candidates import FIELDS,retrieve
        from core.game_title_match import decide
        from models.game_mapping import GameMappingGeneration
        if previous.get('reason')=='explicit_identity_conflict':return previous
        wiki_support=previous.get('_support') or {'check_wikidata_generation':True,
            'wikidata_generation':await wikidata_generation(self.sessions),'wiki_snapshots':[]}
        async with self.sessions() as db:
            generation=await db.scalar(select(GameMappingGeneration.id).where(GameMappingGeneration.source=='imdb',
                GameMappingGeneration.active.is_(True),GameMappingGeneration.observed_at>=now()-timedelta(days=2)))
        if not generation:return previous
        rows=await self.wiki.sources.igdb('games',f'fields {FIELDS}; where id={int(native)}; limit 1;')
        if len(rows)!=1 or str(rows[0].get('id'))!=native:raise ProviderError('identity_conflict')
        raw=rows[0];names=[raw['name'],*[row['name'] for row in raw.get('alternative_names',[]) if isinstance(row.get('name'),str)]]
        titles=await lookup(self.sessions,'imdb',names=names)
        if len(titles)>=101:return {'status':'unresolved','reason':'incomplete_candidates'}
        accepted=[];possible=[]
        uncertain=False
        for title in titles:
            decision=decide(title['value'],[raw],complete=True,branches=[{'kind':'local_imdb_index','generation':generation,'complete':True}])
            if decision['status']=='verified':accepted.append((title,decision))
            elif decision['evidence']['candidates']:
                possible.append((title,decision))
                if title['value'].get('year') is None or not decision['evidence']['candidates'][0]['years']:uncertain=True
        if not accepted and len(possible)==1:accepted=possible;uncertain=False
        if len(accepted)!=1 or uncertain:return {'status':'unresolved' if accepted or uncertain else 'unmapped','reason':'local_title_candidates'}
        title,decision=accepted[0];title_id=title['native']
        from core.wikidata_claims import snak_value
        for mapping in previous.get('mappings',[]):
            claims={snak_value(row.get('mainsnak')) for row in mapping.get('evidence',{}).get('P345',[])}-{None}
            if claims and claims!={title_id}:return previous
        current=await self.source.title(title_id)
        current_title={**title['value'],'id':title_id,'title':current['titleText']['text'],'year':(current.get('releaseYear') or {}).get('year')}
        candidate=await retrieve(self.wiki.sources,current_title)
        proof=decide(current_title,candidate['data']['rows'],complete=candidate['data']['complete'] and not candidate['stale'],branches=candidate['data']['branches'])
        deeper=[]
        if proof['status']=='unresolved' and candidate['data']['complete'] and not candidate['stale']:
            from core.game_match_support import corroborate
            extra,deeper=await corroborate(self.wiki.sources,current_title,candidate['data']['rows'])
            proof=decide(current_title,candidate['data']['rows'],complete=True,branches=candidate['data']['branches'],corroboration=extra)
        if proof['status']!='verified' or str(proof['igdb_id'])!=native:
            return {'status':'unresolved','reason':'changed_imdb_identity'}
        if previous.get('status')=='conflict':
            from core.wikidata_claims import qualified
            scope={int(native),*[row['id'] for row in proof['evidence'].get('rename_scope',[])]}
            slugs={row.get('slug') for row in candidate['data']['rows'] if row['id'] in scope}-{None}
            mappings=previous.get('mappings',[])
            if not mappings:return previous
            for mapping in mappings:
                evidence=mapping.get('evidence',{})
                ids=[snak_value(row.get('mainsnak')) for row in evidence.get('P9043',[])]
                ids.extend(value for row in evidence.get('P5794',[]) for value in qualified(row,'P9043'))
                names=[snak_value(row.get('mainsnak')) for row in evidence.get('P5794',[])]
                if not ids and not names:return previous
                if any(not isinstance(value,str) or not value.isdigit() or int(value) not in scope for value in ids):return previous
                if not ids and any(value not in slugs for value in names):return previous
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource,project_game
        from core.game_types import igdb_game_class,CREDIT_CHILDREN
        games={row['id']:row for row in candidate['data']['rows']}
        parent=raw.get('parent_game')
        if igdb_game_class(raw) in CREDIT_CHILDREN and isinstance(parent,dict) and parent.get('id') not in games:
            games.update(await GameIMDbDiscoverySource(self.sessions,sources=self.wiki.sources,wiki=self.wiki).games({parent['id']}))
        projected=project_game(title_id,raw,games,proof['evidence'])
        if projected['status']!='verified':return projected
        proof['evidence'].update(direction='igdb_to_imdb',support={**wiki_support,'imdb_generation':generation,
            'candidate_key':candidate['data'].get('scope_key'),'candidate_fingerprint':candidate['data'].get('scope_fingerprint'),'snapshots':deeper},
            dependencies=[f'wikidata-imdb:{title_id}',
            f'igdb-query:{candidate["data"].get("scope_key","")}',
            *[f'igdb-name:{name}' for name in candidate['data'].get('scope_names',[])],
            *[f'support:{row["provider"]}:{row["kind"]}:{row["key"]}' for row in deeper],
            *[f'wikidata:{mapping["evidence"]["wikidata_id"]}' for mapping in previous.get('mappings',[])
                if mapping.get('evidence',{}).get('wikidata_id')]])
        return await self.save(work_id,title_id,'matched',{'title_type':'videoGame','igdb_id':native,
            'matching':proof['evidence'],'imdb_identity':current},expected_igdb=native,participation=True)

    async def revalidate(self,work_id,native,previous):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        header=await self.source.title(previous['id'])
        title={'id':previous['id'],'title':header['titleText']['text'],'year':(header.get('releaseYear') or {}).get('year')}
        result=await GameIMDbDiscoverySource(self.sessions,sources=self.wiki.sources,wiki=self.wiki).page([previous['id']],titles={previous['id']:title})
        outcome=result['outcomes'][0]
        if not result.get('stale') and outcome['status']=='verified' and str(outcome['igdb_id'])==native:
            matched=outcome.get('binding_source')=='matched'
            if matched and (previous.get('evidence',{}).get('matching') or {}).get('direction')=='igdb_to_imdb':
                return await self.reverse_lookup(work_id,native,{'status':'unmapped'})
            return await self.save(work_id,previous['id'],'matched' if matched else 'wikidata',{
                'title_type':'videoGame','igdb_id':native,'matching' if matched else 'wikidata':outcome['evidence'],
                'imdb_identity':header},expected_igdb=native,participation=True)
        if not result.get('stale') and outcome['status'] in ('verified','conflict','excluded'):
            async with self.sessions() as db:
                await catalogue.write_lock(db)
                _,current,binding,_=await context(db,work_id,participation=True)
                if current==native and binding and binding.external_id==previous['id'] and binding.source!='reviewed':
                    from core.game_mapping_lifecycle import quarantine
                    await quarantine(db,binding,'incompatible_current_evidence');await db.commit()
            return {'status':'conflict','imdb_id':None}
        return {'status':'unresolved','reason':'mapping_support_stale'}

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

    async def save(self,work_id,title_id,source,evidence,*,expected_igdb,participation=False):
        identifier(title_id)
        from core.game_title_match import VERSION
        dependencies=[f'imdb:{title_id}',f'igdb:{expected_igdb}']
        if item:=(evidence.get('wikidata') or {}).get('wikidata_id'):dependencies.append(f'wikidata:{item}')
        proof=evidence.get('matching') or {}
        from core.game_title_match import spelling
        for name in [(proof.get('imdb') or {}).get('title'),*(proof.get('imdb') or {}).get('aliases',[])]:
            if spelling(name):dependencies.extend([f'imdb-name:{spelling(name)}',f'igdb-name:{spelling(name)}'])
        dependencies.extend(proof.get('dependencies',[]))
        evidence={**evidence,'mapping_state':'active','matcher_version':VERSION,'evidence_revision':uuid.uuid4().hex,
            'dependencies':sorted(set([*dependencies,*evidence.get('dependencies',[])]))}
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            if source=='matched':
                support=proof.get('support') or {}
                if support.get('check_wikidata_generation'):
                    from models.game_mapping import GameMappingGeneration
                    current=await db.scalar(select(GameMappingGeneration.id).where(GameMappingGeneration.source=='wikidata',
                        GameMappingGeneration.active.is_(True)))
                    if current!=support.get('wikidata_generation'):raise ProviderError('stale_mapping')
                from core.game_source_cache import GameSourceCache
                for snapshot in support.get('snapshots',[]):
                    row=(await db.execute(GameSourceCache.query(snapshot['provider'],snapshot['kind'],snapshot['key'])
                        .with_only_columns(MetadataSnapshot.payload['fingerprint'].astext,MetadataSnapshot.expires_at)
                        .with_for_update(read=True))).one_or_none()
                    if not row or row[0]!=snapshot['fingerprint'] or not row[1] or row[1]<=now():raise ProviderError('stale_mapping')
                for snapshot in support.get('wiki_snapshots',[]):
                    fetched=await db.scalar(GameSourceCache.query('wikidata',snapshot['kind'],snapshot['key'])
                        .with_only_columns(MetadataSnapshot.fetched_at).with_for_update(read=True))
                    if not fetched or fetched.isoformat()!=snapshot['fetched_at']:raise ProviderError('stale_mapping')
                if generation:=support.get('imdb_generation'):
                    from models.game_mapping import GameMappingGeneration
                    if not await db.scalar(select(GameMappingGeneration.id).where(GameMappingGeneration.id==generation,
                        GameMappingGeneration.active.is_(True),GameMappingGeneration.observed_at>=now()-timedelta(days=2))):
                        raise ProviderError('stale_mapping')
                if candidate_key:=support.get('candidate_key'):
                    from core.game_source_cache import GameSourceCache
                    fingerprint=await db.scalar(GameSourceCache.query('igdb','game_title_candidates',candidate_key)
                        .with_only_columns(MetadataSnapshot.payload['scope_fingerprint'].astext))
                    if fingerprint!=support.get('candidate_fingerprint'):raise ProviderError('stale_mapping')
            _,native,old,_=await context(db,work_id,participation=participation)
            if native!=expected_igdb:
                raise catalogue.IdentityConflict('Game identity changed during verification')
            if old and old.external_id!=title_id and source!='reviewed':
                raise catalogue.IdentityConflict('IMDb mapping conflict requires review')
            if old and old.source=='reviewed' and source!='reviewed':
                return {'status':'bound','binding':binding_view(old)}
            changed=source=='reviewed' and (not old or old.external_id!=title_id)
            mapping_changed=not old or old.external_id!=title_id or (old.evidence or {}).get('igdb_id')!=evidence.get('igdb_id') or (old.evidence or {}).get('mapping_state','active')!='active'
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
                elif old.source!='reviewed' and source in ('matched','wikidata'):
                    # Refresh only automatic evidence. The reviewed guard above
                    # and this independent check both preserve pinned decisions.
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
            if mapping_changed:
                stamp=now();version={'revision':uuid.uuid4().hex}
                await db.execute(insert(MetadataSnapshot).values(provider='imdb',kind=BINDING_REVISION,external_id='current',
                    coverage=version,fetched_at=stamp).on_conflict_do_update(constraint='uq_catalogue_snapshot',
                        set_={'coverage':version,'fetched_at':stamp}))
            await db.commit()
            return {'status':'bound','binding':binding_view(binding)}
