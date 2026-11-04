"""IGDB company participation, retrieved by ID in bounded batches."""
import asyncio
from contextvars import ContextVar
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources,positive
from core.game_types import capabilities,igdb_game_class,mobile_only,INDEPENDENT
from core.catalogue_normalize import normalize_igdb
from core.game_release_dates import FIELDS as RELEASE_FIELDS

SIZE=25
_game_rows=ContextVar('company_acquisition_games',default=None)
ROLES=('developer','publisher','supporting','porting')
FIELDS=('id,slug,name,first_release_date,game_type.type,version_parent,parent_game.name,parent_game.game_type.type,'
    'cover.url,genres.name,platforms.name,platforms.platform_family.name,ports.game_type.type,'
    'ports.platforms.name,ports.platforms.platform_family.name,external_games.uid,external_games.url,'
    'external_games.external_game_source.name,involved_companies.developer,involved_companies.company.country,'+RELEASE_FIELDS)


def documents(rows,games):
    result=[];omitted=0
    for record in rows:
        raw=games[record['game']]
        if mobile_only(raw):
            continue
        work=raw;seen=set();ports=[]
        for _ in range(4):
            if work['id'] in seen:work=None;break
            seen.add(work['id'])
            if igdb_game_class(work)=='port':
                ports.append({key:work[key] for key in ('id','game_type','platforms') if key in work})
            parent=work.get('version_parent')
            if parent is None and igdb_game_class(work)=='port':parent=work.get('parent_game')
            if parent is None:break
            native=parent.get('id') if isinstance(parent,dict) else parent
            work=games.get(native)
            if not work:break
        else:work=None
        if not work or igdb_game_class(work) in ('unknown','port') or work.get('version_parent'):
            omitted+=1;continue
        if ports:work={**work,'ports':[*work.get('ports',[]),*ports]}
        if mobile_only(work) or not capabilities({'game_class':igdb_game_class(work)}).searchable:continue
        # Expansion pages require an explicit, independently playable base.
        if igdb_game_class(work)=='expansion':
            parent=work.get('parent_game')
            parent=games.get(parent.get('id'),parent) if isinstance(parent,dict) else None
            if not isinstance(parent,dict) or not capabilities({'game_class':igdb_game_class(parent)}).independent:
                omitted+=1;continue
        roles=[role for role in ROLES if record.get(role) is True]
        if not roles:
            if any(type(record.get(role)) is not bool for role in ROLES):omitted+=1
            continue
        doc=normalize_igdb(work)
        if igdb_game_class(work)=='expansion':
            for edge in doc['relationships']:
                if edge['relation']=='expansion' and not edge.get('reverse'):
                    edge['target']=normalize_igdb(parent)['work']
        doc['credits']=[];doc['characters']=[]
        # This connection proves company participation, not complete title
        # cast/characters or a fresh full game detail component.
        doc['coverage']={'company_connection':True}
        result.append({'doc':doc,'record':record,'roles':roles})
    return result,omitted


class GameCompanySource:
    def __init__(self,sessions):self.api=GameBrowseSources(sessions)

    async def page(self,native,after=0):
        if isinstance(after,dict):return await self.primary_page(native,after)
        if not positive(int(native)) or not isinstance(after,int) or after<0:raise ProviderError('invalid_request')
        rows=await self.api.igdb('involved_companies',
            f'fields id,company,game,developer,publisher,supporting,porting; where company={int(native)} & id>{after}; sort id asc; limit {SIZE};')
        if not isinstance(rows,list) or len(rows)>SIZE:raise ProviderError('bad_response')
        previous=after
        for row in rows:
            if not isinstance(row,dict) or not positive(row.get('id')) or row['id']<=previous or row.get('company')!=int(native) or not positive(row.get('game')):
                raise ProviderError('bad_response')
            if any(role in row and type(row[role]) is not bool for role in ROLES):raise ProviderError('bad_response')
            previous=row['id']
        return await self.acquire(rows,previous,len(rows)<SIZE)

    async def primary_page(self,native,cursor):
        """Scan compact participation first; preserve variant-only credits for the tail."""
        after=cursor.get('after');phase=cursor.get('phase');deferred=cursor.get('deferred')
        if (not positive(int(native)) or type(after) is not int or after<0 or phase not in ('primary','variants')
                or not isinstance(deferred,list)):raise ProviderError('invalid_request')
        if phase=='variants':
            selected=deferred[:SIZE];remaining=deferred[SIZE:]
            return await self.acquire(selected,{'phase':phase,'after':after,'deferred':remaining},not remaining,received=0)
        rows=await self.api.igdb('involved_companies',
            f'fields id,company,game.id,game.game_type.type,game.version_parent,developer,publisher,supporting,porting; where company={int(native)} & id>{after}; sort id asc; limit {SIZE};')
        if not isinstance(rows,list) or len(rows)>SIZE:raise ProviderError('bad_response')
        selected=[];pending=list(deferred);previous=after
        for row in rows:
            if (not isinstance(row,dict) or not positive(row.get('id')) or row['id']<=previous
                    or row.get('company')!=int(native) or not isinstance(row.get('game'),dict)
                    or not positive(row['game'].get('id'))):raise ProviderError('bad_response')
            if any(role in row and type(row[role]) is not bool for role in ROLES):raise ProviderError('bad_response')
            previous=row['id'];game=row['game'];kind=igdb_game_class(game)
            record={**row,'game':game['id']}
            if game.get('version_parent') or kind=='port':pending.append(record)
            elif kind=='unknown' or kind in INDEPENDENT|{'expansion'}:selected.append(record)
            # Verified non-display DLC/update/bundle categories need no full
            # metadata or parent fan-out for a company works projection.
        scanned=len(rows)<SIZE
        next_cursor={'phase':'variants' if scanned else 'primary','after':previous,'deferred':pending}
        return await self.acquire(selected,next_cursor,scanned and not pending,received=len(rows))

    async def acquire(self,rows,cursor,complete,*,received=None):
        cached=_game_rows.get()
        games=dict(cached or {});needed={row['game'] for row in rows}
        for _ in range(4):
            missing=needed-set(games)
            if not missing:break
            fetched=await self.api.igdb('games',f'fields {FIELDS}; where id=({",".join(map(str,sorted(missing)))}); limit 100;')
            if not isinstance(fetched,list) or len(fetched)!=len(missing) or {row.get('id') for row in fetched if isinstance(row,dict)}!=missing:
                raise ProviderError('bad_response')
            games.update({row['id']:row for row in fetched})
            if cached is not None:cached.update({row['id']:row for row in fetched})
            needed=set()
            for row in fetched:
                if not isinstance(row.get('name'),str) or not row['name'].strip():raise ProviderError('bad_response')
                parent=row.get('version_parent')
                if parent is None and igdb_game_class(row) in ('port','expansion'):parent=row.get('parent_game')
                native_parent=parent.get('id') if isinstance(parent,dict) else parent
                if native_parent is not None:
                    if not positive(native_parent):raise ProviderError('bad_response')
                    needed.add(native_parent)
        docs,omitted=await asyncio.to_thread(documents,rows,games)
        return {'documents':docs,'omitted':omitted,'received':len(rows) if received is None else received,'cursor':cursor,'complete':complete}
