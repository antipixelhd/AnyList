"""Group reviewed studios and read work evidence without provider fan-out."""
from core.catalog_search import result_filters
from core.studio_groups import studio_root, studio_companies, studio_display_name
from core.contributor_evidence import cached_evidence, evidence_key, local_evidence, work_summary
from models import GlobalSettings

MAX_CANDIDATES = 8


async def verified_studios(db, viewer, rows, api_key):
    if not rows:
        return [], False
    filters = await result_filters(db, viewer)
    settings = await db.get(GlobalSettings, 1)
    show_anime = settings is None or settings.show_anime
    rows = rows[:MAX_CANDIDATES]
    local = await local_evidence(db, rows, 'studio', filters, show_anime)
    rows = [{**row, '_local':local.get(row['key'])} for row in rows]
    # Prefer known contributors, then provider-branded matches; retain provider
    # order among equal candidates rather than sorting unverified names A-Z.
    rows = sorted(rows, key=lambda row: (-row.get('_works', 0), not bool(row.get('image'))))
    groups = {}
    for row in rows:
        native = row.get('_native')
        root = studio_root(native) if native else row['key']
        if root not in groups:
            groups[root] = {**row}
        if row.get('_local'):
            old = groups[root].get('_local') or {}
            groups[root]['_local'] = {'works': max(old.get('works', 0), row['_local']['works']),
                'latest':max(old.get('latest', ''), row['_local']['latest'])}
        if native and native != root:
            groups[root].update(key=f'tmdb:{root}', url=f'/studio/tmdb:{root}')
        if native:
            groups[root].update(name=studio_display_name(native, groups[root]['name']), _native=root)
        if native == root:
            groups[root].update(key=row['key'], url=row['url'], image=row.get('image'))
    for row in groups.values():
        native = row.get('_native')
        if native and studio_companies(native) != native:
            row.update(key=f'tmdb:{native}', url=f'/studio/tmdb:{native}')
    candidates = list(groups.values())[:MAX_CANDIDATES]
    jobs = {}
    for row in candidates:
        if row.get('_native'):
            key, config = evidence_key('studio', row['_native'], filters, show_anime)
            jobs[key] = config
    evidence, pending = await cached_evidence(db, jobs, viewer.id if viewer else None, enqueue=bool(api_key))
    output = []
    for row in candidates:
        key = evidence_key('studio', row['_native'], filters, show_anime)[0] if row.get('_native') else None
        value = work_summary(evidence.get(key), 'studio', filters, show_anime)
        # Actual eligible local works remain useful even before remote verification.
        if row.get('_local'):
            value = {'works':max(row['_local']['works'], (value or {}).get('works', 0)),
                'latest':max(row['_local']['latest'], (value or {}).get('latest', ''))}
        if value and value['works']:
            output.append({**row, '_works':value['works'], '_latest':value['latest']})
    return output, pending and bool(api_key)
