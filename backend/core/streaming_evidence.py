"""Evidence selection and historical activity plans shared by streaming imports."""
from datetime import date
from calendar import monthrange
from core.netflix_import import _norm_text


def choose_viewing(rows, runtime_minutes=None):
    rows = [row for row in rows if row.get('watched_at')]
    if not rows:
        return None
    runtime = float(runtime_minutes or 0) * 60
    completed = [row for row in rows if row.get('completed_evidence') or
                 (runtime > 0 and row.get('watched_seconds') is not None and row['watched_seconds'] >= runtime * .7)]
    if completed:
        return min(completed, key=lambda row: row['watched_at'])
    return min(rows, key=lambda row: (-(row.get('watched_seconds') or 0), row['watched_at']))


def prepare_evidence(items, observations):
    by_title = {}
    for observation in observations:
        row = observation.to_dict()
        for title in {observation.source_title, observation.episode_title, observation.show_title} - {None}:
            by_title.setdefault(_norm_text(title), []).append(row)
    for item in items:
        if item.get('is_anime'):
            continue
        candidate = item.get('match', {}).get('candidate') or {}
        details = candidate.get('details') or {}
        evidence = item.setdefault('source_evidence', {})
        evidence['observations'] = by_title.get(_norm_text(item['source_title']), evidence.get('observations', []))
        if item['kind'] == 'movie':
            rows = by_title.get(_norm_text(item['source_title']), []) or evidence.get('observations', [])
            runtime = details.get('runtime') or candidate.get('runtime')
            chosen = choose_viewing(rows, runtime)
            if chosen:
                item.setdefault('source_evidence', {})['viewings'] = rows
                item['source_dates'] = [chosen['watched_at']]
                if chosen.get('watched_seconds') is not None:
                    enough = chosen.get('completed_evidence') or (runtime and chosen['watched_seconds'] >= runtime * 60 * .7)
                    item['suggested_movie_status'] = 'completed' if enough else 'dropped'
        else:
            catalog = {(ep.get('season_number'), ep.get('episode_number')): ep for ep in item.get('catalog_episodes', [])}
            for episode in item.get('episodes', []):
                names = [episode.get('source_title'), episode.get('source_episode_title')]
                rows = next((by_title[_norm_text(name)] for name in names if name and _norm_text(name) in by_title), [])
                rows = [row for row in rows if (not row.get('show_title') or _norm_text(row['show_title']) == _norm_text(item['source_title']))
                        and (row.get('season_number') is None or row['season_number'] == episode.get('season_number'))]
                runtime = (catalog.get((episode.get('season_number'), episode.get('episode_number'))) or {}).get('runtime')
                chosen = choose_viewing(rows, runtime)
                if chosen:
                    episode['viewings'] = rows
                    episode['dates'] = [chosen['watched_at']]
            item['source_dates'] = sorted({day for ep in item.get('episodes', []) for day in ep.get('dates', [])})
        if item['kind'] == 'show' and item['outcome']['status'] in ('partial', 'skip') and not item['outcome'].get('status_overridden'):
            item['outcome']['tracking_status'] = partial_tracking_status(item)
        item['completion_evidence'] = completion_evidence(item)


def partial_tracking_status(item, today=None):
    today = today or date.today()
    month = today.year * 12 + today.month - 1 - 4
    year, month = divmod(month, 12)
    month += 1
    cutoff = date(year, month, min(today.day, monthrange(year, month)[1]))
    dated = [(ep, day) for ep in item.get('episodes', []) if ep.get('season_number') and ep.get('episode_number')
             for day in ep.get('dates', [])]
    if not dated:
        return 'dropped'
    episode, day = max(dated, key=lambda value: (value[1], value[0]['season_number'], value[0]['episode_number']))
    season = next((s for s in item.get('seasons', []) if s['season_number'] == episode['season_number']), {})
    try:
        recent = cutoff <= date.fromisoformat(day) <= today
    except (ValueError, TypeError):
        recent = False
    finished = bool(season.get('total_released')) and episode['episode_number'] >= season['total_released']
    return 'watching' if finished and recent else 'dropped'


def completion_evidence(item):
    dates = sorted(set(item.get('source_dates') or []))
    result = {'suggested_date': dates[-1] if dates else None, 'finish_date': None}
    if item['kind'] == 'movie':
        result['finish_date'] = dates[0] if dates else None
        return result
    seasons = [s for s in item.get('seasons', []) if s.get('season_number', 0) > 0 and s.get('total_released', 0)]
    if not seasons:
        return result
    last = max(seasons, key=lambda s: s['season_number'])
    tail = [day for ep in item.get('episodes', []) if ep.get('season_number') == last['season_number']
            and ep.get('episode_number') and max(1, last['total_released'] - 2) <= ep['episode_number'] <= last['total_released']
            for day in ep.get('dates', [])]
    result['finish_date'] = max(tail, default=None)
    return result


def activity_event_order(event):
    # Final status must win over episode/season actions on the same day.
    return event['date'], (3 if event['key'] in ('finish', 'status') else
                           2 if event['key'].startswith('season:') else
                           0 if event['key'] == 'start' else 1)


def activity_plan(item, finish_date=None):
    outcome = item.get('outcome') or {}
    episodes = [ep for ep in item.get('episodes', []) if ep.get('resolution') in ('exact', 'guessed')]
    endpoint = (outcome.get('latest_season') or 0, outcome.get('latest_episode') or 0)
    if outcome.get('status') == 'partial':
        episodes = [ep for ep in episodes if (ep.get('season_number') or 0, ep.get('episode_number') or 0) <= endpoint]
    dated = [(ep, min(ep['dates'])) for ep in episodes if ep.get('dates')]
    dates = [day for _, day in dated] if item['kind'] == 'show' else list(item.get('source_dates') or [])
    events = [{'key': 'start', 'date': min(dates), 'status': 'watching', 'started_watching': True, 'status_changed': True}] if dates else []
    for ep, day in dated:
        sn, en = ep.get('season_number'), ep.get('episode_number')
        events.append({'key': f'episode:{sn}:{en}', 'date': day, 'status': 'watching',
                       'episodes_watched': 1, 'position': f'S{sn}E{en}'})
    for season in item.get('seasons', []):
        sn, total = season['season_number'], season.get('total_released')
        complete = outcome.get('status') == 'completed' or (total and (sn, total) <= endpoint)
        evidence = [day for ep, day in dated if ep.get('season_number') == sn]
        if complete and evidence:
            events.append({'key': f'season:{sn}', 'date': max(evidence), 'status': 'watching', 'finished_seasons': [sn]})
    status = 'completed' if outcome.get('status') == 'completed' else outcome.get('tracking_status', 'watching')
    if status in ('completed', 'paused', 'dropped'):
        day = finish_date if status == 'completed' else max(dates, default=None)
        if day:
            events.append({'key': 'finish' if status == 'completed' else 'status', 'date': str(day),
                           'status': status, 'status_changed': True})
    # A season completion replaces its final dated episode event, not earlier history.
    season_days = {(event['finished_seasons'][0], event['date']) for event in events if event.get('finished_seasons')}
    events = [event for event in events if not (event['key'].startswith('episode:') and
              (int(event['key'].split(':')[1]), event['date']) in season_days)]
    return sorted(events, key=activity_event_order)
