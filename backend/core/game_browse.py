"""Ranked public game discovery pages with separately projected viewer state."""
import base64
import copy
import hashlib
import hmac
import json
import re
import time
from datetime import date, timedelta

from sqlalchemy import and_, literal, select

from core.config import settings
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.games import GameChildState, GameEntry

CATEGORIES = {'trending':'Trending','most_played':'Most Played','sellers':'Top sellers',
    'newest':'Newest','popular':'All-time popular','score':'Highest rated'}
PAGE_AGE = timedelta(hours=2)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def encode_cursor(scope, key, expires):
    body = base64.urlsafe_b64encode(json.dumps([scope,key,expires],separators=(',',':')).encode()).decode().rstrip('=')
    signature = hmac.new(settings.secret_key.encode(),body.encode(),hashlib.sha256).hexdigest()
    return body+'.'+signature


def decode_cursor(value, scope):
    try:
        if len(value)>512:
            raise ValueError()
        body,signature = value.split('.')
        expected = hmac.new(settings.secret_key.encode(),body.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise ValueError()
        actual,key,expires = json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if actual!=scope or not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key):
            raise ValueError()
        if isinstance(expires,bool) or not isinstance(expires,int) or expires>time.time()+7500:
            raise ValueError()
        if expires<=time.time():
            raise ProviderError('cursor_expired')
        return key,expires
    except (ValueError,TypeError,UnicodeError):
        raise ProviderError('invalid_cursor') from None


async def filters(sources, *, category, genres='', platforms='', start=None, end=None, q='', min_metacritic=0,timeline=False):
    if category not in CATEGORIES or not 0<=min_metacritic<=100 or min_metacritic and category!='trending':
        raise ProviderError('invalid_request')
    if timeline and category!='newest':
        raise ProviderError('invalid_request')
    try:
        for value in (start,end):
            if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value) or not 1900<=date.fromisoformat(value).year<=2200):
                raise ValueError()
        if start and end and start>end:
            raise ValueError()
    except ValueError:
        raise ProviderError('invalid_request') from None
    if len(genres)>1000 or len(platforms)>5000 or len(q)>100:
        raise ProviderError('invalid_request')
    facet = await sources.facets()
    genre_keys = {row['id'] for row in facet['genres']}
    chosen_genres = sorted({value for value in genres.split(',') if value})
    if not set(chosen_genres)<=genre_keys:
        raise ProviderError('invalid_request')
    chosen = {value for value in platforms.split(',') if value}
    groups = {row['id']:row for row in facet['platform_groups']}
    devices = {device['id']:group['id'] for group in groups.values() for device in group['devices']}
    if not chosen<=set(groups)|set(devices):
        raise ProviderError('invalid_request')
    identifiers = set()
    for group in groups.values():
        selected_devices = {value for value in chosen if devices.get(value)==group['id']}
        if group['id']=='pc' and 'pc' in chosen:
            identifiers.update([3,6,14])
        else:
            # Device refinements replace the broad selected console family.
            values = selected_devices or ({device['id'] for device in group['devices']} if group['id'] in chosen else set())
            identifiers.update(int(value.split(':')[1]) for value in values)
    return {'category':category,'genres':chosen_genres,'platforms':sorted(identifiers),
        'start':start,'end':end,'q':q.strip(),'min_metacritic':min_metacritic,'timeline':timeline}


def matches(row, query):
    if query['genres'] and not set(query['genres']).intersection(g['id'] for g in row.get('genres',[])):
        return False
    if query['platforms'] and not {f'igdb.platform:{p}' for p in query['platforms']}.intersection(row.get('platform_ids',[])):
        return False
    released = row.get('release_date')
    return not (query['start'] and (not released or released<query['start']) or query['end'] and (not released or released>query['end']))


class GameBrowse:
    def __init__(self, sources=None):
        self.sources = sources or GameBrowseSources()

    async def frame(self, query, position, snapshot):
        source = self.sources
        category = query['category']
        if query['timeline'] and query['q']:
            return await source.timeline_search(snapshot=snapshot,offset=position[0],q=query['q'],
                genres=[int(value.split(':')[1]) for value in query['genres']],platforms=query['platforms'],start=query['start'],end=query['end'])
        if query['q'] or category in {'newest','popular'}:
            return await source.catalogue_feed(category if category in {'newest','popular'} else 'popular',
                offset=position[0],genres=[int(value.split(':')[1]) for value in query['genres']],
                platforms=query['platforms'],start=query['start'],end=query['end'],q=query['q'],
                **({'search_mode':position[1]} if query['q'] else {}))
        if category=='most_played':
            return await source.most_played()
        if category=='score':
            return await source.highest_rated(page=position[0],start=position[1])
        return await source.attention(sellers=category=='sellers',offset=position[0])

    async def page(self, query, *, limit=24, cursor=None):
        if not 1<=limit<=48:
            raise ProviderError('invalid_request')
        date_scope=query['category']=='newest' and (query['q'] or any(value and value>date.today().isoformat() for value in (query['start'],query['end'])))
        scope = digest(['release-range-v3' if date_scope else 'prefix-v2' if query['q'] else 'v1',query,limit])
        previous = None
        if cursor:
            parent,expires = decode_cursor(cursor,scope)
            previous = await self.sources.cache.peek('igdb','game_browse_pages',parent,max_age=PAGE_AGE)
            if previous.get('projection')!=2:
                raise ProviderError('cursor_expired')
            if previous.get('expires')!=expires or previous.get('scope')!=scope or not previous.get('has_more'):
                raise ProviderError('invalid_cursor')
            key = digest([scope,parent])
        else:
            key = digest([2,scope,int(time.time()//7200)])
        async def load():
            state = copy.deepcopy(previous['continuation']) if previous else dict(
                position=[1,0] if query['category']=='score' and not query['q'] else [0,0],
                buffer=[],seen=[],exhausted=False,partial=False,limited=False,source_at=None,source=None,stale=False,fetched_at=None,window=int(time.time()//7200))
            seen = set(state['seen'])
            rows,frames,examined,scores = [],0,0,{}
            while len(rows)<limit:
                if not state['buffer']:
                    if state['exhausted'] or frames>=2:
                        break
                    result = await self.frame(query,state['position'],state['window'])
                    frames+=1
                    data = result['data']
                    observed = data.get('source_at')
                    if state['source_at'] and observed:
                        # Primitive timestamps are per-record calculations, not
                        # a formal immutable-feed version. Never mistake small
                        # calculation differences for an expired cursor.
                        state['partial'] |= state['source_at']//86400!=observed//86400
                        state['source_at']=min(state['source_at'],observed)
                    else:
                        state['source_at']=observed or state['source_at']
                    state['source']='IGDB daily Steam top sellers' if query['category']=='sellers' and not query['q'] else data['source']
                    state['fetched_at']=result.get('fetched_at')
                    state['partial'] |= bool(data.get('partial'))
                    state['limited'] |= bool(data.get('coverage_limited'))
                    state['stale'] |= bool(result.get('stale'))
                    state['buffer']=[row for row in data['rows'] if matches(row,query)]
                    state['exhausted']=not bool(data.get('has_more',False))
                    if query['category']=='score' and not query['q']:
                        state['position']=[data.get('next_page',251),data.get('next_start',0)]
                        beyond = state['position'][0]>250
                    else:
                        state['position']=[data.get('next_offset',10001),data.get('search_mode',0)]
                        beyond = state['position'][0]>10000
                    if beyond and not state['exhausted']:
                        state['limited']=True;state['exhausted']=True
                    if not state['buffer']:
                        continue
                if query['min_metacritic'] and examined>=48:
                    break
                if query['min_metacritic'] and state['buffer'][0]['igdb_id'] not in scores:
                    batch = state['buffer'][:min(24,48-examined)]
                    known = await self.sources.critic_scores(batch)
                    scores.update({row['igdb_id']:known.get(row['igdb_id']) for row in batch})
                value = state['buffer'].pop(0)
                identifier = value['igdb_id']
                if identifier in seen:
                    continue
                seen.add(identifier);examined+=1
                if query['min_metacritic']:
                    critic = scores[identifier]
                    if critic is None:
                        state['partial']=True
                        continue
                    if critic<query['min_metacritic']:
                        continue
                    value={**value,'metacritic':critic}
                rows.append(value)
            state['seen']=sorted(seen)
            expires = previous['expires'] if previous else int(time.time()+7200)
            return dict(projection=2,scope=scope,expires=expires,rows=rows,continuation=state,
                has_more=bool(state['buffer']) or not state['exhausted'],partial=state['partial'] or state['limited'],
                coverage_limited=state['limited'],stale=state['stale'],source=state['source'],source_at=state['source_at'],fetched_at=state['fetched_at'])
        public = (await self.sources.cache.get('igdb','game_browse_pages',key,load,ttl=PAGE_AGE,max_stale=PAGE_AGE))['data']
        if public['expires']<=time.time():
            raise ProviderError('cursor_expired')
        observed = public.get('source_at')
        if observed and not query['q'] and query['category'] in {'trending','sellers','most_played'}:
            age = 86400 if query['category']=='most_played' else 3*86400
            if not time.time()-age<=observed<=time.time()+300:
                raise ProviderError('unavailable')
        return {name:public[name] for name in ('rows','has_more','partial','coverage_limited','stale','source','source_at','fetched_at')} | {
            'cursor':encode_cursor(scope,key,public['expires']) if public['has_more'] else None}


async def project(db, viewer_id, page):
    rows = page['rows']
    identifiers = [str(row['igdb_id']) for row in rows]
    query = select(CatalogueIdentity.external_id,CatalogueIdentity.entity_id).join(
        CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace=='igdb.game',CatalogueIdentity.external_id.in_(identifiers),CatalogueEntity.kind=='game')
    if viewer_id is not None:
        query = query.add_columns(GameEntry.status,GameEntry.score,GameChildState.status,GameChildState.score).outerjoin(
            GameEntry,and_(GameEntry.work_id==CatalogueEntity.id,GameEntry.user_id==viewer_id)).outerjoin(
            GameChildState,and_(GameChildState.work_id==CatalogueEntity.id,GameChildState.user_id==viewer_id,GameChildState.base_entry_id.is_not(None)))
    else:
        query = query.add_columns(literal(None),literal(None),literal(None),literal(None))
    known = {str(row[0]):row for row in (await db.execute(query)).all()} if identifiers else {}
    cards = []
    for row in rows:
        identity = known.get(str(row['igdb_id']))
        cards.append({key:value for key,value in row.items() if key not in {'identity_evidence','platform_ids'}} | {
            'id':identity[1] if identity else None,'list_status':(identity[2] or identity[4]) if identity else None,
            'score':(identity[3] if identity[2] else identity[5]) if identity else None})
    return {key:value for key,value in page.items() if key!='rows'} | {'results':cards}
