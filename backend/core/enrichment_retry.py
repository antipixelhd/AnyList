"""Retry transport failures; revisit stable content gaps only on demand.

Malformed responses are not negative evidence. Only explicit missing content,
missing records and identity conflicts can leave the recurring worker queue.
"""
import calendar
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

CONTENT_FAILURES = frozenset({'content_unavailable', 'not_found', 'identity_conflict'})
VERSION = 1
DEMAND_WINDOW = timedelta(days=7)
OPERATIONAL_COVERAGE = frozenset({'requested_at','same_failure_count','retry_policy_version','retry_mode','retry_steam_app_id'})


def public_coverage(coverage):
    return {key:value for key,value in (coverage or {}).items() if key not in OPERATIONAL_COVERAGE}


async def release_context(db, entity_id):
    """Read only release evidence, not a title's artwork/credits/attributes blob."""
    if not entity_id:
        return None
    from sqlalchemy import select
    from models.catalogue import CatalogueEntity
    row=(await db.execute(select(CatalogueEntity.kind,*(CatalogueEntity.attributes[key].astext.label(key)
        for key in ('release_date','release_date_precision','release_quarter','status')))
        .where(CatalogueEntity.id==entity_id))).mappings().one_or_none()
    attrs=dict(row or {})
    if attrs.get('release_quarter') in ('1','2','3','4'):
        attrs['release_quarter']=int(attrs['release_quarter'])
    return SimpleNamespace(kind=attrs.pop('kind',None),attributes=attrs)


def requested_coverage(stamp):
    return {'requested_at': stamp.isoformat()}


def demand_expired(value, stamp):
    try:
        return bool(value and stamp - datetime.fromisoformat(value) > DEMAND_WINDOW)
    except (TypeError, ValueError):
        return False


def pause_unused(row, stamp):
    """Do not keep retrying a failed job after its viewer demand goes cold."""
    if row.error_code and demand_expired((row.coverage or {}).get('requested_at'), stamp):
        row.next_attempt_at = None
        row.expires_at = stamp  # A new visit may re-admit immediately.
        row.coverage = {**(row.coverage or {}), 'retry_mode':'on_demand', 'pending':False}
        return True
    return False


def retain_demand(row, coverage):
    requested = (row.coverage or {}).get('requested_at')
    return {**({'requested_at':requested} if requested else {}), **coverage}


def content_recheck(work, stamp):
    """Use the latest possible release date when only a year/month is known."""
    attrs = getattr(work, 'attributes', None) or {}
    value = attrs.get('release_date') or getattr(work, 'release_date', None)
    released = None
    if isinstance(value, date):
        released = value.date() if isinstance(value, datetime) else value
    elif isinstance(value, str):
        try:
            if len(value) == 4:
                quarter=attrs.get('release_quarter')
                month=quarter*3 if attrs.get('release_date_precision')=='quarter' and type(quarter) is int and 1<=quarter<=4 else 12
                released = date(int(value), month, calendar.monthrange(int(value),month)[1])
            elif len(value) == 7:
                year, month = map(int, value.split('-'))
                released = date(year, month, calendar.monthrange(year, month)[1])
            else:
                released = date.fromisoformat(value[:10])
        except ValueError:
            pass
    days = (stamp.date() - released).days if released else None
    delay = timedelta(days=7 if days is None or days < -30 else
                      1 if days <= 90 else 7 if days <= 365 else
                      30 if days <= 730 else 90)
    if getattr(work,'kind',None)=='series' and str(attrs.get('status') or '').casefold() not in {'ended','canceled','cancelled'}:
        delay=min(delay,timedelta(days=7))
    deadline = stamp + delay
    # Even a weekly prerelease observation expires at the release gate.
    if released and released > stamp.date():
        deadline = min(deadline, datetime.combine(released, time.min))
    return deadline


def schedule_failure(row, code, stamp, *, work=None, retry_after=None,
                     base_delay=60, max_delay=3600):
    previous = row.error_code
    row.attempts = (row.attempts or 0) + 1
    coverage = dict(row.coverage or {})
    repeats = (coverage.get('same_failure_count', 0) if previous == code else 0) + 1
    coverage.update(same_failure_count=repeats, retry_policy_version=VERSION)
    row.error_code = code
    if code in CONTENT_FAILURES and repeats >= 3:
        row.next_attempt_at = None
        row.expires_at = content_recheck(work, stamp)
        if retry_after:
            row.expires_at = max(row.expires_at, stamp + timedelta(seconds=retry_after))
        coverage.update(retry_mode='on_demand', pending=False)
    else:
        delay = max(retry_after or 0, min(max_delay, base_delay * 2 ** min(row.attempts - 1, 10)))
        row.next_attempt_at = stamp + timedelta(seconds=delay)
        row.expires_at = None
        coverage.update(retry_mode='background')
    row.coverage = coverage
