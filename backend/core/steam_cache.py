"""Public app evidence and viewer-scoped progress have separate durable stores."""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from core.steam import SteamError
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.steam import SteamPlayerGame

PUBLIC_KIND = 'steam_import_app'


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def progress_view(row, minutes, stamp):
    if row is None:
        return {'achievement_count': None, 'achievement_total': None, 'achievement_state': 'loading'}
    blocked = bool(row.retry_at and row.retry_at > stamp)
    return {'achievement_count': row.achievement_count, 'achievement_total': row.achievement_total,
        'achievement_state': 'ready' if row.achievement_count is not None else 'unavailable' if blocked else 'loading',
        **({'achievement_checked_at': row.fetched_at.isoformat()} if row.fetched_at else {})}


def progress_due(row, game, stamp, total=None):
    return not (row.retry_at and row.retry_at > stamp or row.lease_until and row.lease_until > stamp) and (
        not row.fetched_at or row.fetched_at <= stamp - timedelta(days=1) or row.observed_playtime != game['playtime_minutes']
        or game.get('last_played') is not None and game['last_played'] != row.observed_last_played
        or total is not None and row.achievement_total != total)


async def read_progress(db, user_id, steam_id, games):
    rows = {row.app_id: row for row in await db.scalars(select(SteamPlayerGame).where(
        SteamPlayerGame.user_id == user_id, SteamPlayerGame.steam_id == steam_id,
        SteamPlayerGame.app_id.in_([game['app_id'] for game in games])))}
    stamp = now()
    totals = dict((await db.execute(select(MetadataSnapshot.external_id, MetadataSnapshot.payload['achievement_total'].as_integer()).where(
        MetadataSnapshot.provider == 'steam', MetadataSnapshot.kind == PUBLIC_KIND,
        MetadataSnapshot.external_id.in_([game['app_id'] for game in games])))).all())
    result = []
    for game in games:
        row = rows.get(game['app_id'])
        value = {**game, **progress_view(row, game['playtime_minutes'], stamp)}
        if totals.get(game['app_id']) is not None:
            value['achievement_total'] = totals[game['app_id']]
        value['achievement_refresh_due'] = row is None or progress_due(row, game, stamp, totals.get(game['app_id']))
        result.append(value)
    return result


async def load_progress(factory, user_id, steam_id, games, source):
    """Claim bounded missing reads; provider failures never invalidate the library."""
    if not games:
        return [], 0
    stamp, token = now(), uuid.uuid4().hex
    by_app = {game['app_id']: game for game in games}
    async with factory() as db:
        await db.execute(insert(SteamPlayerGame).values([
            {'user_id': user_id, 'steam_id': steam_id, 'app_id': app} for app in by_app]).on_conflict_do_nothing())
        rows = list(await db.scalars(select(SteamPlayerGame).where(SteamPlayerGame.user_id == user_id,
            SteamPlayerGame.steam_id == steam_id, SteamPlayerGame.app_id.in_(by_app)).with_for_update(skip_locked=True)))
        totals = dict((await db.execute(select(MetadataSnapshot.external_id, MetadataSnapshot.payload['achievement_total'].as_integer()).where(
            MetadataSnapshot.provider == 'steam', MetadataSnapshot.kind == PUBLIC_KIND, MetadataSnapshot.external_id.in_(by_app)))).all())
        due = [row.app_id for row in rows if progress_due(row, by_app[row.app_id], stamp, totals.get(row.app_id))]
        for row in rows:
            if row.app_id in due:
                row.lease_token, row.lease_until = token, stamp + timedelta(minutes=2)
        await db.commit()
    results = {}
    async def one(app):
        try:
            return app, await source.achievements(steam_id, app)
        except SteamError:
            return app, None
    for offset in range(0, len(due), 4):
        # The transport still enforces the shared cross-process provider budget.
        results.update(await asyncio.gather(*(one(app) for app in due[offset:offset+4])))
    if results:
        async with factory() as db:
            rows = await db.scalars(select(SteamPlayerGame).where(SteamPlayerGame.user_id == user_id,
                SteamPlayerGame.steam_id == steam_id, SteamPlayerGame.app_id.in_(results),
                SteamPlayerGame.lease_token == token).with_for_update())
            for row in rows:
                value = results[row.app_id]
                row.lease_token = row.lease_until = None
                if value is not None and value.get('achievement_count') is not None:
                    row.achievement_count, row.achievement_total = value['achievement_count'], value.get('achievement_total')
                    row.fetched_at, row.observed_playtime, row.retry_at = now(), by_app[row.app_id]['playtime_minutes'], None
                    if by_app[row.app_id].get('last_played') is not None:
                        row.observed_last_played = by_app[row.app_id]['last_played']
                else:
                    row.retry_at = now() + timedelta(minutes=15)
            await db.commit()
        await publish_totals(factory, {app: value['achievement_total'] for app, value in results.items()
            if value is not None and value.get('achievement_total') is not None})
    async with factory() as db:
        return await read_progress(db, user_id, steam_id, games), len(due)


async def save_public(factory, games, *, release_checked=False):
    """Save names/artwork for every game, regardless of personal import selection."""
    stamp = now()
    for offset in range(0, len(games), 200):
        values = []
        for game in games[offset:offset+200]:
            payload = {key: game[key] for key in ('name', 'poster', 'release_date') if game.get(key) is not None}
            if release_checked:
                payload.update(release_date=game.get('release_date'), release_checked_at=stamp.isoformat())
            values.append({'provider': 'steam', 'kind': PUBLIC_KIND, 'external_id': game['app_id'],
                           'payload': payload, 'fetched_at': stamp})
        async with factory() as db:
            if release_checked:
                from core import catalogue
                await catalogue.write_lock(db)
                dates = {game['app_id']: game.get('release_date') for game in games[offset:offset+200]}
                rows = await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity).join(
                    CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
                    CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(dates))
                    .order_by(CatalogueEntity.id).with_for_update(of=CatalogueEntity))
                for app, work in rows:
                    release = dates.get(app)
                    if release and not (work.attributes or {}).get('release_date'):
                        catalogue.merge_fields(work, {'attributes': {'release_date': release,
                            'release_date_precision': 'day' if len(release) == 10 else 'month' if len(release) == 7 else 'year'}}, 'steam')
            statement = insert(MetadataSnapshot).values(values)
            await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot', set_={
                'payload': func.coalesce(MetadataSnapshot.payload, cast('{}', JSONB)).op('||')(statement.excluded.payload),
                'fetched_at': stamp}))
            await db.commit()


async def public_metadata(db, apps):
    """Two compact batch reads replace per-card provider lookups for known data."""
    cached = {row.external_id: dict(row.payload or {}) for row in await db.scalars(select(MetadataSnapshot).where(
        MetadataSnapshot.provider == 'steam', MetadataSnapshot.kind == PUBLIC_KIND, MetadataSnapshot.external_id.in_(apps)))}
    rows = await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity.attributes['release_date'].astext,
        CatalogueEntity.name, CatalogueEntity.image_url).join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(apps)))
    for app, release, name, image in rows:
        item = cached.setdefault(app, {})
        if release:
            item['release_date'] = release
        if name:
            item['name'] = name
        if image:
            item['poster'] = image
    return cached


def merge_total(work, app, total, stamp):
    """The denominator is public catalogue data, never a frozen user percentage."""
    from core import catalogue
    if 'attributes.achievement_catalogue' in (work.protected_fields or []):
        return
    prior = dict((work.attributes or {}).get('achievement_catalogue') or {})
    if prior.get('total') == total and prior.get('steam_app_id') == app:
        return
    definitions = bool(prior.get('definitions_complete') and prior.get('total') == total)
    prior.update(total=total, steam_app_id=app, fetched_at=stamp.isoformat(),
        status='none' if total == 0 else 'complete' if definitions else 'partial', definitions_complete=definitions,
        definitions_stale=bool(prior.get('definitions_stale') or prior.get('total') != total), total_source='player_progress')
    catalogue.merge_fields(work, {'attributes': {'achievement_catalogue': prior}}, 'steam')


async def publish_totals(factory, totals):
    if not totals:
        return
    stamp = now()
    async with factory() as db:
        from core import catalogue
        await catalogue.write_lock(db)
        rows = await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity).join(
            CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
            CatalogueIdentity.namespace == 'steam.app', CatalogueIdentity.external_id.in_(totals))
            .order_by(CatalogueEntity.id).with_for_update(of=CatalogueEntity))
        for app, work in rows:
            merge_total(work, app, totals[app], stamp)
        statement = insert(MetadataSnapshot).values([{'provider': 'steam', 'kind': PUBLIC_KIND, 'external_id': app,
            'payload': {'achievement_total': total, 'achievement_total_checked_at': stamp.isoformat()}, 'fetched_at': stamp} for app, total in totals.items()])
        await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot', set_={
            'payload': func.coalesce(MetadataSnapshot.payload, cast('{}', JSONB)).op('||')(statement.excluded.payload), 'fetched_at': stamp}))
        await db.commit()
