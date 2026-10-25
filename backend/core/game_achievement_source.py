"""Steam catalogue facts only: no player profile, account linking or imports."""
import asyncio
import math
import re
from urllib.parse import urlsplit,urlunsplit

from core.catalogue_providers import ProviderError,ProviderHTTP
from core.config import settings

BASE='https://api.steampowered.com/ISteamUserStats/'


def native_app(app):
    if not isinstance(app,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',app) or int(app)>4294967295:
        raise ProviderError('invalid_request')
    return app


def percentages(raw):
    data=raw.get('achievementpercentages') if isinstance(raw,dict) else None
    rows=data.get('achievements') if isinstance(data,dict) else None
    if not isinstance(rows,list) or len(rows)>50000:raise ProviderError('bad_response')
    result={}
    for row in rows:
        if not isinstance(row,dict):raise ProviderError('bad_response')
        name=row.get('name');value=row.get('percent')
        if not isinstance(name,str) or not 1<=len(name)<=200 or name in result or isinstance(value,bool):raise ProviderError('bad_response')
        try:value=float(value)
        except (ValueError,TypeError):raise ProviderError('bad_response') from None
        if not math.isfinite(value) or not 0<=value<=100:raise ProviderError('bad_response')
        result[name]=value
    return {'available':True,'total':len(result),'percentages':result}


def icon(value,app):
    if not isinstance(value,str):return None
    try:url=urlsplit(value);port=url.port
    except ValueError:return None
    if (url.scheme not in {'http','https'} or url.username or url.password or port or url.query or url.fragment
        or not (url.hostname and (url.hostname.endswith('.steamstatic.com') or url.hostname in {'steamcdn-a.akamaihd.net','steamcommunity-a.akamaihd.net'}))
        or not url.path.startswith(f'/steamcommunity/public/images/apps/{app}/')):return None
    return urlunsplit(('https',url.netloc,url.path,'',''))


def definitions(raw,app):
    game=raw.get('game') if isinstance(raw,dict) else None
    stats=game.get('availableGameStats') if isinstance(game,dict) else None
    rows=stats.get('achievements') if isinstance(stats,dict) else None
    # An omitted field or {} is not proof that this game has zero achievements.
    if not isinstance(rows,list) or len(rows)>50000 or not isinstance(game.get('gameName'),str) or not game['gameName'].strip():
        raise ProviderError('bad_response')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ProviderError('bad_response')
        name,label,hidden=row.get('name'),row.get('displayName'),row.get('hidden')
        if (not isinstance(name,str) or not 1<=len(name)<=200 or name in seen or
            not isinstance(label,str) or not 1<=len(label)<=500 or type(hidden) not in (bool,int) or hidden not in (0,1)):
            raise ProviderError('bad_response')
        description=row.get('description')
        if description is not None and (not isinstance(description,str) or len(description)>20000):raise ProviderError('bad_response')
        seen.add(name)
        result.append({'api_name':name,'name':label,'description':description,'hidden':bool(hidden),
            'icon_url':icon(row.get('icon'),app),'locked_icon_url':icon(row.get('icongray'),app)})
    return {'available':True,'total':len(result),'definitions':result,'language':'english'}


class GameAchievementSource:
    def __init__(self,factory=None,*,http=None,key=None):
        self.http=http or ProviderHTTP(session_factory=factory)
        self.key=settings.steam_web_api_key if key is None else key

    async def fetch(self,app,part,stamp):
        native_app(app)
        if part=='globals':
            raw=await self.http.request('steam','GET',BASE+'GetGlobalAchievementPercentagesForApp/v2/',
                params={'gameid':app,'format':'json'})
            return await asyncio.to_thread(percentages,raw)
        if part=='schema':
            if not self.key:return {'available':False,'reason':'not_configured'}
            raw=await self.http.request('steam','GET',BASE+'GetSchemaForGame/v2/',
                params={'appid':app,'key':self.key,'l':'english','format':'json'})
            return await asyncio.to_thread(definitions,raw,app)
        raise ProviderError('invalid_request')
