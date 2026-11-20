"""Child participation that preserves the main game's primary identity."""
import uuid
from sqlalchemy import select,literal,String,func,or_,update
from sqlalchemy.dialects.postgresql import insert
from core.game_types import CREDIT_CHILDREN,INDEPENDENT
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit,MetadataSnapshot

KIND='game_credit_parent'
PARENT_CREDIT_KINDS=CREDIT_CHILDREN|{'edition','port'}
VERSION=1


def automatic_parent_valid(proof):
    """An edition/port needs unique complete evidence and an independent parent."""
    from core.game_title_match import SUPPORTED_VERSIONS,years
    from core.game_types import igdb_game_class
    variant=proof.get('variant') or {};title=proof.get('imdb') or {}
    child=variant.get('child') or {};parent=variant.get('parent') or {}
    if (variant.get('version')!=VERSION or proof.get('version') not in SUPPORTED_VERSIONS
            or proof.get('retrieval_complete') is not True or type(title.get('year')) is not int
            or igdb_game_class(child) not in INDEPENDENT|{'bundle'}
            or igdb_game_class(parent) not in INDEPENDENT-{'port'} or parent.get('version_parent')
            or not years(parent) or min(years(parent))>title['year']):return False
    proposal=parent_proposal(title,[child],complete=True)
    plausible=[row for row in proof.get('candidates',[]) if row.get('year_match') or not row.get('years')]
    return bool(proposal and proposal['parent_igdb_id']==parent.get('id')
        and len(plausible)==1 and plausible[0]==proposal['evidence']['candidate'])


async def save_automatic_parent(db,title_id,parent_id,proof):
    """Save participation only; never give the base game the edition's IMDb ID."""
    from core import catalogue
    from core.game_imdb_bindings import validate_support
    from core.game_imdb_enrichment import now
    from core.game_mapping_lifecycle import revision
    from core.game_imdb_source import identifier
    identifier(title_id)
    if not automatic_parent_valid(proof) or proof['imdb']['id']!=title_id:
        raise catalogue.IdentityConflict('Complete edition/port identity evidence is required')
    await catalogue.write_lock(db)
    await validate_support(db,proof)
    parent=proof['variant']['parent'];child=proof['variant']['child']
    work=await db.get(CatalogueEntity,parent_id)
    identity=await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==parent_id,
        CatalogueIdentity.namespace=='igdb.game',CatalogueIdentity.external_id==str(parent['id'])))
    if (not work or work.kind!='game' or not identity or str((work.attributes or {}).get('igdb_id'))!=str(parent['id'])
            or (work.attributes or {}).get('game_class') not in INDEPENDENT-{'port'}):
        raise catalogue.IdentityConflict('The independent parent identity changed')
    existing=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==KIND,MetadataSnapshot.external_id==title_id).with_for_update())
    if (existing and not (existing.coverage or {}).get('automatic') or await db.scalar(
            select(CatalogueIdentity.id).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id==title_id))
            or await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.provider=='imdb',
                MetadataSnapshot.kind=='game_credit_alias',MetadataSnapshot.external_id==title_id))):
        raise catalogue.IdentityConflict('An existing identity or reviewed participation requires review')
    kind=variant_parent(child)['kind']
    if existing and existing.entity_id!=parent_id:
        db.add(MetadataSnapshot(provider='imdb',kind='game_credit_parent_audit',external_id=uuid.uuid4().hex,
            entity_id=parent_id,payload={'previous_work_id':existing.entity_id,'previous':existing.payload,'selection':proof},fetched_at=now()))
        await db.execute(update(CatalogueCredit).where(CatalogueCredit.provider=='imdb',
            CatalogueCredit.attributes['imdb_title_id'].astext==title_id).values(
                attributes=CatalogueCredit.attributes.op('||')({'source_active':False})))
    statement=insert(MetadataSnapshot).values(provider='imdb',kind=KIND,external_id=title_id,entity_id=parent_id,
        payload={'title':proof['imdb']['title'],'kind':kind,'evidence':proof,
            'dependencies':proof.get('dependencies',[])},
        coverage={'automatic':True,'policy_version':VERSION,'mapping_state':'active'},fetched_at=now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'entity_id':statement.excluded.entity_id,'payload':statement.excluded.payload,
        'coverage':statement.excluded.coverage,'fetched_at':statement.excluded.fetched_at}))
    await revision(db)


async def reject_automatic_parents(db,title_ids):
    """Confirmed conflicts/exclusions retire automatic, never reviewed, links."""
    if not title_ids:return
    from core.game_mapping_lifecycle import revision
    changed=list(await db.scalars(update(MetadataSnapshot).where(MetadataSnapshot.provider=='imdb',
        MetadataSnapshot.kind==KIND,MetadataSnapshot.external_id.in_(title_ids),
        MetadataSnapshot.coverage['automatic'].astext=='true',
        MetadataSnapshot.coverage['mapping_state'].astext!='quarantined').values(
            coverage=MetadataSnapshot.coverage.op('||')({'mapping_state':'quarantined'})).returning(MetadataSnapshot.external_id)))
    if changed:
        await db.execute(update(CatalogueCredit).where(CatalogueCredit.provider=='imdb',CatalogueCredit.scope=='person',
            CatalogueCredit.attributes['imdb_title_id'].astext.in_(changed),
            CatalogueCredit.attributes['credited_child']['kind'].astext.is_not(None)).values(
                attributes=CatalogueCredit.attributes.op('||')({'source_active':False})))
        await revision(db)


def variant_parent(raw):
    from core.game_types import igdb_game_class
    version=raw.get('version_parent')
    kind='edition' if version else 'port' if igdb_game_class(raw)=='port' else None
    if not kind or igdb_game_class(raw)=='unknown':return None
    parent=version if version else raw.get('parent_game')
    parent=parent.get('id') if isinstance(parent,dict) else parent
    if type(parent) is not int or not 0<parent<=2147483647 or parent==raw['id']:return None
    return {'child_igdb_id':raw['id'],'parent_igdb_id':parent,'kind':kind}


def parent_proposal(title,candidates,*,complete):
    """A precise variant can establish participation without a primary binding."""
    from core.game_title_match import decide
    if type(title.get('year')) is not int or not complete:return None
    decision=decide(title,candidates,complete=True,branches=[])
    rows=decision['evidence']['candidates']
    plausible=[row for row in rows if row['year_match'] or not row['years']]
    # Missing years and same-year namesakes still require independent evidence.
    if len(plausible)!=1 or not plausible[0]['year_match']:return None
    raw=next(row for row in candidates if row['id']==plausible[0]['id'])
    result=variant_parent(raw)
    if not result:return None
    return {**result,
        'evidence':{'method':'unique_title_year_parent','imdb':title,'candidate':plausible[0]}}


def person_bindings(ids):
    from core.game_imdb_bindings import verified_bindings
    from core.game_title_match import SUPPORTED_VERSIONS
    verified=verified_bindings(ids).subquery()
    direct=select(verified.c.imdb_id,verified.c.work_id,verified.c.game_title,verified.c.igdb_id,
        literal(None,String).label('credit_kind'),literal(None,String).label('credit_title'))
    igdb=select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id==CatalogueEntity.id,
        CatalogueIdentity.namespace=='igdb.game').order_by(CatalogueIdentity.id).limit(1).correlate(CatalogueEntity).scalar_subquery()
    reviewed=select(MetadataSnapshot.external_id.label('imdb_id'),MetadataSnapshot.entity_id.label('work_id'),
        CatalogueEntity.name.label('game_title'),func.coalesce(CatalogueEntity.attributes['igdb_id'].astext,igdb).label('igdb_id'),
        MetadataSnapshot.payload['kind'].astext.label('credit_kind'),MetadataSnapshot.payload['title'].astext.label('credit_title')).join(
        CatalogueEntity,CatalogueEntity.id==MetadataSnapshot.entity_id).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==KIND,
        or_(MetadataSnapshot.coverage['reviewed'].astext=='true',
            (MetadataSnapshot.coverage['automatic'].astext=='true') &
            (MetadataSnapshot.coverage['policy_version'].as_integer()==VERSION) &
            MetadataSnapshot.payload['evidence']['version'].as_integer().in_(SUPPORTED_VERSIONS) &
            (MetadataSnapshot.coverage['mapping_state'].astext=='active') &
            (MetadataSnapshot.payload['evidence']['variant']['parent']['id'].astext==func.coalesce(CatalogueEntity.attributes['igdb_id'].astext,igdb)) &
            ~select(CatalogueIdentity.id).where(CatalogueIdentity.namespace=='imdb.title',
                CatalogueIdentity.external_id==MetadataSnapshot.external_id).exists()),
        MetadataSnapshot.payload['kind'].astext.in_(PARENT_CREDIT_KINDS),
        CatalogueEntity.kind=='game',CatalogueEntity.attributes['game_class'].astext.in_(INDEPENDENT-{'port'}),
        select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==CatalogueEntity.id,
            CatalogueIdentity.namespace=='igdb.game').exists(),
        ~select(verified.c.imdb_id).where(verified.c.imdb_id==MetadataSnapshot.external_id).exists())
    if ids is not None:reviewed=reviewed.where(MetadataSnapshot.external_id.in_(ids))
    from core.game_credit_aliases import alias_bindings
    aliases=alias_bindings(ids)
    return direct.union_all(reviewed,aliases).cte('person_game_bindings')


async def review_parent(db,title_id,parent_id,*,title,kind,source_url,reviewer_id,evidence=None):
    from urllib.parse import urlsplit
    from core import catalogue
    from core.game_imdb_source import identifier
    from core.game_imdb_enrichment import now
    from core.game_mapping_lifecycle import revision
    from models import User
    identifier(title_id)
    url=urlsplit(source_url)
    reviewer=await db.get(User,reviewer_id)
    if kind not in PARENT_CREDIT_KINDS or not title.strip() or url.scheme!='https' or not url.hostname or not reviewer or not reviewer.is_admin:
        raise ValueError('A reviewed child title, public evidence URL and administrator are required')
    await catalogue.write_lock(db)
    parent=await db.get(CatalogueEntity,parent_id)
    if not parent or parent.kind!='game' or (parent.attributes or {}).get('game_class') not in INDEPENDENT-{'port'}:
        raise ValueError('An independent parent game is required')
    if not await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==parent_id,CatalogueIdentity.namespace=='igdb.game')):
        raise ValueError('The parent must have an IGDB identity')
    statement=insert(MetadataSnapshot).values(provider='imdb',kind=KIND,external_id=title_id,entity_id=parent_id,
        payload={'title':title.strip(),'kind':kind,'source_url':source_url,'reviewer_id':reviewer_id,
            **({'evidence':evidence} if evidence else {})},
        coverage={'reviewed':True},fetched_at=now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'entity_id':statement.excluded.entity_id,'payload':statement.excluded.payload,'coverage':statement.excluded.coverage,
        'fetched_at':statement.excluded.fetched_at}))
    await revision(db)
