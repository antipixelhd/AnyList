"""IGDB timestamps may be placeholders: preserve their stated precision."""
import calendar
from datetime import date, datetime, timezone

VERSION = 1
FIELDS = 'release_dates.date,release_dates.date_format.format,release_dates.y,release_dates.m,release_dates.d'
FORMATS = {0:'YYYYMMDD',1:'YYYYMM',2:'YYYY',3:'YYYYQ1',4:'YYYYQ2',5:'YYYYQ3',6:'YYYYQ4',7:'TBD'}
KEYS = {'release_date','release_date_precision','release_quarter','release_date_evidence','release_date_projection'}


def timestamp_day(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None


def release(row):
    stamp = timestamp_day(row.get('date'))
    raw = row.get('date_format')
    if isinstance(raw, dict):
        identifier = raw.get('id')
        form = raw.get('format') or (FORMATS.get(identifier) if type(identifier) is int else None)
        if type(identifier) is int and identifier in FORMATS and form != FORMATS[identifier]:
            form = None
    else:
        identifier = raw if type(raw) is int else row.get('category')
        form = FORMATS.get(identifier) if type(identifier) is int else None
    year = row.get('y') if type(row.get('y')) is int else stamp.year if stamp else None
    month = row.get('m') if type(row.get('m')) is int else stamp.month if stamp else None
    day = row.get('d') if type(row.get('d')) is int else stamp.day if stamp else None
    result = {'release_date':None,'release_date_precision':'unknown','release_quarter':None,
        'release_date_evidence':'release_dates','release_date_projection':VERSION}
    if form == 'TBD' or not year or not 1 <= year <= 9999:
        return result
    try:
        if form == 'YYYYMMDD':
            result.update(release_date=date(year,month,day).isoformat(),release_date_precision='day')
        elif form == 'YYYYMM':
            result.update(release_date=date(year,month,1).isoformat()[:7],release_date_precision='month')
        elif form == 'YYYY':
            result.update(release_date=f'{year:04}',release_date_precision='year')
        elif form in {'YYYYQ1','YYYYQ2','YYYYQ3','YYYYQ4'}:
            result.update(release_date=f'{year:04}',release_date_precision='quarter',release_quarter=int(form[-1]))
    except (ValueError, TypeError):
        pass
    return result


def game_release(raw):
    stamp = timestamp_day(raw.get('first_release_date'))
    rows = [row for row in raw.get('release_dates') or [] if isinstance(row, dict)]
    matching = [row for row in rows if stamp and timestamp_day(row.get('date')) == stamp]
    if not stamp:
        dated = [row for row in rows if timestamp_day(row.get('date'))]
        if dated:
            first = min(timestamp_day(row['date']) for row in dated)
            matching = [row for row in dated if timestamp_day(row['date']) == first]
    if matching:
        # Conflicting platform precision cannot promote a shared placeholder
        # to an exact day. Keep the least precise supported evidence.
        points = [release(row) for row in matching]
        order = {'unknown':0,'year':1,'quarter':2,'month':3,'day':4}
        return min(points,key=lambda point:order[point['release_date_precision']])
    if not stamp and rows:
        return release(rows[0])
    return {'release_date':f'{stamp.year:04}' if stamp else None,
        'release_date_precision':'year' if stamp else 'unknown','release_quarter':None,
        'release_date_evidence':'first_release_date','release_date_projection':VERSION}


def merge_release(attrs, sources, protected, incoming, source, *, rank_allowed=True):
    """Keep a precise provider projection together; shallow stubs cannot erase it."""
    if incoming.get('release_date_projection') != VERSION or source != 'igdb':
        return False
    richer = attrs.get('release_date_evidence') == 'release_dates' and incoming.get('release_date_evidence') != 'release_dates'
    unavailable = incoming.get('release_date') is None and incoming.get('release_date_evidence') != 'release_dates'
    blocked = any('attributes.'+key in protected for key in KEYS)
    # A protected/manual source also guards the whole precision tuple.
    old_source = sources.get('attributes.release_date')
    if not (richer or unavailable or blocked or not rank_allowed or old_source in ('manual','reviewed')):
        for key in KEYS:
            attrs[key] = incoming.get(key)
            sources['attributes.'+key] = source
    return True


def bounds(row):
    value = row.get('release_date')
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 4 and value.isdecimal():
            year = int(value)
            quarter = row.get('release_quarter') if row.get('release_date_precision') == 'quarter' else None
            if type(quarter) is int and 1 <= quarter <= 4:
                month = quarter * 3
                return date(year,month-2,1).isoformat(), date(year,month,calendar.monthrange(year,month)[1]).isoformat()
            return date(year,1,1).isoformat(),date(year,12,31).isoformat()
        if len(value) == 7:
            first = date.fromisoformat(value+'-01')
            return first.isoformat(),date(first.year,first.month,calendar.monthrange(first.year,first.month)[1]).isoformat()
        if len(value) == 10:
            return (date.fromisoformat(value).isoformat(),)*2
    except ValueError:
        pass
    return None


def in_range(row, start=None, end=None, *, released_before=None):
    interval = bounds(row)
    if not interval:
        return not (start or end or released_before)
    first,last = interval
    return not (start and last < start or end and first > end or released_before and last > released_before)
