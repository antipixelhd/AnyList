"""Observed Steam Store fields, normalized to text rather than provider HTML."""
import asyncio
import re
from collections import OrderedDict
from html import unescape
from html.parser import HTMLParser

from core.catalogue_providers import ProviderError,ProviderHTTP

_locks=OrderedDict()
BOARDS={
    'esrb':('ESRB','United States / Canada',{'e':'Everyone','e10':'Everyone 10+','e10+':'Everyone 10+','t':'Teen','m':'Mature 17+','ao':'Adults Only 18+','rp':'Rating Pending','ec':'Early Childhood'}),
    'pegi':('PEGI','Europe',{str(v):str(v) for v in (3,7,12,16,18)}),
    'usk':('USK','Germany',{str(v):str(v) for v in (0,6,12,16,18)}),
    'cero':('CERO','Japan',{v:v.upper() for v in ('a','b','c','d','z')}),
    'oflc':('ACB','Australia',{v:v.upper() for v in ('g','pg','m','ma15','ma15+','r18','r18+','rc')}),
    'kgrb':('GRAC','South Korea',{'all':'All','12':'12','15':'15','18':'18'}),
    'dejus':('CLASS_IND','Brazil',{v:v.upper() for v in ('l','10','12','14','16','18')}),
}


def store_data(raw,app):
    if not isinstance(raw,dict) or not isinstance(raw.get(app),dict):raise ProviderError('bad_response')
    response=raw[app]
    if response.get('success') is not True:raise ProviderError('unavailable')
    data=response.get('data')
    if not isinstance(data,dict) or type(data.get('steam_appid')) is not int or data['steam_appid']!=int(app):raise ProviderError('bad_response')
    return data


async def store_request(http,app):
    """Share the transport cache across critic/info consumers, including misses."""
    if not isinstance(app,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',app) or int(app)>4294967295:raise ProviderError('invalid_request')
    key=(id(http.session_factory),id(http.client),app)
    lock=_locks.get(key)
    if lock is None:
        while len(_locks)>=256:
            idle=next((key for key,value in _locks.items() if not value.locked()),None)
            if idle is None:raise ProviderError('busy',5)
            _locks.pop(idle)
        lock=asyncio.Lock();_locks[key]=lock
    _locks.move_to_end(key)
    async with lock:
        return await http.request('steam','GET','https://store.steampowered.com/api/appdetails',
            params={'appids':app,'l':'english','cc':'US'})


class RequirementText(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.lines=[];self.current=[];self.blocked=0
    def line(self):
        value=' '.join(''.join(self.current).split());self.current=[]
        if value:self.lines.append(value)
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','iframe','object','svg','math','template'):self.blocked+=1
        elif not self.blocked and tag in ('br','li','p','div','ul','ol'):self.line()
    def handle_endtag(self,tag):
        if tag in ('script','style','iframe','object','svg','math','template'):self.blocked=max(0,self.blocked-1)
        elif not self.blocked and tag in ('li','p','div','ul','ol'):self.line()
    def handle_data(self,value):
        if not self.blocked:self.current.append(value)


def text_rows(raw):
    if raw in (None,''):return []
    if not isinstance(raw,str) or len(raw)>24000:raise ProviderError('bad_response')
    parser=RequirementText();parser.feed(raw);parser.close();parser.line()
    if len(parser.lines)>100:raise ProviderError('bad_response')
    rows=[]
    for value in parser.lines:
        if value.casefold().rstrip(':') in ('minimum','recommended'):continue
        label,separator,body=value.partition(':')
        if separator and len(label)<60:
            if body.strip():rows.append({'label':label.strip(),'value':body.strip()})
        else:rows.append({'label':None,'value':value})
    return rows


def age_ratings(raw):
    if raw is None:return []
    if not isinstance(raw,dict):raise ProviderError('bad_response')
    rows=[]
    for key,(board,region,values) in BOARDS.items():
        value=raw.get(key)
        if not isinstance(value,dict):continue
        rating=value.get('rating');rating=values.get(str(rating).lower()) if isinstance(rating,(str,int)) and not isinstance(rating,bool) else None
        if not rating:continue
        description=value.get('descriptors')
        if description is not None and (not isinstance(description,str) or len(description)>5000):raise ProviderError('bad_response')
        descriptors=[' '.join(unescape(line).split()) for line in (description or '').splitlines() if line.strip()]
        rows.append({'board':board,'region':region,'rating':rating,'descriptors':descriptors,'source':'steam'})
    return rows


def igdb_age_ratings(raw):
    rows=[]
    for value in raw or []:
        if not isinstance(value,dict):continue
        organization=value.get('organization');category=value.get('rating_category')
        if not isinstance(organization,dict) or not isinstance(category,dict):continue
        name=organization.get('name');rating=category.get('rating')
        known=next(((board,region) for board,region,_ in BOARDS.values() if board==str(name).upper()),None)
        if not known or not isinstance(rating,str) or not rating.strip() or len(rating)>80:continue
        rows.append({'board':known[0],'region':known[1],'rating':rating.strip(),'descriptors':[],
            'source':'igdb','source_id':str(value['id']) if type(value.get('id')) is int else None})
    return rows


class GameStoreSource:
    def __init__(self,factory):self.http=ProviderHTTP(session_factory=factory)

    async def fetch(self,app,part,stamp):
        raw=await store_request(self.http,app);data=store_data(raw,app)
        requirements=data.get('pc_requirements')
        if requirements is None or requirements==[]:requirements={}
        if not isinstance(requirements,dict):raise ProviderError('bad_response')
        minimum=text_rows(requirements.get('minimum'));recommended=text_rows(requirements.get('recommended'))
        return {'age_ratings':age_ratings(data.get('ratings')),
            'requirements':{'minimum':minimum,'recommended':recommended,'platform':'PC','source':'steam'} if minimum or recommended else None,
            'source':'steam','store_country':'US'}
