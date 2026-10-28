"""Bounded, shared public title retrieval with explicit pagination evidence."""
import hashlib
import json
from datetime import timedelta

from core.catalogue_providers import ProviderError
from core.game_imdb_discovery_source import FIELDS
from core.game_title_match import VERSION, spelling

FIELDS = FIELDS + ',alternative_names.name,release_dates.date,remakes,remasters,websites.url,external_games.platform,external_games.countries'
PAGE = 100
MAX_PAGES = 3


async def retrieve(sources, title):
    names = list(dict.fromkeys([title['title'], *title.get('aliases', [])]))
    if not names or len(names) > 20 or any(not isinstance(name, str) or not 1 <= len(name) <= 500 for name in names):
        raise ProviderError('coverage_limited')
    key = hashlib.sha256(json.dumps([VERSION,names], ensure_ascii=True).encode()).hexdigest()

    async def load():
        found, branches = {}, []
        for name in names:
            # Search supplies punctuation/alias candidates; exact case-insensitive
            # name filters are a separate branch, rather than a top-hit decision.
            quoted = json.dumps(name)
            for branch, query in [('search', f'search {quoted};'),
                                  ('exact', f'where name ~ {quoted} | alternative_names.name ~ {quoted};')]:
                complete = False
                for page in range(MAX_PAGES):
                    rows = await sources.igdb('games', f'fields {FIELDS}; {query} limit {PAGE}; offset {page * PAGE};')
                    if not isinstance(rows, list) or len(rows) > PAGE or any(
                        not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                        or not isinstance(row.get('name'), str) for row in rows):
                        raise ProviderError('bad_response')
                    found.update((row['id'], row) for row in rows)
                    if len(rows) < PAGE:
                        complete = True
                        break
                branches.append({'name': name, 'kind': branch, 'complete': complete, 'pages': page + 1})
        identity_fields=('id','name','alternative_names','first_release_date','release_dates','game_type',
            'version_parent','parent_game','websites','external_games')
        identity=[{field:row[field] for field in identity_fields if field in row} for _,row in sorted(found.items())]
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return {'rows': list(found.values()), 'complete': all(row['complete'] for row in branches), 'branches': branches,
            'scope_key':key,'scope_names':sorted({spelling(name) for name in names}), 'scope_fingerprint':fingerprint}

    return await sources.cache.get('igdb', 'game_title_candidates', key, load,
                                   ttl=timedelta(days=1), max_stale=timedelta(days=7))
