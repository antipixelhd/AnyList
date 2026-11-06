"""Private editor context from the account's last accepted Steam observations."""
from sqlalchemy import select
from models import SteamConnection, UserSettings


async def context(db, user_id, work_id):
    rows = await db.scalars(select(SteamConnection).where(
        SteamConnection.user_id == user_id, SteamConnection.imported.is_(True)))
    playtime, achievements = {}, {}
    for row in rows:
        for app in row.selection:
            checkpoint = row.checkpoints.get(app, {})
            if checkpoint.get('work_id') != work_id:
                continue
            if row.sync_playtime and checkpoint.get('observed_playtime_minutes') is not None:
                playtime[app] = max(playtime.get(app, 0), checkpoint['observed_playtime_minutes'])
            if row.sync_achievements:
                achievements[app] = checkpoint.get('achievement_count')
    settings = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
    acknowledged = (settings.preferences or {}).get('steam_progress_warnings', []) if settings else []
    return {'playtime_seconds': sum(playtime.values()) * 60 if playtime else None,
        'achievement_count': sum(achievements.values()) if achievements and all(v is not None for v in achievements.values()) else None,
        'playtime_enabled': bool(playtime), 'achievements_enabled': bool(achievements),
        'acknowledged': acknowledged}


async def acknowledge(db, user_id, field):
    # Same owner lock as game writes, including creation of account settings.
    from core.game_tracking import lock_owner
    await lock_owner(db, user_id)
    settings = await db.scalar(select(UserSettings).where(UserSettings.user_id == user_id).with_for_update())
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
    preferences = dict(settings.preferences or {})
    preferences['steam_progress_warnings'] = sorted(set(preferences.get('steam_progress_warnings', [])) | {field})
    settings.preferences = preferences
    await db.commit()
