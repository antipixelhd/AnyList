"""One pending echo per outbound Jellyfin/Emby watched write.

These webhooks carry no durable play identity. A per-user/title queue suppresses
one echo for each peer write while allowing a later genuine rewatch.
"""
from datetime import datetime, timedelta

_recently_pushed_watched: dict[tuple[int, int], list[datetime]] = {}
_PUSHED_WATCHED_TTL = timedelta(minutes=10)


def mark_pushed_watched(user_id: int, media_id: int) -> None:
    if len(_recently_pushed_watched) > 5000:
        cutoff = datetime.utcnow() - _PUSHED_WATCHED_TTL
        for key, queue in list(_recently_pushed_watched.items()):
            if all(pushed_at < cutoff for pushed_at in queue):
                del _recently_pushed_watched[key]
    _recently_pushed_watched.setdefault((user_id, media_id), []).append(datetime.utcnow())



def consume_recently_pushed_watched(user_id: int, media_id: int) -> bool:
    key = (user_id, media_id)
    queue = _recently_pushed_watched.get(key)
    if not queue:
        return False
    pushed_at = queue.pop(0)
    if not queue:
        del _recently_pushed_watched[key]
    return (datetime.utcnow() - pushed_at) < _PUSHED_WATCHED_TTL
