"""Shared display projections for tracked titles, entries, and activity.

These functions read already-loaded values; they do not query or mutate state.
"""

from models.media import Media
from datetime import datetime
from core.tracking_rules import effective_score
from core.movie_financials import movie_financials


def is_anime(media: Media) -> bool:
    return is_anime_data(media.tmdb_data or {})


def media_genres(data):
    return [g if isinstance(g, str) else g['name'] for g in data.get('genres', []) or []
        if isinstance(g, str) or (isinstance(g, dict) and isinstance(g.get('name'), str) and g['name'])]


def is_anime_data(data) -> bool:
    genres = {
        str(g if isinstance(g, str) else g.get("name", "")).lower()
        for g in data.get("genres", []) or []
        if isinstance(g, (str, dict))
    }
    countries = {
        str(value).upper() for value in data.get("origin_country", []) or [] if value
    }
    countries.update(
        str(value.get("iso_3166_1", "")).upper()
        for value in data.get("production_countries", []) or []
        if isinstance(value, dict)
    )
    return "animation" in genres and (
        data.get("original_language") == "ja" or "JP" in countries
    )


def media_data(media):
    data = media.tmdb_data or {}
    from core.media_images import screen_backdrop, IMAGE_POLICY_VERSION
    backdrop = screen_backdrop(media)['backdrop'] or media.backdrop_path
    gallery = data.get('_anylist_images') or {}
    poster = gallery.get('primary_poster') if gallery.get('version') == IMAGE_POLICY_VERSION else None
    regular_seasons = [
        season
        for season in data.get("seasons", [])
        if isinstance(season, dict) and (season.get("season_number") or 0) > 0
    ]
    return {
        "id": media.id,
        "title": media.title,
        "type": media.media_type.value,
        "poster": poster or media.poster_path,
        "backdrop": backdrop,
        "overview": media.overview,
        "year": (media.release_date or "")[:4],
        "release_date": media.release_date,
        "original_title": media.original_title,
        "runtime": media.runtime or data.get("runtime"),
        **(movie_financials(data) if media.media_type.value == 'movie' else {}),
        "tmdb_score": media.tmdb_rating,
        "imdb_score": media.imdb_rating,
        "rt_critic_score": media.rt_critic_score,
        "rt_audience_score": media.rt_audience_score,
        "external_scores_updated_at": media.external_scores_updated_at,
        "tagline": media.tagline or data.get("tagline"),
        "adult": media.adult,
        "original_language": data.get("original_language"),
        "networks": [
            n for n in data.get("networks", []) if isinstance(n, dict) and n.get("name")
        ],
        "studios": [
            c
            for c in data.get("production_companies", [])
            if isinstance(c, dict) and c.get("name")
        ],
        "creators": [
            c
            for c in data.get("created_by", [])
            if isinstance(c, dict) and c.get("name")
        ],
        "episode_runtime": next(
            (value for value in data.get("episode_run_time", []) if value), None
        ),
        "season_count": data.get("number_of_seasons") or len(regular_seasons) or None,
        "episode_count": data.get("number_of_episodes"),
        "last_air_date": data.get("last_air_date"),
        "release_status": media.status or data.get("status"),
        "genres": media_genres(data),
        "tmdb_id": media.tmdb_id,
        "tvdb_id": media.tvdb_id,
        "imdb_id": media.imdb_id,
        "is_anime": is_anime(media),
    }


def release_annotations(episodes, watched_ids, status):
    """Classify released, unwatched episodes using actual season history."""
    if status != 'completed':
        return {'new_seasons': 0, 'new_episodes': 0}
    watched_seasons = {episode.season_number for episode in episodes if episode.id in watched_ids}
    unwatched = [episode for episode in episodes if episode.id not in watched_ids]
    return {
        'new_seasons': len({episode.season_number for episode in unwatched
                            if episode.season_number not in watched_seasons}),
        'new_episodes': sum(episode.season_number in watched_seasons for episode in unwatched),
    }


def entry_data(entry, media, owner=False, *, favorite=None):
    result = {
        **media_data(media),
        "status": entry.status,
        "rating_mode": entry.rating_mode,
        "score": effective_score(
            entry.rating_mode, entry.manual_score, entry.season_scores
        ),
        "season_scores": entry.season_scores,
        "progress": entry.progress,
        "favorite": entry.favorite if favorite is None else favorite,
        "start_date": entry.start_date,
        "finish_date": entry.finish_date,
        "rewatch_count": entry.rewatch_count,
        "updated_at": entry.updated_at,
        "notes": entry.notes,
    }
    if owner:
        result.update(manual_score=entry.manual_score)
    return result


def activity_data(rows, *, include_user=False, limit=12, today=None):
    """Present fixed-window cards, retaining UTC-day grouping for legacy rows."""
    from core.activity import has_activity_event, merge_activity_payload
    from core.streaming_evidence import streaming_episode_span
    from core.season_activity import present_series_activity, season_activity_parts

    grouped = {}
    for row in rows:
        activity, media = row[0], row[1]
        details = present_series_activity(activity, media, today=today)
        user = row[2] if include_user else None
        profile = row[3] if include_user else None
        window = (details.get('window_key') or details.get('window_started_at')
                  or activity.created_at.date().isoformat())
        parts = (season_activity_parts(activity, details) if media.media_type.value == 'series'
                 else [(details, activity.created_at, None)])
        for part, created_at, scope in parts:
            batch = part.get('watch_batch')
            action_window = f'bulk:{batch}' if batch else f'{window}:{scope}' if scope else window
            key = (activity.user_id, media.id, action_window)
            if key in grouped:
                current = grouped[key]
                combined = merge_activity_payload(current['payload'], part)
                combined['series_completed'] = bool(current['payload'].get('series_completed') or part.get('series_completed'))
                if batch and part.get('completion_batch_date'):
                    combined['completion_batch_date'] = part['completion_batch_date']
                current['payload'] = combined
                continue
            grouped[key] = {
                "key": f"{key[0]}:{key[1]}:{key[2]}",
                **(
                    {
                        "user_id": user.id,
                        "username": user.username,
                        "display_name": (profile.display_name if profile else None) or user.username,
                        "has_avatar": bool(profile and profile.avatar_path),
                    }
                    if user
                    else {}
                ),
                "status": activity.status,
                "score": activity.score,
                "payload": part,
                "created_at": created_at,
                "media": media_data(media),
                "_media_record": media,
            }
    for activity in grouped.values():
        details = activity['payload']
        media = activity.pop('_media_record')
        if details.get('completion_batch_date') and activity['created_at'].date().isoformat() != details['completion_batch_date']:
            activity['created_at'] = datetime.fromisoformat(details['completion_batch_date'])
        if details.get('watch_batch') and details.get('finished_seasons') and details.get('episodes_watched'):
            details['bulk_progress'] = True
        if details.get('series_completed'):
            activity['status'] = 'completed'
            details['status_changed'] = True
        if not details.get('position_start'):
            span = streaming_episode_span(details)
            if span:
                details['position_start'], details['position'] = span
        if media.media_type.value == 'series':
            present_season_artwork(activity, media)
        if activity["score"] is None:
            # A known previous score means the rating was cleared. A legacy row
            # with no old or new score cannot establish that an edit occurred.
            activity["payload"]["rating_first"] = False
            if activity["payload"].get("previous_score") is None:
                activity["payload"]["rating_changed"] = False
    return [
        item
        for item in sorted(grouped.values(), key=lambda item: item['created_at'], reverse=True)
        if has_activity_event(item["status"], item["payload"])
    ][:limit]


def present_season_artwork(card, media):
    from core.media_images import tmdb_image_url
    from core.season_activity import activity_season_number

    details = card['payload']
    order = details.get('episode_order') or details.get('progress_order')
    if order not in (None, 'tmdb:aired'):
        return  # Native season artwork doesn't describe an alternate grouping.
    seasons = {season['season_number']: season for season in (media.tmdb_data or {}).get('seasons') or []
               if isinstance(season, dict) and (season.get('season_number') or 0) > 0}
    names = {str(number): name for number, season in seasons.items()
             if (name := (season.get('name') or '').strip()) and name != f'Season {number}'}
    if names:
        details['season_names'] = names
    position = activity_season_number(details.get('position'))
    numbers = (details.get('finished_seasons') or [int(number) for number in details.get('season_ratings') or {}]
               or [details.get('progress_season') or details.get('season_number') or (position[0] if position else 0)])
    for number in sorted(numbers, reverse=True):
        if poster := tmdb_image_url(seasons.get(number, {}).get('poster_path')):
            card['media']['poster'] = poster
            break


def following_title_activity(rows, media, *, today=None, limit=30):
    """Retain season milestones while ordinary statuses follow the latest action."""
    from datetime import date
    today = today or date.today()
    premieres = {
        int(season['season_number']): str(season['air_date'])[:10]
        for season in (media.tmdb_data or {}).get('seasons', [])
        if isinstance(season, dict) and (season.get('season_number') or 0) > 0
        and season.get('air_date') and str(season['air_date'])[:10] <= today.isoformat()
    } if media.media_type.value == 'series' else {}
    candidates = []
    for activity, user, profile in rows:
        details = activity.payload or {}
        status_event = details.get('status_changed') and (
            activity.status in ('completed', 'dropped', 'paused', 'planning')
            or (activity.status == 'watching' and details.get('started_watching') is True)
        )
        if not status_event and not details.get('finished_seasons') and not details.get('season_rating_group'):
            continue
        candidates.append((activity, user, profile))
    ordered = sorted(candidates, key=lambda row: (row[0].created_at, row[0].id), reverse=True)
    cards = activity_data([(activity, media, user, profile) for activity, user, profile in ordered],
                          include_user=True, limit=len(ordered), today=today)
    by_user = {}
    for card in cards:
        details = card['payload']
        # A combined first-watch card may also contain episodes. The details
        # summary exposes its milestone, while timelines retain every action.
        for key in ('episodes_watched', 'position_start', 'position', 'progress'):
            details.pop(key, None)
        completed_on = card['created_at'].date().isoformat()
        if card['status'] == 'completed' and details.get('status_changed') and any(
            premiere > completed_on for premiere in premieres.values()
        ):
            finished = [number for number, premiere in premieres.items() if premiere <= completed_on]
            finished.extend(details.get('finished_seasons') or [])
            if finished:
                card['status'] = 'watching'
                details['status_changed'] = False
                details['finished_seasons'] = [max(finished)]
        choices = by_user.setdefault(card['user_id'], {})
        if details.get('status_changed'):
            choices.setdefault('status', card)
        if details.get('finished_seasons'):
            choices.setdefault('season', card)
        if details.get('season_rating_group'):
            choices.setdefault('rating', card)
    result = []
    for choices in by_user.values():
        status = choices.get('status')
        card = status if status and status['status'] == 'completed' else choices.get('season') or status
        seasonal = [candidate for candidate in (status, choices.get('rating')) if candidate and
                    (candidate['payload'].get('season_status') or candidate['payload'].get('season_rating_group'))]
        if seasonal:
            card = max([candidate for candidate in [card, *seasonal] if candidate], key=lambda candidate:candidate['created_at'])
        if card is None:
            continue
        if card is choices.get('season') and not (card['status'] == 'completed' and card['payload'].get('status_changed')):
            card['status'] = 'watching'
            card['payload']['status_changed'] = False
        result.append(card)
    return sorted(result, key=lambda card: card['created_at'], reverse=True)[:limit]
