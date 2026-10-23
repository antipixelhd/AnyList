from datetime import datetime, timedelta
from sqlalchemy import select
from models.tracking import TrackingActivity
from core.activity import activity_window_start, has_activity_event, _record_rating_details, RATING_ACTIVITY_FIELDS


def _rebuild(row):
    details = dict(row.payload or {})
    events = details.get('streaming_events', {})
    base = dict(details.get('streaming_base', {}))
    values = sorted(events.values(), key=lambda event: (event['date'], event['key'] == 'finish', event['key'].startswith('season:')))
    base['streaming_events'] = events
    base['streaming_base'] = details.get('streaming_base', {})
    base['episodes_watched'] = max(int(base.get('episodes_watched') or 0), sum(e.get('episodes_watched', 0) for e in values))
    base['finished_seasons'] = sorted(set(base.get('finished_seasons', [])) | {s for e in values for s in e.get('finished_seasons', [])})
    for event in values:
        for key in ('position', 'status_changed', 'started_watching'):
            if event.get(key) is not None:
                base[key] = event[key]
        row.status = event['status']
    if values:
        row.created_at = datetime.fromisoformat(max(e['date'] for e in values))
        base['window_started_at'] = min(e['date'] for e in values) + 'T00:00:00'
    for key in RATING_ACTIVITY_FIELDS:
        if key in details:
            base[key] = details[key]
    row.payload = base


async def record_streaming_activity(db, user_id, media_id, events, *, score=None, previous_score=None):
    rows = list((await db.execute(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media_id,
    ).order_by(TrackingActivity.created_at, TrackingActivity.id))).scalars().all())
    touched = []
    for event in events:
        # Stable evidence keys make a repeated import idempotent.
        existing = next((row for row in rows if event['key'] in (row.payload or {}).get('streaming_events', {})), None)
        if existing:
            old = existing.payload['streaming_events'][event['key']]
            if old['date'] <= event['date']:
                touched.append(existing)
                continue
            details = dict(existing.payload)
            details['streaming_events'] = {key: value for key, value in details['streaming_events'].items() if key != event['key']}
            existing.payload = details
            _rebuild(existing)
        now = datetime.fromisoformat(event['date'])
        row = next((r for r in reversed(rows) if activity_window_start(r) <= now < activity_window_start(r) + timedelta(hours=24)), None)
        if row is None:
            row = TrackingActivity(user_id=user_id, media_id=media_id, status=event['status'], created_at=now,
                                   payload={'window_started_at': now.isoformat(), 'streaming_base': {}})
            db.add(row)
            rows.append(row)
        details = dict(row.payload or {})
        if 'streaming_base' not in details:
            details['streaming_base'] = dict(details)
        details['streaming_events'] = {**details.get('streaming_events', {}), event['key']: event}
        row.payload = details
        _rebuild(row)
        touched.append(row)
    active = [row for row in touched if has_activity_event(row.status, row.payload or {})]
    latest = max(active, key=lambda row: row.created_at, default=None)
    if latest is not None and score is not None:
        details = dict(latest.payload)
        _record_rating_details(details, rating_changed=True, previous_score=previous_score, score=score, now=latest.created_at)
        latest.payload, latest.score = details, score
    for row in rows:
        if 'streaming_events' in (row.payload or {}) and not has_activity_event(row.status, row.payload):
            await db.delete(row)
    return latest

async def reschedule_date_activity(db, user_id, media_id, *, start_date, finish_date, changed):
    """Move date actions and their attached rating, preserving unrelated history."""
    rows = list((await db.execute(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media_id,
    ).order_by(TrackingActivity.created_at.desc(), TrackingActivity.id.desc()))).scalars().all())
    moved = []
    ratings = []
    for key, value in (('start', start_date), ('finish', finish_date)):
        if ('start_date' if key == 'start' else 'finish_date') not in changed:
            continue
        tagged = [row for row in rows if key in (row.payload or {}).get('streaming_events', {})]
        legacy = next((row for row in rows if 'streaming_events' not in (row.payload or {}) and
                       (row.payload or {}).get('status_changed') and
                       (row.status == 'completed' if key == 'finish' else (row.payload or {}).get('started_watching'))), None)
        for row in tagged or ([legacy] if legacy is not None else []):
            details = dict(row.payload or {})
            if row in tagged:
                event = dict(details['streaming_events'][key])
                details['streaming_events'] = {name: item for name, item in details['streaming_events'].items() if name != key}
            else:
                event = {'key': key, 'status': 'completed' if key == 'finish' else 'watching', 'status_changed': True}
                if key == 'start':
                    event['started_watching'] = True
                details['status_changed'] = False
                details.pop('started_watching', None)
            # A start-date edit must not steal a rating from a combined finish card.
            if row.score is not None and details.get('rating_changed') and (key == 'finish' or row.status == 'watching'):
                ratings.append((event, row.score, {name: item for name, item in details.items() if name in RATING_ACTIVITY_FIELDS}))
                details = {name: item for name, item in details.items() if name not in RATING_ACTIVITY_FIELDS}
                if 'streaming_base' in details:
                    details['streaming_base'] = {name: item for name, item in details['streaming_base'].items() if name not in RATING_ACTIVITY_FIELDS}
                row.score = None
            row.payload = details
            if row in tagged:
                _rebuild(row)
            if value:
                event['date'] = value.isoformat()
                moved.append(event)
    await db.flush()
    latest = await record_streaming_activity(db, user_id, media_id, moved)
    for event, score, metadata in ratings:
        if event.get('date') and event in moved:
            target = await record_streaming_activity(db, user_id, media_id, [event])
        else:
            target = max((row for row in rows if has_activity_event(row.status, row.payload or {}) and row.score is None),
                         key=lambda row: row.created_at, default=None)
        if target is not None:
            target.score = score
            target.payload = {**target.payload, **metadata}
    for row in rows:
        if not has_activity_event(row.status, row.payload or {}):
            await db.delete(row)
    return latest
