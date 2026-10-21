"""Private game state, with one serialized write boundary per account.

Catalogue and Steam identities remain public metadata. No provider call is made
while holding a tracking lock; reads and writes never infer missing progress.
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, select

from core.game_types import capabilities
from models import User
from models.catalogue import CatalogueEntity, CatalogueRelationship
from models.games import (GameAchievement, GameAchievementUnlock, GameActivity,
                          GameChildState, GameEntry, GameFavorite)
from schemas_games import AchievementSummary, GameEntryOut, GameSubject


async def lock_owner(db, user_id):
    # Includes first insert, child save and base removal, so neither a missing
    # row nor a concurrent first addition can bypass the same-owner invariant.
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())


async def work(db, work_id):
    row = await db.get(CatalogueEntity, work_id)
    if row is None or row.kind != 'game':
        raise HTTPException(404, 'Game not found')
    return row


async def base_work_id(db, entity):
    cls = capabilities(entity.attributes).game_class
    ids = list((await db.scalars(select(CatalogueRelationship.target_id).where(
        CatalogueRelationship.source_id == entity.id,
        CatalogueRelationship.relation == cls).distinct())).all())
    if len(ids) != 1:
        raise HTTPException(409, 'A verified base game is required')
    base = await work(db, ids[0])
    if not capabilities(base.attributes).independent:
        raise HTTPException(409, 'A verified independently playable base game is required')
    return base.id


def achievement_metadata(entity):
    info = (entity.attributes or {}).get('achievement_catalogue') or {}
    total = info.get('total')
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        total = None
    availability = info.get('status', 'unknown')
    if availability not in {'unknown', 'partial', 'complete', 'none'}:
        availability = 'unknown'
    return availability, total, bool(info.get('definitions_complete'))


def achievement_summary(entity, row=None, exact_count=0):
    availability, total, definitions = achievement_metadata(entity)
    mode = row.achievement_mode if isinstance(row, GameEntry) else None
    count = (exact_count if mode == 'targeted' else row.achievement_count) if mode else None
    percentage = None
    if mode == 'percentage':
        percentage = row.achievement_percentage
    elif count is not None and total is not None and 0 <= count <= total and total > 0:
        percentage = count / total * 100
    return AchievementSummary(availability=availability, total=total,
        definitions_available=definitions, mode=mode, count=count,
        percentage=percentage, exact_count=exact_count)


def present(entity, row=None, *, owner=True, favorite=False, exact_count=0):
    attrs = entity.attributes or {}
    year = str(attrs.get('release_date') or '')[:4]
    independent = isinstance(row, GameEntry)
    listed = bool(row and (independent or row.base_entry_id is not None))
    # A retained child is an owner-only editor prefill, never public list state.
    if not listed and not owner:
        row = None
    eligible = bool(independent and row.status == 'completed' and row.finish_date is not None
        and row.completion_seconds is not None
        and (row.total_seconds is None or row.total_seconds >= row.completion_seconds))
    return GameEntryOut(subject=GameSubject(catalogue_id=entity.id), title=entity.name,
        year=year if len(year) == 4 and year.isdigit() else None,
        genres=[genre for genre in attrs.get('genres') or [] if isinstance(genre, str)],
        image_url=entity.image_url, game_class=capabilities(entity.attributes).game_class,
        base_work_id=row.base_work_id if isinstance(row, GameChildState) else None,
        listed=listed, status=row.status if row else None, score=row.score if row else None,
        notes=row.notes if owner and independent else None,
        start_date=row.start_date if row else None, finish_date=row.finish_date if row else None,
        total_seconds=row.total_seconds if independent else None,
        completion_seconds=row.completion_seconds if independent else None,
        completion_observed_at=row.completion_observed_at if independent else None,
        completion_source=row.completion_source if independent else None,
        completion_comparison_eligible=eligible,
        replay_count=row.replay_count if independent else None,
        favorite=favorite, revision=row.revision if owner and row else None,
        updated_at=row.updated_at if row else None,
        achievements=achievement_summary(entity, row, exact_count))


async def states(db, user_id, work_ids, *, owner=True):
    ids = set(work_ids)
    if not ids:
        return []
    entities = (await db.scalars(select(CatalogueEntity).where(
        CatalogueEntity.id.in_(ids), CatalogueEntity.kind == 'game'))).all()
    entries = {r.work_id: r for r in (await db.scalars(select(GameEntry).where(
        GameEntry.user_id == user_id, GameEntry.work_id.in_(ids)))).all()}
    children = {r.work_id: r for r in (await db.scalars(select(GameChildState).where(
        GameChildState.user_id == user_id, GameChildState.work_id.in_(ids)))).all()}
    favorites = set((await db.scalars(select(GameFavorite.work_id).where(
        GameFavorite.user_id == user_id, GameFavorite.work_id.in_(ids)))).all())
    exact = dict((await db.execute(select(GameAchievementUnlock.work_id, func.count()).where(
        GameAchievementUnlock.user_id == user_id, GameAchievementUnlock.work_id.in_(ids)
    ).group_by(GameAchievementUnlock.work_id))).all())
    return [present(e, entries.get(e.id) or children.get(e.id), owner=owner,
        favorite=e.id in favorites, exact_count=exact.get(e.id, 0)) for e in entities]


def require_revision(row, revision):
    if row is not None and revision != row.revision:
        raise HTTPException(409, {'code': 'stale_game_entry', 'revision': row.revision})
    if row is None and revision is not None:
        raise HTTPException(409, {'code': 'game_entry_removed'})


def apply_common(row, patch, previous_status):
    fields = patch.model_fields_set
    if 'status' in fields and patch.status is None:
        raise HTTPException(422, 'status cannot be null')
    for name in ('status', 'score', 'start_date', 'finish_date'):
        if name in fields:
            setattr(row, name, getattr(patch, name))
    # Explicit null dates denote unknown historical dates and suppress defaults.
    if row.status != previous_status:
        if row.status == 'playing' and row.start_date is None and 'start_date' not in fields:
            row.start_date = date.today()
        if row.status == 'completed' and row.finish_date is None and 'finish_date' not in fields:
            row.finish_date = date.today()


async def apply_achievements(db, entity, row, patch):
    fields = patch.model_fields_set
    progress_fields = {'achievement_mode', 'achievement_count', 'achievement_percentage', 'unlocked_ids', 'unlock_updates'}
    if not fields & progress_fields:
        return
    mode = patch.achievement_mode if 'achievement_mode' in fields else row.achievement_mode
    if mode is None:
        if fields & {'achievement_count', 'achievement_percentage', 'unlocked_ids', 'unlock_updates'}:
            raise HTTPException(422, 'Choose an achievement tracking mode')
        row.achievement_mode = row.achievement_count = row.achievement_percentage = None
        return  # Exact evidence survives clearing aggregate input and mode changes.
    _, total, definitions = achievement_metadata(entity)
    if mode in {'count', 'targeted'}:
        if total is None or (mode == 'targeted' and not definitions):
            raise HTTPException(409, {'code': 'achievement_metadata_unavailable'})
        if patch.expected_achievement_total != total:
            raise HTTPException(409, {'code': 'achievement_catalogue_changed', 'total': total})
    if row.achievement_mode == 'targeted' and mode != 'targeted' and not patch.confirm_simplified:
        raise HTTPException(409, {'code': 'confirm_simplified_tracking'})
    if mode == 'targeted':
        if fields & {'achievement_count', 'achievement_percentage'}:
            raise HTTPException(409, {'code': 'confirm_simplified_tracking'})
        if patch.unlocked_ids is not None:
            if patch.unlock_updates is not None:
                raise HTTPException(422, 'Choose achievement replacements or individual updates')
            selected = set(patch.unlocked_ids)
            valid = set((await db.scalars(select(GameAchievement.id).where(
                GameAchievement.work_id == entity.id, GameAchievement.id.in_(selected)))).all())
            if selected != valid:
                raise HTTPException(422, 'Achievements must belong to this game')
            # Future imported evidence is not discarded by a manual mode switch.
            await db.execute(delete(GameAchievementUnlock).where(
                GameAchievementUnlock.user_id == row.user_id, GameAchievementUnlock.work_id == entity.id,
                GameAchievementUnlock.source == 'manual', GameAchievementUnlock.achievement_id.not_in(selected)))
            existing = set((await db.scalars(select(GameAchievementUnlock.achievement_id).where(
                GameAchievementUnlock.user_id == row.user_id, GameAchievementUnlock.work_id == entity.id))).all())
            db.add_all([GameAchievementUnlock(user_id=row.user_id, work_id=entity.id, achievement_id=i)
                        for i in selected - existing])
        elif patch.unlock_updates is not None:
            updates = patch.unlock_updates
            valid = set((await db.scalars(select(GameAchievement.id).where(
                GameAchievement.work_id == entity.id, GameAchievement.id.in_(updates)))).all())
            if valid != set(updates):
                raise HTTPException(422, 'Achievements must belong to this game')
            await db.execute(delete(GameAchievementUnlock).where(
                GameAchievementUnlock.user_id == row.user_id, GameAchievementUnlock.work_id == entity.id,
                GameAchievementUnlock.source == 'manual', GameAchievementUnlock.achievement_id.in_([
                    identifier for identifier, selected in updates.items() if not selected])))
            existing = set((await db.scalars(select(GameAchievementUnlock.achievement_id).where(
                GameAchievementUnlock.user_id == row.user_id, GameAchievementUnlock.work_id == entity.id))).all())
            db.add_all([GameAchievementUnlock(user_id=row.user_id, work_id=entity.id, achievement_id=i)
                       for i, selected in updates.items() if selected and i not in existing])
        row.achievement_count = row.achievement_percentage = None
    elif mode == 'count':
        if patch.unlocked_ids is not None or patch.unlock_updates is not None or 'achievement_percentage' in fields:
            raise HTTPException(422, 'Count tracking accepts only a count')
        count = patch.achievement_count if 'achievement_count' in fields else row.achievement_count
        if count is None or count > total:
            raise HTTPException(422, 'Choose an unlocked count within the known total')
        row.achievement_count, row.achievement_percentage = count, None
    else:
        if patch.unlocked_ids is not None or patch.unlock_updates is not None or 'achievement_count' in fields:
            raise HTTPException(422, 'Percentage tracking accepts only a percentage')
        percentage = patch.achievement_percentage if 'achievement_percentage' in fields else row.achievement_percentage
        if percentage is None:
            raise HTTPException(422, 'Choose an achievement percentage')
        row.achievement_percentage, row.achievement_count = percentage, None
    row.achievement_mode = mode


async def save_entry(db, user_id, work_id, patch):
    await lock_owner(db, user_id)
    entity = await work(db, work_id)
    if not capabilities(entity.attributes).independent:
        raise HTTPException(422, 'This work is not an independently trackable game')
    row = await db.scalar(select(GameEntry).where(GameEntry.user_id == user_id, GameEntry.work_id == work_id))
    require_revision(row, patch.revision)
    previous = row.status if row else None
    if row is None:
        row = GameEntry(user_id=user_id, work_id=work_id, status='planning', revision=0)
        db.add(row)
    apply_common(row, patch, previous)
    fields = patch.model_fields_set
    for name in ('notes', 'total_seconds', 'replay_count'):
        if name in fields:
            setattr(row, name, getattr(patch, name))
    if 'completion_seconds' in fields:
        if row.status != 'completed':
            raise HTTPException(422, 'First completion time can be edited while Completed is selected')
        row.completion_seconds = patch.completion_seconds
        row.completion_observed_at = datetime.now(timezone.utc).replace(tzinfo=None) if patch.completion_seconds is not None else None
        row.completion_source = 'manual' if patch.completion_seconds is not None else None
        if row.total_seconds is None and patch.completion_seconds is not None:
            row.total_seconds = patch.completion_seconds
    elif row.status == 'completed' and previous != 'completed' and row.completion_seconds is None:
        # Only a new completion observed today can capture the current actual
        # total. Historical/imported first-completion measurements stay unknown.
        if row.finish_date == date.today() and row.total_seconds is not None:
            row.completion_seconds = row.total_seconds
            row.completion_observed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            row.completion_source = 'current_total'
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()  # Owner FK for exact achievements; all changes still atomic.
    await apply_achievements(db, entity, row, patch)
    if 'favorite' in fields:
        if patch.favorite is None:
            raise HTTPException(422, 'favorite cannot be null')
        favorite = await db.get(GameFavorite, (user_id, work_id))
        if patch.favorite and favorite is None:
            db.add(GameFavorite(user_id=user_id, work_id=work_id))
        elif not patch.favorite and favorite:
            await db.delete(favorite)
    db.add(GameActivity(user_id=user_id, work_id=work_id, status=row.status, score=row.score,
        payload={'revision': row.revision, 'changed': sorted(fields - {'revision', 'confirm_simplified', 'expected_achievement_total'})}))
    await db.commit()
    return (await states(db, user_id, [work_id]))[0]


async def save_child(db, user_id, work_id, patch, *, listed):
    await lock_owner(db, user_id)
    entity = await work(db, work_id)
    caps = capabilities(entity.attributes)
    if not caps.playable_child or (listed and not caps.expansion_page):
        raise HTTPException(422, 'Only a verified expansion can have a child list entry')
    base_id = await base_work_id(db, entity)
    base_entry = await db.scalar(select(GameEntry).where(GameEntry.user_id == user_id, GameEntry.work_id == base_id))
    if base_entry is None:
        raise HTTPException(409, {'code': 'base_game_required', 'base_work_id': base_id})
    row = await db.scalar(select(GameChildState).where(GameChildState.user_id == user_id, GameChildState.work_id == work_id))
    require_revision(row, patch.revision)
    previous = row.status if row else None
    if row is None:
        row = GameChildState(user_id=user_id, work_id=work_id, base_work_id=base_id, status='planning', revision=0)
        db.add(row)
    if row.base_work_id != base_id:
        raise HTTPException(409, 'Saved child state belongs to another base game')
    # Ordinary DLC can have personal state in the base tab, but no membership.
    row.base_entry_id = base_entry.id if listed else None
    apply_common(row, patch, previous)
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    return (await states(db, user_id, [work_id]))[0]


async def remove_entry(db, user_id, work_id, revision):
    await lock_owner(db, user_id)
    row = await db.scalar(select(GameEntry).where(GameEntry.user_id == user_id, GameEntry.work_id == work_id))
    require_revision(row, revision)
    if row:
        # SET NULL retains child facts; revision changes invalidate open editors.
        children = (await db.scalars(select(GameChildState).where(
            GameChildState.user_id == user_id, GameChildState.base_work_id == work_id))).all()
        for child in children:
            child.base_entry_id = None
            child.revision += 1
            child.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.flush()
        await db.delete(row)
        await db.execute(delete(GameActivity).where(GameActivity.user_id == user_id, GameActivity.work_id == work_id))
    await db.execute(delete(GameFavorite).where(GameFavorite.user_id == user_id, GameFavorite.work_id == work_id))
    await db.commit()
