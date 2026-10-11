"""Read-only local diagnostics. Candidate duplicates are never auto-merged."""
from datetime import timedelta, timezone

from sqlalchemy import select, text

from core.statistics_snapshots import CONTRACT_VERSION, utcnow
from models.sync import SyncJob, SyncStatus

JOB_TYPE = 'data_health'
LOCK_KEY = 714903421
STALE_AFTER = timedelta(minutes=15)
NAMED_ROLE = r"regexp_replace(regexp_replace(coalesce(c.character_label,''), '\s*\((voice|uncredited|archive (footage|sound))\)\s*', ' ', 'gi'), '[^[:alnum:]_]', '', 'g')<>''"
HAS_GENRES = "EXISTS (SELECT 1 FROM (SELECT * FROM jsonb_each(legacy) UNION ALL SELECT * FROM jsonb_each(attrs)) v WHERE v.key='genres' AND jsonb_typeof(v.value)='array' AND v.value<>'[]'::jsonb)"

# One cohort shared by all title checks. Prefer an explicit, kind-checked bridge;
# also inspect verified identities so disagreements cannot look like completeness.
COHORT = """
WITH tracked AS (
 SELECT DISTINCT m.* FROM media m JOIN tracked_entries t ON t.media_id=m.id
 WHERE m.media_type::text IN ('movie','series')
), roots AS (
 SELECT m.id, e.id AS entity_id FROM tracked m JOIN catalogue_legacy_links l ON l.media_id=m.id
 JOIN catalogue_entities e ON e.id=l.entity_id AND e.kind=m.media_type::text
 UNION
 SELECT m.id, e.id FROM tracked m JOIN catalogue_identities i ON
 (i.namespace='tmdb.'||m.media_type::text AND i.external_id=m.tmdb_id::text) OR
 (i.namespace='tvdb.'||m.media_type::text AND i.external_id=m.tvdb_id::text)
 JOIN catalogue_entities e ON e.id=i.entity_id AND e.kind=m.media_type::text
), resolved AS (SELECT id, min(entity_id) AS entity_id, count(*) AS root_count FROM roots GROUP BY id),
 titles AS (
 SELECT m.id,m.title AS name,m.media_type,m.tmdb_id,m.tvdb_id,m.imdb_id,m.poster_path,
 r.root_count, e.id AS entity_id, e.image_url,
 coalesce(m.tmdb_data,'{}'::jsonb) AS legacy, coalesce(e.attributes,'{}'::jsonb) AS attrs
 FROM tracked m LEFT JOIN resolved r ON r.id=m.id
 LEFT JOIN catalogue_entities e ON e.id=r.entity_id AND r.root_count=1
), tracked_credits AS (
 SELECT c.* FROM catalogue_credits c WHERE c.work_id IN (SELECT entity_id FROM titles)
)
"""

# Counts use records/roles/groups as indicated, not repeated user memberships.
CHECKS = [
 ('identity_conflicts', 'Conflicting title identities', 'Provider identities point to different catalogue titles. Review before changing mappings.', 'warning', 'titles', None,
  "SELECT id,name,'Review identity mappings' AS detail FROM titles WHERE root_count>1"),
 ('unmatched_titles', 'Titles without a catalogue match', 'Tracked movies and series without a verified catalogue identity.', 'warning', 'titles', 'metadata',
  "SELECT id,name,CASE WHEN tmdb_id IS NULL AND tvdb_id IS NULL THEN 'No verified TMDB or TVDB ID' ELSE 'Verified ID available' END AS detail FROM titles WHERE entity_id IS NULL AND coalesce(root_count,0)=0"),
 ('genres', 'Missing genres', 'Tracked titles with no genre metadata in either local source.', 'warning', 'titles', 'metadata',
  f"SELECT id,name,'' AS detail FROM titles WHERE NOT {HAS_GENRES}"),
 ('country', 'Missing country', 'Tracked titles without origin or production country metadata.', 'warning', 'titles', 'metadata',
  "SELECT id,name,'' AS detail FROM titles WHERE NOT EXISTS (SELECT 1 FROM (SELECT * FROM jsonb_each(legacy) UNION ALL SELECT * FROM jsonb_each(attrs)) v WHERE v.key IN ('countries','origin_countries','origin_country','production_countries') AND v.value NOT IN ('[]'::jsonb,'null'::jsonb,'\"\"'::jsonb,'{}'::jsonb))"),
 ('posters', 'Missing posters', 'Tracked titles with no catalogue image or legacy poster.', 'warning', 'titles', 'metadata',
  "SELECT id,name,'' AS detail FROM titles WHERE nullif(image_url,'') IS NULL AND nullif(poster_path,'') IS NULL"),
 ('cast', 'Missing cast', 'Matched titles with no actor credits. Some providers do not publish cast.', 'warning', 'titles', 'metadata',
  "SELECT id,name,'' AS detail FROM titles t WHERE entity_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tracked_credits c WHERE c.work_id=t.entity_id AND c.role='actor')"),
 ('staff', 'Missing staff', 'Matched titles with no non-acting person credits.', 'warning', 'titles', 'metadata',
  "SELECT id,name,'' AS detail FROM titles t WHERE entity_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id AND p.kind='person' WHERE c.work_id=t.entity_id AND lower(c.role) NOT IN ('actor','acting','voice actor','voice acting','guest star','gueststar','self') AND lower(coalesce(c.role_label,c.role)) NOT IN ('actor','acting','voice actor','voice acting','guest star','gueststar','self'))"),
 ('studios', 'Missing studios', 'Matched titles with no production company credits.', 'warning', 'titles', 'metadata',
  "SELECT id,name,'' AS detail FROM titles t WHERE entity_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id AND p.kind='organization' WHERE c.work_id=t.entity_id AND c.role='producer')"),
 ('character_links', 'Unmapped character roles', 'Named actor credits on tracked titles without a derived character link.', 'warning', 'credits', 'characters',
  f"SELECT c.id,p.name, e.name || ' — ' || c.character_label AS detail FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id JOIN catalogue_entities e ON e.id=c.work_id WHERE c.role='actor' AND {NAMED_ROLE} AND c.screen_character_id IS NULL"),
 ('character_names', 'Actors without a named character', 'Provider credits sometimes say only “voice” or supply no character name. Those credits cannot form a named character mapping.', 'info', 'performances', None,
  f"SELECT min(c.id)::int AS id,p.name,e.name AS detail FROM tracked_credits c JOIN catalogue_entities p ON p.id=c.contributor_id JOIN catalogue_entities e ON e.id=c.work_id WHERE c.role='actor' AND NOT EXISTS (SELECT 1 FROM tracked_credits named WHERE named.work_id=c.work_id AND named.contributor_id=c.contributor_id AND named.role='actor' AND {NAMED_ROLE.replace('c.character_label','named.character_label')}) GROUP BY c.work_id,c.contributor_id,p.name,e.name"),
 ('character_images', 'Characters without artwork', 'Named roles without artwork from this title or another title for the same actor and role. Posters remain available as fallback.', 'info', 'roles', 'metadata',
  "SELECT DISTINCT sc.id,p.name,sc.name AS detail FROM tracked_credits c JOIN catalogue_screen_characters sc ON sc.id=c.screen_character_id JOIN catalogue_entities p ON p.id=sc.actor_id WHERE NOT EXISTS (SELECT 1 FROM catalogue_credits donor WHERE donor.screen_character_id=sc.id AND donor.character_image_url LIKE 'https://%')"),
 ('history_runtime', 'Missing watched runtimes', 'Completed movie or episode history without a duration. Series averages can still provide an estimate.', 'warning', 'media', 'metadata',
  "SELECT m.id,m.title AS name,'Movie / episode duration' AS detail FROM media m WHERE m.media_type::text IN ('movie','episode') AND coalesce(m.runtime,0)<=0 AND CASE WHEN coalesce(m.tmdb_data->>'runtime','') ~ '^[0-9]+$' THEN (m.tmdb_data->>'runtime')::numeric ELSE 0 END<=0 AND EXISTS (SELECT 1 FROM watch_events w WHERE w.media_id=m.id AND w.completed)"),
 ('history_dates', 'Unknown historical watch dates', 'History imported without an actual watch date. Metadata repair cannot recover personal dates.', 'info', 'events', None,
  "SELECT w.id,m.title AS name,'Actual watch date unavailable' AS detail FROM watch_events w JOIN media m ON m.id=w.media_id WHERE w.completed AND w.watched_at IS NULL"),
 ('estimated_dates', 'Estimated watch dates', 'Estimated or provisional dates remain marked as estimates until an authoritative history source supplies an actual date.', 'info', 'events', None,
  "SELECT w.id,m.title AS name,'Estimated date retained' AS detail FROM watch_events w JOIN media m ON m.id=w.media_id WHERE w.completed AND (w.date_inferred OR w.date_shared OR w.provisional)"),
 ('planned_runtime', 'Missing planned runtimes', 'Planning-list movies or released regular episodes without a duration. Future episode runtimes may not be available yet.', 'warning', 'media', 'metadata',
  "SELECT m.id,m.title AS name,'Planning duration unavailable' AS detail FROM media m LEFT JOIN shows s ON s.id=m.show_id WHERE m.media_type::text IN ('movie','episode') AND coalesce(m.runtime,0)<=0 AND CASE WHEN coalesce(m.tmdb_data->>'runtime','') ~ '^[0-9]+$' THEN (m.tmdb_data->>'runtime')::numeric ELSE 0 END<=0 AND EXISTS (SELECT 1 FROM tracked_entries t JOIN media title ON title.id=t.media_id WHERE t.status='planning' AND (title.id=m.id OR (m.media_type::text='episode' AND m.season_number>0 AND (m.release_date<=to_char(timezone('UTC',now()),'YYYY-MM-DD') OR m.tmdb_data->>'tracking_import_released'='true') AND title.media_type::text='series' AND ((title.tmdb_id IS NOT NULL AND title.tmdb_id=s.tmdb_id) OR (title.tvdb_id IS NOT NULL AND title.tvdb_id=s.tvdb_id)))))"),
 ('orphan_episodes', 'Episodes without a series', 'Watched episodes without a parent series. Review provider identity before relinking.', 'warning', 'media', None,
  "SELECT m.id,m.title AS name,'Parent series missing' AS detail FROM media m WHERE m.media_type::text='episode' AND m.show_id IS NULL AND EXISTS (SELECT 1 FROM watch_events w WHERE w.media_id=m.id AND w.completed)"),
 ('duplicate_media', 'Possible duplicate media', 'Multiple rows share an exact provider ID and media type. Review before merging; watch events are preserved.', 'warning', 'groups', None,
  "SELECT min(id)::int AS id,min(title) AS name, provider || ': ' || external_id || ' · IDs ' || string_agg(id::text,', ' ORDER BY id) AS detail FROM (SELECT id,title,media_type, 'TMDB' AS provider,tmdb_id::text AS external_id FROM media WHERE tmdb_id IS NOT NULL UNION ALL SELECT id,title,media_type,'TVDB',tvdb_id::text FROM media WHERE tvdb_id IS NOT NULL UNION ALL SELECT id,title,media_type,'IMDb',imdb_id FROM media WHERE nullif(imdb_id,'') IS NOT NULL) ids GROUP BY media_type,provider,external_id HAVING count(*)>1"),
 ('duplicate_people', 'People sharing a name', 'Same-name catalogue people are candidates for review, not proof of a duplicate identity.', 'info', 'groups', None,
  "SELECT min(id)::int AS id,min(name) AS name,'Catalogue IDs ' || string_agg(id::text,', ' ORDER BY id) AS detail FROM catalogue_entities WHERE kind='person' AND nullif(trim(name),'') IS NOT NULL GROUP BY lower(trim(name)) HAVING count(*)>1"),
 ('provider_failures', 'Provider enrichment failures', 'Failed metadata requests retain last-good data and provider retry delays. Metadata repair covers tracked screen titles; other providers retry on their normal schedule.', 'warning', 'requests', None,
  "SELECT s.id,coalesce(e.name,s.provider || ' ' || s.kind) AS name,'Request failed; retry follows provider backoff' AS detail FROM catalogue_metadata_snapshots s LEFT JOIN catalogue_entities e ON e.id=s.entity_id WHERE s.error_code IS NOT NULL"),
 ('statistics_errors', 'Statistics calculation failures', 'Accounts with a failed calculation. The last successful snapshot is retained.', 'warning', 'accounts', 'statistics',
  "SELECT s.user_id AS id,'Account ' || s.user_id AS name,'Last successful snapshot retained' AS detail FROM user_stats_state s WHERE s.error_code IS NOT NULL"),
 ('statistics_missing', 'Missing or incompatible statistics', 'Accounts with tracking or history that need a current statistics snapshot.', 'warning', 'accounts', 'statistics',
  "SELECT u.id,'Account ' || u.id AS name,'Current snapshot unavailable' AS detail FROM users u LEFT JOIN user_stats_state s ON s.user_id=u.id LEFT JOIN user_stats_snapshots p ON p.id=s.active_snapshot_id WHERE (EXISTS (SELECT 1 FROM tracked_entries t WHERE t.user_id=u.id) OR EXISTS (SELECT 1 FROM watch_events w WHERE w.user_id=u.id AND w.completed)) AND (p.id IS NULL OR p.contract_version<>:contract)"),
 ('statistics_scheduled', 'Scheduled statistics updates', 'Changed data waiting for the normal daily refresh is expected. A refresh here is a one-off operation.', 'info', 'accounts', 'statistics',
  "SELECT s.user_id AS id,'Account ' || s.user_id AS name,'Waiting for scheduled refresh' AS detail FROM user_stats_state s JOIN user_stats_snapshots p ON p.id=s.active_snapshot_id WHERE s.error_code IS NULL AND p.contract_version=:contract AND (s.dirty OR p.source_revision<>s.source_revision OR p.metadata_revision<>(SELECT revision FROM stats_metadata_revision WHERE id=1))"),
 ('expired_leases', 'Interrupted statistics calculations', 'Expired calculation leases can be reclaimed by a statistics refresh.', 'warning', 'accounts', 'statistics',
  "SELECT user_id AS id,'Account ' || user_id AS name,'Calculation lease expired' AS detail FROM user_stats_state WHERE lease_token IS NOT NULL AND (lease_until IS NULL OR lease_until<timezone('UTC',now()))"),
]


def job_view(job):
    if job is None:
        return None
    status = job.status.value
    if status in ('pending', 'running') and job.updated_at < utcnow() - STALE_AFTER:
        status = 'interrupted'
    stats = job.stats or {}
    return {'id': job.id, 'action': stats.get('action', 'metadata'), 'status': status,
            'step': job.current_step, 'completed_steps': job.processed_items,
            'total_steps': job.total_items, 'results': stats.get('results', {}),
            'error': 'Repair did not finish. Completed steps were preserved; retry is safe.' if status in ('failed', 'interrupted') else None,
            'updated_at': job.updated_at.replace(tzinfo=timezone.utc)}


async def latest_job(db):
    return await db.scalar(select(SyncJob).where(SyncJob.job_type == JOB_TYPE).order_by(SyncJob.id.desc()).limit(1))


async def report(db):
    # A single local SQL snapshot, no provider calls and no personal payload reads.
    union = ' UNION ALL '.join(f"SELECT '{c[0]}' AS code, q.* FROM ({c[6]}) q" for c in CHECKS)
    query = COHORT + f""", issues AS ({union}), ranked AS (
     SELECT *,row_number() OVER (PARTITION BY code ORDER BY id) AS position,
     count(*) OVER (PARTITION BY code) AS total FROM issues)
     SELECT code,max(total) AS total,jsonb_agg(jsonb_build_object('id',id,'name',name,'detail',detail)
     ORDER BY id) AS examples FROM ranked WHERE position<=8 GROUP BY code"""
    rows = {r['code']: r for r in (await db.execute(text(query), {'contract': CONTRACT_VERSION})).mappings()}
    checks = []
    for code, label, description, severity, unit, action, _ in CHECKS:
        row = rows.get(code, {})
        examples = row.get('examples', [])
        if code in ('identity_conflicts','unmatched_titles','genres','country','posters','cast','staff','studios','history_runtime','planned_runtime','orphan_episodes','duplicate_media'):
            for example in examples:
                example['href'] = f"/title/{example['id']}"
        checks.append({'code': code, 'label': label, 'description': description, 'severity': severity,
                       'unit': unit, 'action': action, 'count': row.get('total', 0), 'examples': examples})
    totals = (await db.execute(text("SELECT (SELECT count(DISTINCT t.media_id) FROM tracked_entries t JOIN media m ON m.id=t.media_id WHERE m.media_type::text IN ('movie','series')) AS tracked, (SELECT count(*) FROM catalogue_entities) AS catalogue"))).one()
    return {'checked_at': utcnow().replace(tzinfo=timezone.utc), 'tracked_titles': totals.tracked,
            'catalogue_records': totals.catalogue, 'checks': checks, 'job': job_view(await latest_job(db))}


async def enqueue(db, user_id, action):
    """Serialize enqueues across workers with the same lock held by the runner."""
    if not await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_KEY}):
        return None
    active = list(await db.scalars(select(SyncJob).where(SyncJob.job_type == JOB_TYPE,
        SyncJob.status.in_([SyncStatus.pending, SyncStatus.running])).with_for_update()))
    if any(job.updated_at >= utcnow() - STALE_AFTER for job in active):
        return None
    for job in active:
        job.status = SyncStatus.failed
        job.error_message = 'interrupted'
    job = SyncJob(user_id=user_id, source='tmdb', job_type=JOB_TYPE, status=SyncStatus.pending,
                  total_items=1 if action == 'statistics' else 2 if action == 'characters' else 5,
                  processed_items=0, stats={'action': action, 'results': {}},
                  created_at=utcnow(), updated_at=utcnow())
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job
