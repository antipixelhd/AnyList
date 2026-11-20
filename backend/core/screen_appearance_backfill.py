"""Acquire season cast once per show and publish compact, immutable card revisions."""

import asyncio
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import Integer, String, column, func, insert, or_, select, union, update, values
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.catalogue_providers import ProviderError
from core.credit_roles import work_credit
from models import Media
from models.catalogue import CatalogueIdentity, ContributorEvidence, MetadataSnapshot
from models.screen_appearances import ScreenActorAppearance


KIND = 'screen_season_cast'
BATCH_SEASONS = 8
FINAL = {'Ended', 'Canceled'}


def appearance_candidates(cast):
    """Prefer brief credits on older series, which can move furthest in a filmography."""
    from core.browse import anime_visible

    best = {}
    for credit in cast:
        if not isinstance(credit, dict) or credit.get('media_type') != 'tv' or credit.get('adult'):
            continue
        native, premiere, count = credit.get('id'), credit.get('first_air_date'), credit.get('episode_count')
        if (type(native) is not int or not 0 < native <= 2147483647 or not isinstance(premiere, str)
                or not '0000' < premiere <= date.today().isoformat() or not work_credit('actor', credit.get('character'))):
            continue
        episodes = count if type(count) is int and count > 0 else 10000
        rank = (episodes > 4, premiere, episodes, native)
        if native not in best or rank < best[native][0]:
            best[native] = rank, {'id': native, 'anime': not anime_visible(credit, False)}
    return [row for _, row in sorted(best.values(), key=lambda item: item[0])[:12]]


async def career_candidates(db, *, show_anime):
    evidence = ContributorEvidence
    conditions = (evidence.kind == 'person', evidence.expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
                  evidence.payload['complete'].as_boolean().is_(True))
    # Fill compact hints for preexisting evidence once, with no new person call.
    missing = ~evidence.payload.has_key('appearance_candidates')
    old = (await db.execute(select(evidence.native_id, evidence.variant, evidence.expires_at, evidence.payload['cast'])
        .where(*conditions, missing).order_by(evidence.expires_at.desc()).limit(8))).all()
    for native, variant, expires, cast in old:
        await db.execute(update(evidence).where(evidence.kind == 'person', evidence.native_id == native,
            evidence.variant == variant, evidence.expires_at == expires, missing).values(
            payload=evidence.payload.concat({'appearance_candidates': appearance_candidates(cast or [])})))
    recent = (select(evidence.payload['appearance_candidates'].label('candidates'),
        evidence.native_id, evidence.expires_at)
        .where(*conditions, evidence.payload.has_key('appearance_candidates'))
        .distinct(evidence.native_id).order_by(evidence.native_id,evidence.expires_at.desc())).subquery()
    rows = list(await db.scalars(select(recent.c.candidates)
        .order_by(recent.c.expires_at.desc(),recent.c.native_id).limit(48)))
    await db.commit()
    ordered = {}
    for rank in range(12):
        for row in rows:
            if rank < len(row):
                item = row[rank]
                if show_anime or not item['anime']:
                    ordered.setdefault(item['id'], None)
    return list(ordered)


def season_manifest(seasons):
    if not isinstance(seasons, list):
        raise ProviderError('incomplete_seasons')
    manifest = {}
    for item in seasons:
        if not isinstance(item, dict):
            raise ProviderError('incomplete_seasons')
        number = item.get('season_number')
        count = item.get('episode_count')
        if type(number) is not int or number < 0 or type(count) is not int or count < 0 or number in manifest:
            raise ProviderError('incomplete_seasons')
        release = item.get('air_date') or None
        if release:
            try:
                release = date.fromisoformat(release).isoformat()
            except (TypeError, ValueError):
                release = None
        manifest[number] = {
            'id': item.get('id'), 'count': count, 'date': release,
            'label': item.get('name') or (f'Season {number}' if number else 'Specials'),
            'poster': item.get('poster_path'),
        }
    return {str(number): item for number, item in sorted(manifest.items()) if item['count'] > 0}


def season_cast(aggregate):
    if not isinstance(aggregate, dict) or not isinstance(aggregate.get('cast'), list):
        raise ProviderError('incomplete_season_cast')
    cast = defaultdict(set)
    for person in aggregate['cast']:
        if not isinstance(person, dict):
            raise ProviderError('incomplete_season_cast')
        person_id = person.get('id')
        roles = person.get('roles')
        if type(person_id) is not int or not 0 < person_id <= 2147483647 or not isinstance(roles, list):
            raise ProviderError('incomplete_season_cast')
        if not roles:
            cast[str(person_id)] = set()
        for role in roles:
            if not isinstance(role, dict) or not isinstance(role.get('character') or '', str):
                raise ProviderError('incomplete_season_cast')
            character = (role.get('character') or '').strip()
            if work_credit('actor', character):
                cast[str(person_id)].add(character)
    return {person: sorted(roles - {''}, key=lambda role: (role.casefold(), role))
            for person, roles in cast.items()}


def projections(source):
    if not source:
        return {}
    manifest, cast = source['manifest'], source['cast']
    ordered = sorted(manifest, key=int)
    regular = [number for number in ordered if number != '0']
    first = regular[0] if regular else '0'
    appearances = defaultdict(dict)
    for number in ordered:
        for person, roles in cast[number].items():
            season = manifest[number]
            appearances[person][number] = {'label': season['label'], 'date': season['date'], 'roles': roles}
    result = {}
    for person, credited in appearances.items():
        regular_credits = [number for number in regular if number in credited]
        matches = regular_credits or ['0']
        anchor = matches[0]
        if (anchor == first and len(matches) > 1) or not manifest[anchor]['date']:
            continue
        result[person] = {
            'poster': manifest[anchor]['poster'],
            'seasons': [
                {**credited[number], **({'role_keys': [role.casefold() for role in credited[number]['roles']]}
                                       if number == anchor else {})}
                for number in matches
            ],
        }
    return result


def due(now):
    snapshot = MetadataSnapshot
    return (
        or_(snapshot.lease_until.is_(None), snapshot.lease_until <= now),
        or_(snapshot.next_attempt_at.is_(None), snapshot.next_attempt_at <= now),
        or_(snapshot.fetched_at.is_(None), snapshot.expires_at <= now,
            snapshot.error_code.is_not(None)),
    )


async def acquire(db, providers, series_id, counts):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    native = str(series_id)
    scope = (MetadataSnapshot.provider == 'tmdb', MetadataSnapshot.kind == KIND, MetadataSnapshot.external_id == native)
    await db.execute(pg_insert(MetadataSnapshot).values(provider='tmdb', kind=KIND, external_id=native)
                     .on_conflict_do_nothing())
    row = await db.scalar(select(MetadataSnapshot).where(*scope, *due(now))
                          .with_for_update().execution_options(populate_existing=True))
    if row is None:
        await db.commit()
        return
    previous = (row.payload or {}).get('published')
    pending = (row.payload or {}).get('pending')
    token = uuid.uuid4().hex
    row.lease_token = token
    row.lease_until = now + timedelta(minutes=5)
    hint = None
    if not pending and not previous:
        hint = await db.scalar(select(MetadataSnapshot.payload['seasons']).where(
            MetadataSnapshot.provider == 'tmdb', MetadataSnapshot.kind == 'series', MetadataSnapshot.external_id == native))
        if not hint:
            hint = await db.scalar(select(Media.tmdb_data['seasons']).where(Media.media_type == 'series',
                Media.tmdb_id == series_id).order_by(Media.id).limit(1))
    await db.commit()

    source = pending
    try:
        try:
            initial = sorted(season_manifest(hint), key=int) if hint else []
        except ProviderError:
            initial = []
        if previous and not source:
            known = sorted(previous['manifest'], key=int)
            # Recheck the current season when a running series is due. Older
            # seasons are reused unless the provider's episode count changes.
            initial = known[-1:]
        wanted = (initial or ['1'])[:BATCH_SEASONS] if not source else [
            number for number in source['manifest'] if number not in source['cast']]
        batch_size = BATCH_SEASONS
        for _ in range(128):
            if not wanted:
                break
            batch = wanted[:batch_size]
            counts['batches'] += 1
            try:
                raw = await providers.tmdb_season_cast(series_id, list(map(int, batch)))
            except ProviderError as error:
                if error.code != 'payload_too_large' or len(batch) == 1:
                    raise
                batch_size = len(batch) // 2
                continue
            if not isinstance(raw, dict) or raw.get('id') != series_id:
                raise ProviderError('identity_conflict')
            manifest = season_manifest(raw.get('seasons'))
            if raw.get('adult'):
                manifest = {}
            if not source:
                retained = {}
                if previous:
                    for number, season in manifest.items():
                        before = previous['manifest'].get(number)
                        if before and (before['id'], before['count']) == (season['id'], season['count']):
                            retained[number] = previous['cast'][number]
                source = {'manifest': manifest, 'cast': retained, 'status': raw.get('status')}
            elif source['manifest'] != manifest:
                # Avoid mixing cast from two different episode catalogues.
                source = None
                raise ProviderError('season_manifest_changed')
            for number in batch:
                if number in manifest:
                    source['cast'][number] = season_cast(raw.get(f'season/{number}/aggregate_credits'))
            wanted = sorted(manifest.keys() - source['cast'].keys(), key=int)
            if wanted:
                await db.refresh(row, with_for_update=True)
                if row.lease_token != token:
                    await db.commit()
                    return
                row.payload = {'published': previous, 'pending': source}
                row.lease_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
                await db.commit()
        if not source or wanted:
            raise ProviderError('incomplete_season_cast')

        before = projections(previous)
        after = projections(source)
        changes = [{'series_id': series_id, 'person_id': int(person), 'projection': after.get(person) or {}}
                   for person in sorted(before.keys() | after.keys()) if before.get(person) != after.get(person)]
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return
        if changes:
            # IDs are safe read watermarks only if all publishers commit in
            # allocation order. Hold this lock for the short publication only.
            await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(KIND))))
            await db.execute(insert(ScreenActorAppearance.__table__), changes)
        row.payload = {'published': source}
        row.coverage = {'seasons': len(source['manifest']), 'season_cards': len(after)}
        row.fetched_at = now
        row.expires_at = None if source['status'] in FINAL else now + timedelta(days=7)
        row.error_code = None
        row.next_attempt_at = None
        row.attempts = 0
        counts['published'] += 1
        counts['seasons'] += len(source['manifest'])
        counts['cards_changed'] += len(changes)
    except ProviderError as error:
        await db.rollback()
        await db.refresh(row, with_for_update=True)
        if row.lease_token != token:
            await db.commit()
            return
        row.payload = {'published': previous, 'pending': source}
        row.error_code = getattr(error, 'code', 'unavailable')
        row.attempts += 1
        backoff = max(getattr(error, 'retry_after', 0) or 0, min(86400, 3600 * 2 ** min(row.attempts - 1, 5)))
        row.next_attempt_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=backoff)
        counts['deferred'] += 1
    except asyncio.CancelledError:
        await db.rollback()
        # The durable lease expires after an interrupted worker.
        raise
    row.lease_token = None
    row.lease_until = None
    await db.commit()


async def backfill_screen_appearances(db, providers, *, limit, priority_ids=None):
    counts = dict(examined=0, batches=0, published=0, deferred=0, seasons=0, cards_changed=0)
    if not providers.tmdb_key:
        return counts
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if priority_ids is not None:
        if not priority_ids:
            return counts
        series = values(column('native', String), column('rank', Integer), name='appearance_demand').data(
            [(str(native), rank) for rank, native in enumerate(priority_ids)])
        order = (series.c.rank,)
    else:
        series = union(
            select(CatalogueIdentity.external_id.label('native')).where(CatalogueIdentity.namespace == 'tmdb.series'),
            select(Media.tmdb_id.cast(String).label('native')).where(Media.media_type == 'series', Media.tmdb_id.is_not(None)),
        ).subquery()
        order = (MetadataSnapshot.fetched_at.asc().nulls_first(), series.c.native)
    candidates = list(await db.scalars(select(series.c.native).outerjoin(MetadataSnapshot,
        (MetadataSnapshot.provider == 'tmdb') & (MetadataSnapshot.kind == KIND)
        & (MetadataSnapshot.external_id == series.c.native))
        .where(*due(now)).order_by(*order).limit(limit)))
    await db.commit()
    counts['examined'] = len(candidates)
    for native in candidates:
        await acquire(db, providers, int(native), counts)
    return counts
