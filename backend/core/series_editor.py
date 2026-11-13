"""Focused catalogue and watch projection for the series editor."""
import hashlib
from datetime import date
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from core.season_completion import is_released, membership_key, release_day, season_groups
from models import Rating, WatchEvent
from models.seasons import SeasonTracking, SeriesSeason
from models.tracking import TrackedEntry


class SeasonEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    season_number: int = Field(gt=0)
    membership: str = Field(min_length=64, max_length=64)
    start_date: date | None = None
    completion_date: date | None = None
    start_preset: bool = False
    completion_preset: bool = False
    score: float | None = None


class SeriesEditorPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str | None = Field(default=None, max_length=64)
    order: str | None = Field(default=None, max_length=40)
    action: Literal['episode', 'through_season', 'position', 'catch_up', 'complete_previous'] | None = None
    episode_id: int | None = Field(default=None, gt=0)
    season_number: int | None = Field(default=None, gt=0)
    episode_count: int | None = Field(default=None, ge=0)
    watch_date: date | None = None
    seasons: list[SeasonEdit] = Field(default_factory=list, max_length=250)


class SeriesEditorContext:
    def __init__(self, data, positions, watched, cursor=None):
        self.data = data
        self.order, self.groups, self.metadata = data
        self.positions = positions
        self.watched = watched
        self.cursor = cursor
        self.today = date.today()
        self.released = [ep for _, members, _ in self.groups for ep in members if is_released(ep, self.today)]
        self.revision = hashlib.sha256(repr((self.order, [(n, [(ep.id, ep.release_date) for ep in episodes], expected)
            for n, episodes, expected in self.groups], [ep.id for ep in self.released], sorted(watched),
            self.selected_position())).encode()).hexdigest()

    def cursor_for(self, patch):
        if patch.season_number is None and patch.episode_count is None:
            return None
        if patch.action != 'position' or patch.season_number is None or patch.episode_count is None:
            raise HTTPException(422, 'Provide a season and episode count for the selected position')
        members = [ep for ep in self.released if self.positions[ep.id][0] == patch.season_number]
        if not members or patch.episode_count > len(members):
            raise HTTPException(409, 'Choose a released season and episode')
        previous = [ep for ep in self.released if self.positions[ep.id][0] < patch.season_number]
        endpoint = members[patch.episode_count-1] if patch.episode_count else (previous[-1] if previous else None)
        if patch.episode_id != (endpoint.id if endpoint else None):
            raise HTTPException(422, 'The episode does not match the selected season position')
        return {'order':self.order,'season_number':patch.season_number,'episode_count':patch.episode_count,
                'episode_id':patch.episode_id}

    def selected_position(self):
        if not isinstance(self.cursor,dict) or self.cursor.get('order') != self.order:
            return None
        try:
            patch = SeriesEditorPatch(action='position',**{k:self.cursor.get(k) for k in
                ('season_number','episode_count','episode_id')})
            cursor = self.cursor_for(patch)
            if cursor is None:
                return None
        except (HTTPException, ValueError):
            return None
        ids = [ep.id for ep in self.released]
        boundary = ids.index(cursor['episode_id'])+1 if cursor['episode_id'] in ids else 0
        return cursor if self.watched == set(ids[:boundary]) else None

    def airing(self):
        released_ids = {ep.id for ep in self.released}
        for number, members, expected in reversed(self.groups):
            released = [ep for ep in members if ep.id in released_ids]
            future = sorted((ep for ep in members if ep.id not in released_ids and
                (day := release_day(ep)) and day > self.today), key=release_day)
            if not (released and future and len(released) < expected):
                continue
            previous = next(((n, eps[-1].id) for n, eps, count in reversed(self.groups)
                if n < number and eps and count == len(eps) and all(ep.id in released_ids for ep in eps)), None)
            final_day = release_day(members[-1]) if len(members) == expected and members[-1] in future else None
            return {
                'season_number': number,
                'latest_episode': self.positions[released[-1].id][1],
                'latest_episode_id': released[-1].id,
                'next_date': release_day(future[0]).isoformat(),
                'final_date': final_day.isoformat() if final_day else None,
                'watched': any(ep.id in self.watched for ep in released),
                'previous_season': previous[0] if previous else None,
                'previous_episode_id': previous[1] if previous else None,
            }
        return None

    def resolve(self, patch, status, needs_progress):
        """Translate editor intent to a canonical watch delta under the user lock."""
        airing = self.airing()
        self.cursor_for(patch)
        if (patch.action or needs_progress) and (not self.released or patch.revision != self.revision):
            raise HTTPException(409, {'code': 'series_editor_changed', 'airing': airing})
        if needs_progress and status == 'completed' and airing and patch.action not in ('complete_previous', 'catch_up'):
            raise HTTPException(409, {'code': 'series_editor_airing', 'airing': airing})
        if needs_progress and patch.action not in (None, 'complete_previous', 'catch_up'):
            raise HTTPException(422, 'Choose a series completion action')
        action, episode_id = patch.action, patch.episode_id
        if action in ('catch_up', 'complete_previous'):
            if not airing or (action == 'complete_previous' and (airing['watched'] or not airing['previous_episode_id'])):
                raise HTTPException(409, {'code': 'series_editor_changed', 'airing': airing})
            episode_id = airing['latest_episode_id' if action == 'catch_up' else 'previous_episode_id']
        ids = [ep.id for ep in self.released]
        if action == 'position' and episode_id is None:
            return set(), None
        if action and episode_id not in ids:
            raise HTTPException(409, 'Choose a released episode in this series')
        if action == 'episode':
            return self.watched | {episode_id}, episode_id
        if action:
            target = ids.index(episode_id) + 1
            selected = set(ids[:target])
            if action != 'position':
                selected |= self.watched
            return selected, episode_id
        if needs_progress:
            return set(ids) | self.watched, ids[-1] if self.watched else None
        return self.watched, None

    def finish_targets(self, edits, series_finish=None):
        """Dates imply watched coverage, independently of the list status."""
        dates = {edit.season_number: edit.completion_date for edit in edits
                 if 'completion_date' in edit.model_fields_set and edit.completion_date is not None}
        if series_finish is not None and self.released:
            dates.setdefault(self.positions[self.released[-1].id][0], series_finish)
        return {members[-1].id: dates[number] for number, _, _ in self.groups
                if number in dates and (members := [ep for ep in self.released if self.positions[ep.id][0] == number])}


async def editor_context(db, media, user_id):
    positions = {}
    data = await season_groups(db, media, user_id, display_positions=positions)
    watched = set(await db.scalars(select(WatchEvent.media_id).where(
        WatchEvent.user_id == user_id, WatchEvent.media_id.in_(positions),
        WatchEvent.completed.is_(True)).distinct())) if positions else set()
    cursor = await db.scalar(select(TrackedEntry.season_cursor).where(TrackedEntry.user_id==user_id,
        TrackedEntry.media_id==media.id))
    return SeriesEditorContext(data, positions, watched, cursor)


async def intent_date_edits(db, media_id, user_id, context, edits, *, number, start, finish, day):
    """Preset only the season explicitly acted on; earlier coverage is undated."""
    edits = list(edits)
    if number is None or not (start or finish):
        return edits
    members = next((eps for sn, eps, _ in context.groups if sn == number), [])
    if not members:
        return edits
    saved = (await db.execute(select(SeasonTracking).join(SeriesSeason).where(
        SeriesSeason.media_id == media_id, SeriesSeason.episode_order == context.order,
        SeriesSeason.season_number == number, SeriesSeason.membership_key == membership_key(members),
        SeasonTracking.user_id == user_id))).scalar_one_or_none()
    edit = next((edit for edit in edits if edit.season_number == number), None)
    values = edit.model_dump(exclude_unset=True) if edit else {'season_number': number, 'membership': membership_key(members)}
    for field, wanted, manual in (('start_date', start, 'start_manual'), ('completion_date', finish, 'date_manual')):
        if field in values or not wanted:
            continue
        previous = getattr(saved, field) if saved else None
        if previous is not None or not (saved and getattr(saved, manual)):
            values[field] = previous or day
            values['start_preset' if field == 'start_date' else 'completion_preset'] = True
    replacement = SeasonEdit(**values)
    return [replacement if item is edit else item for item in edits] if edit else [*edits, replacement]


async def editor_summary(db, media, user_id, context):
    rows = (await db.execute(select(SeriesSeason.season_number, SeriesSeason.membership_key,
        SeasonTracking.start_date, SeasonTracking.start_source, SeasonTracking.start_manual,
        SeasonTracking.completion_date, SeasonTracking.date_source, SeasonTracking.date_manual,
        SeasonTracking.completed).join(SeasonTracking, SeasonTracking.season_id == SeriesSeason.id).where(
        SeriesSeason.media_id == media.id, SeriesSeason.episode_order == context.order,
        SeasonTracking.user_id == user_id, SeasonTracking.current.is_(True)))).mappings().all()
    saved = {(row['season_number'], row['membership_key']): dict(row) for row in rows}
    ratings = dict((await db.execute(select(Rating.season_number, Rating.rating).where(
        Rating.user_id == user_id, Rating.media_id == media.id, Rating.season_number.is_not(None),
        Rating.episode_order == (None if context.order == 'tmdb:aired' else context.order)))).all())
    released = {ep.id for ep in context.released}
    seasons = []
    for number, members, expected in context.groups:
        key = membership_key(members)
        metadata = context.metadata.get(number, {})
        season = {'season_number': number, 'membership': key,
            'name': metadata.get('name'), 'poster': metadata.get('poster_path'),
            'total': expected or len(members), 'score': ratings.get(number),
            'episodes': [{'id': ep.id, 'number': context.positions[ep.id][1], 'watched': ep.id in context.watched}
                for ep in members if ep.id in released],
            'upcoming': [{'id': ep.id, 'number': context.positions[ep.id][1]}
                for ep in members if ep.id not in released]}
        season.update(saved.get((number, key), {}))
        seasons.append(season)
    return {'order': context.order, 'revision': context.revision, 'seasons': seasons,
            'selected_position': context.selected_position(),
            'airing': context.airing(), 'ready': bool(context.released)}
