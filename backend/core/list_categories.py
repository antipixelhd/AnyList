"""Category presentation is independent of tracking and statistical semantics.

Writers hold the same account lock as game entries. Other media domains can
register their definitions without changing the storage or presentation contract.
"""
from fastapi import HTTPException
from sqlalchemy import func, select, union_all
from sqlalchemy.dialects.postgresql import insert

from models import User, UserSettings
from models.games import GameEntry, GameChildState
from schemas_list_categories import CategoryPresentation, ListCategory

DEFINITIONS = {
    'game': (
        ('playing', 'In Progress', 'Currently playing, usually toward a main objective.'),
        ('completed', 'Completed', 'Achieved your main objective.'),
        ('played', 'Played', 'Played without marking as completed, including endless games and one-off tries.'),
        ('paused', 'Paused', 'Set aside with the intention of returning.'),
        ('dropped', 'Dropped', 'Stopped with no intention of returning.'),
        ('planning', 'Planning', 'Wishlist or backlog games.'),
    ),
}
KEY = 'list_categories'


def resolve(domain='game', preferences=None, counts=None):
    definitions = {row[0]: row for row in DEFINITIONS[domain]}
    stored = ((preferences or {}).get(KEY) or {}).get(domain) or {}
    order = list(dict.fromkeys(key for key in stored.get('order', []) if key in definitions))
    order.extend(key for key in definitions if key not in order)
    labels = stored.get('labels') or {}
    hidden = set(stored.get('hidden') or [])
    return CategoryPresentation(revision=stored.get('revision', 0), categories=[
        ListCategory(id=key, label=definitions[key][1] if key == 'completed' else labels.get(key) or definitions[key][1],
            default_label=definitions[key][1], description=definitions[key][2],
            hidden=key not in {'completed', 'planning'} and key in hidden and not (counts or {}).get(key),
            can_rename=key != 'completed', can_hide=key not in {'completed', 'planning'})
        for key in order
    ])


async def read(db, user_id, *, counts=None):
    preferences = await db.scalar(select(UserSettings.preferences).where(UserSettings.user_id == user_id))
    return resolve(preferences=preferences, counts=counts)


async def membership_counts(db, user_id):
    memberships = union_all(
        select(GameEntry.status).where(GameEntry.user_id == user_id),
        select(GameChildState.status).where(GameChildState.user_id == user_id, GameChildState.base_entry_id.is_not(None)),
    ).subquery()
    return dict((await db.execute(select(memberships.c.status, func.count()).group_by(memberships.c.status))).all())


def store(row, domain, presentation):
    preferences = dict(row.preferences or {})
    domains = dict(preferences.get(KEY) or {})
    domains[domain] = {
        'revision': presentation.revision,
        'order': [category.id for category in presentation.categories],
        'labels': {category.id: category.label for category in presentation.categories if category.label != category.default_label},
        'hidden': [category.id for category in presentation.categories if category.hidden],
    }
    row.preferences = {**preferences, KEY: domains}


async def save(db, user_id, patch):
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    row = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id).with_for_update().execution_options(populate_existing=True))
    current = resolve(preferences=row.preferences if row else None)
    if patch.revision != current.revision:
        raise HTTPException(409, 'Categories changed. Reload them before saving.')
    ids = {category.id for category in current.categories}
    if patch.order is not None and (len(patch.order) != len(ids) or set(patch.order) != ids):
        raise HTTPException(422, 'Include each category exactly once.')
    categories = {category.id: category for category in current.categories}
    if patch.labels is not None:
        if set(patch.labels) - ids or 'completed' in patch.labels:
            raise HTTPException(422, 'Completed cannot be renamed.')
        for key, label in patch.labels.items():
            label = categories[key].default_label if label is None else label.strip()
            if not label or len(label) > 32 or any(ord(character) < 32 for character in label):
                raise HTTPException(422, 'Category names must contain 1 to 32 characters.')
            categories[key].label = label
    labels = [category.label.casefold() for category in categories.values()]
    if len(set(labels)) != len(labels):
        raise HTTPException(422, 'Category names must be unique.')
    counts = await membership_counts(db, user_id)
    if patch.hidden is not None:
        if len(set(patch.hidden)) != len(patch.hidden) or set(patch.hidden) - ids or {'completed', 'planning'} & set(patch.hidden):
            raise HTTPException(422, 'Completed and Planning cannot be hidden.')
        if any(counts.get(key) for key in patch.hidden):
            raise HTTPException(409, 'Only empty categories can be hidden.')
        for category in categories.values():
            category.hidden = category.id in patch.hidden
    current.categories = [categories[key] for key in (patch.order or list(categories))]
    current.revision += 1
    if row is None:
        await db.execute(insert(UserSettings).values(user_id=user_id).on_conflict_do_nothing())
        row = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id)
            .with_for_update().execution_options(populate_existing=True))
    store(row, 'game', current)
    await db.commit()
    return current


async def restore_populated(db, user_id):
    """Called before committing an account-locked membership write."""
    row = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id)
        .with_for_update().execution_options(populate_existing=True))
    current = resolve(preferences=row.preferences if row else None)
    if not any(category.hidden for category in current.categories):
        return
    await db.flush()
    counts = await membership_counts(db, user_id)
    changed = False
    for category in current.categories:
        if category.hidden and counts.get(category.id):
            category.hidden = False
            changed = True
    if changed:
        current.revision += 1
        store(row, 'game', current)
