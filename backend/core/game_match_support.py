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

KINDS={'game_match_credits','game_match_store','game_match_news','game_match_identity','game_match_wiki'}


def plain(value):
    return ' '.join(unescape(re.sub(r'<[^>]*>|\[[^\]]*\]', ' ', value or '')).split())


def contains(text, value):
    value=spelling(value)
    return bool(value and any(f' {form} ' in f' {spelling(text)} ' for form in (value,value+'s')))


def store_date(value):
    for form in ('%b %d, %Y','%d %b, %Y'):
        try:return datetime.strptime(value,form).date()
        except (TypeError,ValueError):pass
    return None


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
    evidence={};snapshots=[]
    if not selected:
        from core.game_wikidata_title_support import corroborate as wiki_corroborate
        return await wiki_corroborate(sources,title,candidates)
    if len(selected)>4:return {},[]
    credits=None
    launch_scope=any(igdb_game_class(raw)=='port'
        and raw.get('release_dates') and all(isinstance(row.get('status'),dict) and row['status'].get('name','').casefold() in ('cancelled','canceled')
            for row in raw['release_dates']) for raw in selected)
    later_scope=type(title.get('year')) is int and any(years(raw) and min(years(raw))<title['year'] for raw in selected)

    # Non-store titles need not be forced through Steam for a regional date.
    # IMDb's primary year and detailed release date can legitimately differ.
    # A focused, cached current identity read preserves that evidence explicitly.
    regional=[raw for raw in selected if not products(raw) and type(title.get('year')) is int
        and years(raw) and title['year'] not in years(raw)]
    if regional:
        async def load_identity():
            header=await IMDbGameSource(sources.api.http).title(title['id'])
            point=header.get('releaseDate') or {}
            try:
                if any(type(point.get(key)) is not int for key in ('year','month','day')):raise ValueError()
                day=datetime(point['year'],point['month'],point['day']).date().isoformat()
            except (KeyError,TypeError,ValueError):day=None
            return {'id':header['id'],'title':header['titleText']['text'],
                'year':(header.get('releaseYear') or {}).get('year'),'date':day}
        identity,snapshot=await cached(sources,'imdb','game_match_identity',title['id'],load_identity)
        snapshots.append(snapshot)
        from core.game_title_match import supported
        for raw in regional:
            proof={'release_identity':identity}
            if supported({'years':sorted(years(raw)),'corroboration':proof},title):evidence[raw['id']]=proof

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
        if raw['id'] in evidence:continue
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
            credits,snapshot=await cached(sources,'imdb','game_match_credits',title['id']+(':release-v1' if later_scope else ':launch-v1' if launch_scope else ''),load_credits)
            snapshots.append(snapshot)
        support={'app':app,'developer_agreement':True,'store_title':store['name'],'people':[],'characters':[]}
        if launch_scope and (store.get('release_date') or {}).get('coming_soon') is False:
            store_day=store_date((store.get('release_date') or {}).get('date'))
            store_day=store_day.isoformat() if store_day else None
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
        # Store dates often retain the first early-access launch. A publisher
        # announcement on IMDb's exact full-release day explains that mismatch.
        if not support['people'] and not support.get('cast_character') and later_scope:
            try:
                launch=datetime.strptime(credits['release_date'],'%Y-%m-%d').date()
                initial=store_date(release.get('date'))
            except (KeyError,TypeError,ValueError):launch=initial=None
            if (launch and initial and release.get('coming_soon') is False and initial.year in years(raw)
                    and initial.year<launch.year==year and launch<=datetime.now(timezone.utc).date()):
                end=int(datetime.combine(launch+timedelta(days=1),datetime.min.time(),timezone.utc).timestamp())
                historical,snapshot=await news(app,end);snapshots.append(snapshot)
                for article in historical['rows']:
                    if type(article.get('date')) is not int or datetime.fromtimestamp(article['date'],timezone.utc).date()!=launch:continue
                    lead=plain(article['title'])+' '+plain(article['contents'])[:600]
                    own=any(contains(lead,name) for name in [title['title'],*title.get('aliases',[])])
                    actual=re.search(r'(?i)\b(out of (?:early access|beta)|full release|version 1\.0)\b',lead)
                    present=re.search(r'(?i)\b(today|now|with this launch)\b',lead)
                    planned=re.search(r'(?i)\b(will|tomorrow|next (?:week|month|year)|in \d+ days|delay\w*|postpon\w*|not (?:yet )?(?:out|available|released))\b',lead)
                    if own and actual and present and not planned:
                        support['full_release']={'date':launch.isoformat(),'initial_date':initial.isoformat(),'announcement':str(article['gid'])};break
        if support['characters'] or len(support['people'])>=2 or support.get('cast_character') or support.get('early_access') or support.get('full_release') or support.get('release_scope'):
            evidence[raw['id']]=support
    if not evidence:
        from core.game_wikidata_title_support import corroborate as wiki_corroborate
        extra,references=await wiki_corroborate(sources,title,candidates)
        evidence.update(extra);snapshots.extend(references)
    return evidence,snapshots
