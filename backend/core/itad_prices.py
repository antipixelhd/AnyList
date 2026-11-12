"""Validate the regional, Steam-only portion of an ITAD price response."""
import math
import re
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from core.catalogue_providers import ProviderError


def money(value):
    if value is None:return None
    if not isinstance(value,dict) or not isinstance(value.get('currency'),str) or not re.fullmatch(r'[A-Z]{3}',value['currency']):
        raise ProviderError('bad_response')
    decimal=value.get('amount');integer=value.get('amountInt')
    if decimal is None and integer is None:raise ProviderError('bad_response')
    if decimal is not None and (type(decimal) not in (int,float) or not math.isfinite(decimal) or decimal<0):raise ProviderError('bad_response')
    if integer is not None and (type(integer) is not int or integer<0):raise ProviderError('bad_response')
    return {key:value[key] for key in ('currency','amount','amountInt') if value.get(key) is not None}


def game_id(lookup):
    if not isinstance(lookup,dict) or type(lookup.get('found')) is not bool:raise ProviderError('bad_response')
    if not lookup['found']:raise ProviderError('not_found')
    game=lookup.get('game');native=game.get('id') if isinstance(game,dict) else None
    try:
        if not isinstance(native,str) or str(UUID(native))!=native:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ProviderError('bad_response') from None
    return native


def shop_record(rows,native,field):
    if not isinstance(rows,list) or len(rows)>200:raise ProviderError('bad_response')
    matched=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not isinstance(row.get(field),list):raise ProviderError('bad_response')
        if row['id']!=native:continue
        matched.append(row)
    if len(matched)>1:raise ProviderError('bad_response')
    items=[]
    for row in matched:
        for item in row[field]:
            if not isinstance(item,dict) or not isinstance(item.get('shop'),dict):raise ProviderError('bad_response')
            if type(item['shop'].get('id')) is not int or item['shop']['id']<=0:raise ProviderError('bad_response')
            if item['shop']['id']==61:items.append(item)
    if len(items)>1:raise ProviderError('bad_response')
    return items[0] if items else {}


def normalized(native,prices,lows):
    current=shop_record(prices,native,'deals');historical=shop_record(lows,native,'lows')
    price=money(current.get('price'));low=money(historical.get('price'))
    if current and price is None or historical and low is None:raise ProviderError('bad_response')
    timestamp=historical.get('timestamp')
    if timestamp is not None:
        try:
            if not isinstance(timestamp,str) or not datetime.fromisoformat(timestamp.replace('Z','+00:00')).tzinfo:raise ValueError()
        except (TypeError,ValueError):raise ProviderError('bad_response') from None
    url=current.get('url')
    if url is not None:
        try:
            if not isinstance(url,str):raise ValueError()
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError()
        except ValueError:raise ProviderError('bad_response') from None
    return {'itad_id':native,'current':price,'historical_low':low,'historical_low_at':timestamp,'url':url}
