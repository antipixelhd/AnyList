"""Optional, cached edition associations; identities and private tracking stay separate."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core import catalogue
from core.catalogue_normalize import entity
from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from core.game_source_cache import GameSourceCache
from core.game_types import igdb_game_class, mobile_only
from core.game_release_dates import game_release
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship

KINDS={'expanded_games':'expanded_game','remasters':'remaster','remakes':'remake'}
FIELDS='id,name,first_release_date,game_type.type,version_parent,parent_game.id,platforms.id'
QUERY_FIELDS='id,name,first_release_date,'+','.join(f'{field}.{part}' for field in KINDS for part in FIELDS.split(','))


def candidates(raw,stamp=None):
    """An explicit relationship plus two known release dates; unknown is not newer."""
    stamp=stamp or datetime.now(timezone.utc).timestamp()
    released=raw.get('first_release_date')
    if type(released) is not int:return []
    result={}
    for field,kind in KINDS.items():
        for row in raw.get(field) or []:
            if not isinstance(row,dict):continue
            native=row.get('id');day=row.get('first_release_date')
            if (type(native) is not int or native<=0 or native==raw.get('id') or not isinstance(row.get('name'),str) or not row['name'].strip()
                or type(day) is not int or not released<day<=stamp or row.get('version_parent')
                or igdb_game_class(row)!=kind or mobile_only(row)):continue
            parent=row.get('parent_game')
            if parent and (not isinstance(parent,dict) or parent.get('id')!=raw['id']):continue
            result[native]={'native':str(native),'name':row['name'],'year':datetime.fromtimestamp(day,timezone.utc).year,'released_at':day,'kind':kind}
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
            rows=await self.providers.igdb('games',f'fields {QUERY_FIELDS}; where id={int(native)}; limit 1;')
            if len(rows)!=1 or type(rows[0].get('id')) is not int or rows[0]['id']!=int(native):raise ProviderError('bad_response')
            values=candidates(rows[0])
            async with self.sessions() as db:
                await catalogue.write_lock(db)
                original=await db.get(CatalogueEntity,work_id)
                if not original or await native_id(db,original)!=native:raise ProviderError('identity_conflict')
                for value in values:
                    target=await catalogue.upsert_entity(db,entity('game','igdb.game',{'id':int(value['native']),'name':value['name']},
                        attributes={'game_class':value['kind'],'igdb_id':value['native'],**game_release({'first_release_date':value['released_at']})}),'igdb')
                    if target.id==work_id:continue
                    # A version association is not a provider identity or progress target.
                    await catalogue.upsert(db,CatalogueRelationship,{'source_id':target.id,'target_id':work_id,
                        'relation':'version','provider':'igdb','attributes':{'edition_type':value['kind'],'source_active':True}},
                        ['source_id','target_id','relation','provider'])
                await db.commit()
            return {'rows':values}
        cached=await self.cache.get('igdb','game_newer_editions',native,load,ttl=timedelta(days=7),max_stale=timedelta(days=14))
        values=cached['data']['rows']
        if not values:return {'editions':[],'status':{'state':'ready'}}
        async with self.sessions() as db:
            rows=(await db.execute(select(CatalogueEntity.id,CatalogueEntity.name,CatalogueIdentity.external_id).join(CatalogueIdentity,
                CatalogueIdentity.entity_id==CatalogueEntity.id).where(CatalogueIdentity.namespace=='igdb.game',
                CatalogueIdentity.external_id.in_([row['native'] for row in values])))).all()
        known={native:{'id':identifier,'title':title} for identifier,title,native in rows}
        return {'editions':[{**known[value['native']],'year':value['year']}
            for value in values if value['native'] in known and known[value['native']]['id']!=work_id],
            'status':{'state':'ready'}}
