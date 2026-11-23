"""Optional, cached edition associations for game detail pages."""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, tuple_

from core import catalogue
from core.catalogue_normalize import entity, image
from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from core.game_source_cache import GameSourceCache
from core.game_types import INDEPENDENT, igdb_game_class, mobile_only
from core.game_release_dates import game_release
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship, GameRelease

KINDS={'expanded_games':'expanded_game','remasters':'remaster','remakes':'remake'}
FIELDS='id,name,first_release_date,game_type.type,version_parent,parent_game.id,platforms.id,cover.url'
QUERY_FIELDS=FIELDS+',slug,'+','.join(f'{field}.{part}' for field in KINDS for part in FIELDS.split(','))
PAGE_SIZE=500
RELEASE_CLASSES=INDEPENDENT-{'port'}


def candidates(raw,stamp=None,*,versions=()):
    """An explicit relationship plus two known release dates; unknown is not newer."""
    stamp=stamp or datetime.now(timezone.utc).timestamp()
    released=raw.get('first_release_date')
    if type(released) is not int:return []
    groups=[(kind,raw.get(field) or []) for field,kind in KINDS.items()]
    groups.append((None,versions))
    result={}
    for expected,rows in groups:
        for row in rows:
            if not isinstance(row,dict):continue
            native=row.get('id');day=row.get('first_release_date')
            if (type(native) is not int or native<=0 or native==raw.get('id') or not isinstance(row.get('name'),str) or not row['name'].strip()
                or type(day) is not int or not released<day<=stamp or mobile_only(row)):continue
            kind=igdb_game_class(row)
            version_parent=row.get('version_parent')
            if expected is None:
                slug=row.get('slug')
                if (type(version_parent) is not int or version_parent!=raw['id'] or kind not in RELEASE_CLASSES
                    or not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]+',slug)):continue
            elif version_parent or kind!=expected:continue
            parent=row.get('parent_game')
            if parent and (not isinstance(parent,dict) or parent.get('id')!=raw['id']):continue
            value={'native':str(native),'name':row['name'],'year':datetime.fromtimestamp(day,timezone.utc).year,
                'released_at':day,'kind':kind,'poster':image(row.get('cover'))}
            if expected is None:value['igdb_slug']=slug
            result[native]=value
    return sorted(result.values(),key=lambda row:(-row['year'],int(row['native'])))


class GameEditions:
    def __init__(self,sessions,providers=None):
        self.sessions=sessions
        self.providers=providers or CatalogueProviders(ProviderHTTP(session_factory=sessions))
        self.cache=GameSourceCache(sessions)

    async def read(self,work_id):
        from core.game_metadata import native_id
        async with self.sessions() as db:
            work=await db.get(CatalogueEntity,work_id)
            native=await native_id(db,work) if work else None
        if not native:return {'editions':[],'status':{'state':'ready'}}
        async def load():
            original_source=None
            versions=[]
            after=0
            identifier=int(native)
            while True:
                rows=await self.providers.igdb('games',f'fields {QUERY_FIELDS}; '
                    f'where (id={identifier} | version_parent={identifier}) & id>{after}; sort id asc; limit {PAGE_SIZE};')
                for row in rows:
                    identifier_in_row=row.get('id') if isinstance(row,dict) else None
                    if type(identifier_in_row) is not int or identifier_in_row<=after:raise ProviderError('bad_response')
                    after=identifier_in_row
                    if after==identifier:original_source=row
                    else:versions.append(row)
                if len(rows)<PAGE_SIZE:break
            if original_source is None:raise ProviderError('bad_response')
            values=candidates(original_source,versions=versions)
            async with self.sessions() as db:
                await catalogue.write_lock(db)
                original=await db.get(CatalogueEntity,work_id)
                if not original or await native_id(db,original)!=native:raise ProviderError('identity_conflict')
                for value in values:
                    if 'igdb_slug' in value:
                        released=game_release({'first_release_date':value['released_at']})
                        try:
                            await catalogue.upsert_game_release(db,work_id,{
                                'entity':entity('release','igdb.game_version',{'id':int(value['native']),'name':value['name']},
                                    artwork=value['poster'],attributes=released),
                                'platform_namespace':'igdb.version','platform_id':value['native'],
                                'release_date':released.get('release_date'),
                            },'igdb')
                        except catalogue.IdentityConflict as exc:
                            raise ProviderError('identity_conflict') from exc
                        continue
                    target=await catalogue.upsert_entity(db,entity('game','igdb.game',{'id':int(value['native']),'name':value['name']},artwork=value.get('poster'),
                        attributes={'game_class':value['kind'],'igdb_id':value['native'],**game_release({'first_release_date':value['released_at']})}),'igdb')
                    if target.id==work_id:continue
                    # A version association is not a provider identity or progress target.
                    await catalogue.upsert(db,CatalogueRelationship,{'source_id':target.id,'target_id':work_id,
                        'relation':'version','provider':'igdb','attributes':{'edition_type':value['kind'],'source_active':True}},
                        ['source_id','target_id','relation','provider'])
                await db.commit()
            return {'rows':values}
        cached=await self.cache.get('igdb','game_newer_editions_v2',native,load,ttl=timedelta(days=7),max_stale=timedelta(days=14))
        values=cached['data']['rows']
        if not values:return {'editions':[],'status':{'state':'ready'}}
        def identity_key(value):
            return ('igdb.game_version' if 'igdb_slug' in value else 'igdb.game',value['native'])
        async with self.sessions() as db:
            rows=(await db.execute(select(CatalogueEntity.id,CatalogueEntity.name,CatalogueEntity.image_url,
                CatalogueIdentity.namespace,CatalogueIdentity.external_id).join(CatalogueIdentity,
                CatalogueIdentity.entity_id==CatalogueEntity.id).outerjoin(GameRelease,
                GameRelease.entity_id==CatalogueEntity.id).where(
                tuple_(CatalogueIdentity.namespace,CatalogueIdentity.external_id).in_([identity_key(row) for row in values]),
                (CatalogueIdentity.namespace=='igdb.game') | (GameRelease.work_id==work_id)))).all()
        known={(namespace,native):{'id':identifier,'title':title,'poster':poster}
            for identifier,title,poster,namespace,native in rows}
        editions=[]
        for value in values:
            row=known.get(identity_key(value))
            if row is None or row['id']==work_id:continue
            editions.append({**row,'year':value['year'],**({'igdb_slug':value['igdb_slug']} if 'igdb_slug' in value else {})})
        return {'editions':editions,'status':{'state':'ready'}}
