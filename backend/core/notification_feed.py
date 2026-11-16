"""Private notification reads, separate from operational delivery queues."""
from datetime import datetime, timedelta

from sqlalchemy import case, func, or_, select, update

from core import tracking_projection
from core.import_notifications import active_import_events
from models import Media, MediaServerConnection
from models.sync import SyncJob, SyncStatus
from models.tracking import SyncReview

HIGH_REVIEW_KINDS = ('initial_import', 'initial_cloud_import', 'conflict', 'cloud_conflict', 'rating_conflict',
    'deletion_conflict', 'unmatched_import', 'library_conflict', 'watch_conflict', 'progress_conflict', 'resume_conflict')


def priority_expression():
    return case((SyncReview.kind.in_(HIGH_REVIEW_KINDS), 'high'),
        (SyncReview.kind == 'outbound_pending', 'medium'), else_=func.coalesce(func.nullif(SyncReview.priority, ''), 'low'))


async def retire_expired_reviews(db, user_id, retention_days):
    """Extending retention must not resurrect already expired quiet notices."""
    now = datetime.utcnow()
    await db.execute(update(SyncReview).where(SyncReview.user_id == user_id,
        SyncReview.dismissed_at.is_(None), SyncReview.state != 'pending',
        SyncReview.created_at < now-timedelta(days=retention_days), priority_expression() == 'low')
        .values(dismissed_at=now).execution_options(synchronize_session=False))


def review_data(review, media):
    return {'id': review.id, 'kind': review.kind, 'state': review.state, 'provider': review.provider,
        'message': review.message, 'previous_status': review.previous_status, 'proposed_status': review.proposed_status,
        'previous_score': review.previous_score, 'proposed_score': review.proposed_score,
        'season_number': review.season_number, 'priority': review_priority(review),
        'dismissible': review.state != 'pending' or review.kind in {'new_season_release_date', 'new_season_release'},
        'payload': review.payload or {}, 'media': tracking_projection.media_data(media) if media else None,
        'created_at': review.created_at}


async def notification_feed(db, user_id, prefs):
    """Only user notifications: never hydrate delivery queues or sync statistics."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=prefs.low_priority_retention_days if prefs else 7)
    priority = priority_expression()
    # Apply retention before the existing 100-item window, matching the inbox.
    # Expiration is a read predicate here; opening the full inbox persists cleanup.
    rows = (await db.execute(select(SyncReview, Media).outerjoin(Media, Media.id == SyncReview.media_id)
        .where(SyncReview.user_id == user_id, SyncReview.dismissed_at.is_(None),
            or_(SyncReview.state == 'pending', SyncReview.state.is_(None), SyncReview.created_at >= cutoff, priority != 'low'))
        .order_by(SyncReview.created_at.desc()).limit(100))).all()
    rows = [(r, m) for r, m in rows if r.kind != 'outbound_pending'
        and (not prefs or prefs.low_priority_notifications or review_priority(r) != 'low' or r.state == 'pending')]
    jobs = (await db.execute(select(SyncJob.source, SyncJob.status, SyncJob.connection_id,
        SyncJob.error_message, SyncJob.updated_at, MediaServerConnection.name)
        .outerjoin(MediaServerConnection, (MediaServerConnection.id == SyncJob.connection_id)
            & (MediaServerConnection.user_id == user_id))
        .where(SyncJob.user_id == user_id)
        .order_by(SyncJob.updated_at.desc(), SyncJob.id.desc()).limit(200))).all()
    names = {row.connection_id: row.name for row in jobs if row.name is not None}
    failures = connection_failure_events(jobs, names)
    imports = await active_import_events(db, user_id, now)
    return {'pending': sum(r.state == 'pending' for r, _ in rows) + len(failures) + len(imports),
        'results': imports + failures + [review_data(r, m) for r, m in rows], 'outbound': []}


def review_priority(review: SyncReview) -> str:
    if review.kind in HIGH_REVIEW_KINDS:
        return 'high'
    if review.kind == 'outbound_pending':
        return 'medium'
    return review.priority or 'low'


_AUTH_FAILURE_MARKERS = (
    '401', '403', 'auth', 'forbidden', 'invalid token', 'permission',
    'refresh token', 'token expired', 'unauthorized',
)


def connection_failure_events(jobs, connection_names):
    """Project durable job history into one current alert per provider/connection."""
    groups = {}
    for job in jobs:
        source = job.source.value if hasattr(job.source, 'value') else str(job.source)
        if job.connection_id is not None:
            key = f'connection:{job.connection_id}'
            title = connection_names.get(job.connection_id, source.title())
            resolve_url = f'/connections#conn-body-{job.connection_id}'
        elif source in {'trakt', 'simkl', 'mdblist'}:
            key = f'provider:{source}'
            title = source.title() if source != 'mdblist' else 'MDBList'
            resolve_url = f'/connections#{source}-body'
        else:
            continue
        group = groups.setdefault(key, {
            'title': title, 'provider': source, 'resolve_url': resolve_url,
            'failures': [], 'closed': False,
        })
        if group['closed']:
            continue
        if job.status == SyncStatus.completed:
            group['closed'] = True
        elif job.status == SyncStatus.failed:
            group['failures'].append(job)

    results = []
    for key, group in groups.items():
        failures = group['failures']
        if not failures:
            continue
        error = (failures[0].error_message or '').lower()
        auth_failure = any(marker in error for marker in _AUTH_FAILURE_MARKERS)
        if not auth_failure and len(failures) < 2:
            continue
        title = group['title']
        message = (f'{title} needs authorization before AnyList can sync again.' if auth_failure else
            f'{title} has failed to sync repeatedly. Check the connection and retry it.')
        results.append({
            'id': f'connection-failure:{key}', 'kind': 'connection_failure',
            'state': 'pending', 'provider': group['provider'], 'message': message,
            'previous_status': None, 'proposed_status': None, 'previous_score': None,
            'proposed_score': None, 'season_number': None, 'priority': 'high',
            'dismissible': False, 'payload': {
                'title': title, 'resolve_url': group['resolve_url'],
                'reason': 'authorization' if auth_failure else 'repeated_failure',
            }, 'media': None, 'created_at': failures[0].updated_at,
        })
    return results
