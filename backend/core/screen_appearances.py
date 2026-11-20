"""Read an actor's published season cards without loading provider cast payloads."""

from sqlalchemy import func, literal, select, true

from core.statistics_genres import poster
from models.screen_appearances import ScreenActorAppearance as Appearance


async def apply_appearances(db, person_id, works, revision=None):
    series = {row['tmdb_id']: row for row in works if row['type'] == 'series' and row.get('tmdb_id')}
    if not series:
        return revision
    # Publication assigns IDs under one transaction lock. A fixed high-water
    # mark lets later pages read the same version even when a show is refreshed.
    watermark = (select(func.coalesce(func.max(Appearance.id), 0)).scalar_subquery()
                 if revision is None else literal(revision))
    cutoff = select(watermark.label('revision')).cte('appearance_cutoff')
    latest = (select(Appearance.series_id, Appearance.projection)
              .where(Appearance.person_id == person_id, Appearance.series_id.in_(series),
                     Appearance.id <= cutoff.c.revision)
              .distinct(Appearance.series_id).order_by(Appearance.series_id, Appearance.id.desc())
              .lateral())
    result = await db.execute(select(cutoff.c.revision, latest.c.series_id, latest.c.projection)
                              .select_from(cutoff.outerjoin(latest, true())))
    for revision, series_id, appearance in result:
        if not appearance:
            continue
        row = series[series_id]
        anchor = appearance['seasons'][0]
        release = anchor['date']
        row.update(
            release_date=release, year=release[:4],
            characters=anchor['roles'],
            appearance={'label': anchor['label'], 'poster': poster(appearance['poster']), 'seasons': [
                {'label': season['label'], 'year': (season['date'] or '')[:4],
                 'roles': [role for role in season['roles'] if role.casefold() not in anchor['role_keys']]}
                for season in appearance['seasons']
            ]},
        )
    return revision
