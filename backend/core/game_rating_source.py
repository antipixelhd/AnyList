"""Bounded Steam review summaries and observed Store critic metadata."""
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from core.catalogue_providers import ProviderError,ProviderHTTP

PARTS=('all_time','recent','critic')


def review_query(app,part,stamp):
    if not isinstance(app,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',app) or int(app)>4294967295 or part not in PARTS[:2]:
        raise ProviderError('invalid_request')
    request={'appid':int(app),'languages':['english'],'filter':1,'review_type':0,'purchase_type':0,
        'num_per_page':1,'filter_offtopic_activity':True,'display_language':'english','cursor':'*'}
    if part=='recent':request.update(date_range_start=int((stamp-timedelta(days=30)).timestamp()),date_range_end=int(stamp.timestamp()))
    return request


def summary(raw):
    if not isinstance(raw,dict):raise ProviderError('bad_response')
    data=raw.get('response',raw)
    value=data.get('query_summary') if isinstance(data,dict) else None
    if not isinstance(value,dict):raise ProviderError('bad_response')
    values=[value.get(key) for key in ('total_positive','total_negative','total_reviews')]
    if any(type(number) is not int or number<0 for number in values) or values[0]+values[1]!=values[2]:raise ProviderError('bad_response')
    description=value.get('review_score_desc')
    if description is not None and (not isinstance(description,str) or len(description)>150):raise ProviderError('bad_response')
    positive,negative,total=values
    return {'positive':positive,'negative':negative,'total':total,'percentage':positive/total*100 if total else None,
        'description':description,'language':'english','purchase_type':'steam','offtopic_filtered':True}


def critic(raw,app):
    if not isinstance(raw,dict) or not isinstance(raw.get(app),dict):raise ProviderError('bad_response')
    response=raw[app]
    # Region restriction, removal or Store failure is unavailable evidence,
    # never a successful assertion that this game has no critic score.
    if response.get('success') is not True:raise ProviderError('unavailable')
    data=response.get('data')
    if not isinstance(data,dict) or type(data.get('steam_appid')) is not int or data['steam_appid']!=int(app):raise ProviderError('bad_response')
    value=data.get('metacritic')
    if value is None:return {'score':None,'url':None,'source':'steam_store','platform':'PC'}
    if not isinstance(value,dict) or type(value.get('score')) is not int or not 0<=value['score']<=100:raise ProviderError('bad_response')
    url=value.get('url')
    if url is not None:
        try:
            if not isinstance(url,str):raise ValueError()
            parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname not in ('metacritic.com','www.metacritic.com') or parsed.username or parsed.password or not parsed.path.startswith('/game/'):raise ValueError()
        except ValueError:raise ProviderError('bad_response') from None
    return {'score':value['score'],'url':url,'source':'steam_store','platform':'PC'}


class GameRatingSource:
    def __init__(self,factory):self.http=ProviderHTTP(session_factory=factory)

    async def fetch(self,app,part,stamp):
        if part=='critic':
            raw=await self.http.request('steam','GET','https://store.steampowered.com/api/appdetails',
                params={'appids':app,'l':'english','cc':'US'})
            return {**critic(raw,app),'store_country':'US'}
        request=review_query(app,part,stamp)
        # Only this first-page summary is retained. No review paging, author
        # data, account linking or individual review text enters the cache.
        raw=await self.http.request('steam','GET','https://api.steampowered.com/IUserReviewsService/GetAppReviews/v1/',
            params={'input_json':json.dumps(request,separators=(',',':'))},cache=False)
        return {**summary(raw),'window_start':request.get('date_range_start'),'window_end':request.get('date_range_end')}
