"""Independent, bounded corroboration only for unresolved title candidates."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from html import unescape

from core.catalogue_providers import ProviderError
from core.game_imdb_source import IMDbGameSource
from core.game_store_source import store_request, store_data
from core.game_title_match import products, spelling, years
from core.game_release_dates import game_release
from core.game_types import igdb_game_class

KINDS={'game_match_credits','game_match_store','game_match_news'}


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>|\[[^\]]*\]', ' ', value or '')).split())


def contains(text, value):
    value=spelling(value)
    return bool(value and any(f' {form} ' in f' {spelling(text)} ' for form in (value,value+'s')))


async def cached(sources, provider, kind, key, loader):
    async def load():
        value=await loader()
        value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        return value
    result=await sources.cache.get(provider,kind,key,load,ttl=timedelta(days=7),max_stale=timedelta(days=30))
    if result['stale']:raise ProviderError('stale_mapping')
    return result['data'],{'provider':provider,'kind':kind,'key':key,'fingerprint':result['data']['fingerprint']}


async def corroborate(sources,title,candidates):
    """Return positive evidence; missing coverage is never negative identity proof."""
    names={spelling(title['title']),*[spelling(name) for name in title.get('aliases',[])]}
    selected=[raw for raw in candidates if spelling(raw.get('name')) in names and not raw.get('version_parent')]
    if not selected or len(selected)>4:return {},[]
    evidence={};snapshots=[]
    credits=None
    launch_scope=any(igdb_game_class(raw)=='port'
        and raw.get('release_dates') and all(isinstance(row.get('status'),dict) and row['status'].get('name','').casefold() in ('cancelled','canceled')
            for row in raw['release_dates']) for raw in selected)

    async def load_credits():
        source=IMDbGameSource(sources.api.http)
        rows=[];cursor=None
        for _ in range(3):
            page=await source.credits(title['id'],cursor)
            for row in page['rows']:
                person=row.get('name') or {}
                if (row.get('category') or {}).get('id') not in ('actor','actress','director','writer','composer'):continue
                rows.append({'id':person['id'],'name':person['nameText']['text'],
                    'role':row['category']['id'],'characters':[r['name'] for r in row.get('characters') or [] if isinstance(r.get('name'),str)]})
            if not page['has_next']:break
            cursor=page['cursor']
        point=(page.get('header') or {}).get('releaseDate') or {}
        try:
            if any(type(point.get(key)) is not int for key in ('year','month','day')):raise ValueError()
            day=datetime(point['year'],point['month'],point['day']).date().isoformat()
        except (KeyError,TypeError,ValueError):day=None
        return {'people':rows,'complete':not page['has_next'],'release_date':day}

    async def news(app,end=None):
        key=f'{app}:{end or "latest"}'
        async def load():
            params={'appid':app,'count':20,'maxlength':16000,'feeds':'steam_community_announcements,steam_release'}
            if end:params['enddate']=end
            body=await sources.api.http.request('steam','GET','https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/',params=params)
            data=body.get('appnews') if isinstance(body,dict) else None
            if not isinstance(data,dict) or data.get('appid')!=int(app) or not isinstance(data.get('newsitems'),list) or len(data['newsitems'])>20:
                raise ProviderError('bad_response')
            rows=[]
            for row in data['newsitems']:
                if row.get('feedname') not in ('steam_community_announcements','steam_release'):continue
                if not isinstance(row.get('title'),str) or not isinstance(row.get('contents'),str) or len(row['contents'])>20000:
                    raise ProviderError('bad_response')
                rows.append({k:row.get(k) for k in ('gid','title','contents','date','feedname')})
            return {'app':app,'rows':rows}
        return await cached(sources,'steam','game_match_news',key,load)

    for raw in selected:
        apps=[uid for namespace,uid in products(raw) if namespace=='steam.app']
        if len(apps)!=1:continue
        app=apps[0]
        async def load_store():
            data=store_data(await store_request(sources.api.http,app),app)
            return {k:data.get(k) for k in ('steam_appid','type','name','developers','release_date','detailed_description')}
        store,snapshot=await cached(sources,'steam','game_match_store',app,load_store)
        snapshots.append(snapshot)
        if store.get('type')!='game' or spelling(store.get('name')) not in names:continue
        developers={spelling(row['company']['name']) for row in raw.get('involved_companies',[])
            if row.get('developer') and isinstance(row.get('company'),dict) and row['company'].get('name')}
        if not developers.intersection(spelling(name) for name in store.get('developers') or []):continue
        if credits is None:
            credits,snapshot=await cached(sources,'imdb','game_match_credits',title['id']+(':launch-v1' if launch_scope else ''),load_credits)
            snapshots.append(snapshot)
        support={'app':app,'developer_agreement':True,'store_title':store['name'],'people':[],'characters':[]}
        if launch_scope and (store.get('release_date') or {}).get('coming_soon') is False:
            store_day=None
            for form in ('%b %d, %Y','%d %b, %Y'):
                try:store_day=datetime.strptime(store['release_date']['date'],form).date().isoformat();break
                except (KeyError,TypeError,ValueError):pass
            normal=game_release(raw)
            if store_day and store_day==credits.get('release_date')==normal.get('release_date') and normal.get('release_date_precision')=='day':
                support['release_scope']={'imdb_date':store_day,'store_date':store_day,'igdb_date':store_day}
        description=plain(store.get('detailed_description'))
        # A distinctive full character name corroborates an undated exact
        # title. Generic/single-word labels cannot establish this tier.
        if title.get('year') is None or not years(raw):
            support['characters']=sorted({name for person in credits['people'] for name in person['characters']
                if len(spelling(name).split())>=2 and len(spelling(name))>=12
                and not re.search(r'(?i)\b(patient|guard|soldier|captain|doctor|nurse|various|additional|unnamed|unknown|animal|clerk)\b',name)
                and contains(description,name)})
        announcements={'rows':[]}
        if not support['characters']:
            announcements,snapshot=await news(app)
            snapshots.append(snapshot)
        for article in announcements['rows']:
            text=plain(article['contents'])
            if not re.search(r'(?i)\b(cast|voic\w*|actor\w*|director\w*|composer\w*)\b',text):continue
            people={person['id']:person for person in credits['people']
                if len(spelling(person['name']).split())>=2 and contains(text,person['name'])}
            own_title=any(contains(article['title'],name) for name in [title['title'],*title.get('aliases',[])])
            if own_title and len(people)>=2:
                support['people']=[{'id':person['id'],'role':person['role']} for person in people.values()]
                support['announcement']=article['gid'];break
            # Character reveal posts often omit the game's name. Bind a reveal
            # to an independently supplied exact actor/character pair instead.
            pairs=[{'id':person['id'],'character':name} for person in people.values() for name in person['characters']
                if len(spelling(name))>=5 and contains(article['title'],name)
                and re.search(r'(?i)\b(meet|introduc\w*|reveal\w*)\b',article['title'])
                and re.search(r'(?i)(?:voiced by|voice actor|played by|portrayed by)\W{0,20}'+re.escape(person['name']),text)]
            if pairs:
                support['cast_character']=pairs;support['announcement']=article['gid'];break
        # Check one historical window only when IMDb precedes a known release.
        # Early access explains a year difference; it never replaces display year.
        year=title.get('year');release=store.get('release_date') or {}
        release_years=re.findall(r'\b(?:19|20)\d{2}\b',release.get('date') or '')
        full_year=int(release_years[0]) if len(release_years)==1 and release.get('coming_soon') is False else None
        if not support['people'] and not support.get('cast_character') and type(year) is int and full_year in years(raw) and year<full_year:
            end=int(datetime(year+1,1,1,tzinfo=timezone.utc).timestamp())
            historical,snapshot=await news(app,end);snapshots.append(snapshot)
            for article in historical['rows']:
                if not contains(article['title'],title['title']) or type(article.get('date')) is not int:continue
                if datetime.fromtimestamp(article['date'],timezone.utc).year!=year:continue
                headline=plain(article['title'])
                actual=re.search(r'(?i)\b(now available|available now|out now|has (?:been )?(?:released|launched)|released today|launch(?:es|ed) today)\b',headline)
                planned=re.search(r'(?i)\b(will|next year|next month|tomorrow|delay\w*|postpon\w*|cancel\w*|not available|not released)\b',headline)
                unavailable=re.search(r'(?i)\bnot (?:yet )?(?:available|released)\b',plain(article['contents']))
                if actual and not planned and not unavailable and re.search(r'(?i)early\s+access',headline):
                    support['early_access']={'year':year,'release_year':full_year,'announcement':article['gid']};break
        if support['characters'] or len(support['people'])>=2 or support.get('cast_character') or support.get('early_access') or support.get('release_scope'):
            evidence[raw['id']]=support
    return evidence,snapshots
