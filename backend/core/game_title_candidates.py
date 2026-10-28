"""Bounded, shared public title retrieval with explicit pagination evidence."""
import hashlib
import json
import re
from datetime import timedelta

from core.catalogue_providers import ProviderError
from core.game_imdb_discovery_source import FIELDS
from core.game_title_match import VERSION, spelling

FIELDS = FIELDS + ',alternative_names.name,release_dates.date,release_dates.status.name,remakes,remasters,websites.url,external_games.platform,external_games.countries,platforms.id,parent_game.id'
PAGE = 100
MAX_PAGES = 3
IDENTITY_FIELDS=('id','name','alternative_names','first_release_date','release_dates','game_type',
    'version_parent','parent_game','websites','external_games','involved_companies','platforms')


def observations(rows):
    """Order-insensitive public identity facts, shared across query spellings."""
    def normalized(value):
        if isinstance(value,dict):return {key:normalized(item) for key,item in value.items()}
        if isinstance(value,list):return sorted((normalized(item) for item in value),key=lambda item:json.dumps(item,sort_keys=True))
        return value
    return {str(row['id']):hashlib.sha256(json.dumps(normalized({field:row[field] for field in IDENTITY_FIELDS if field in row}),
        sort_keys=True).encode()).hexdigest() for row in rows}


async def retrieve(sources, title):
    names = list(dict.fromkeys([title['title'], *title.get('aliases', [])]))
    if not names or len(names) > 20 or any(not isinstance(name, str) or not 1 <= len(name) <= 500 for name in names):
        raise ProviderError('coverage_limited')
    key = hashlib.sha256(json.dumps([VERSION,names], ensure_ascii=True).encode()).hexdigest()

    async def load():
        found, branches = {}, []
        expanded=list(names);parents={}
        for position,name in enumerate(expanded):
            # Search supplies punctuation/alias candidates; exact case-insensitive
            # name filters are a separate branch, rather than a top-hit decision.
            quoted = json.dumps(name,ensure_ascii=False)
            queries=[('search', f'search {quoted};'),('exact', f'where name ~ {quoted} | alternative_names.name ~ {quoted};')]
            if name in parents:
                parent=json.dumps(parents[name],ensure_ascii=False);full=json.dumps(parents[name]+': '+name,ensure_ascii=False)
                queries=[('search',f'search {quoted}; where parent_game.name ~ {parent};'),
                    ('exact',f'where parent_game.name ~ {parent} & (name ~ {quoted} | name ~ {full} | alternative_names.name ~ {quoted} | alternative_names.name ~ {full});')]
            for branch, query in queries:
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
            if position==len(names)-1 and not any(spelling(row['name']) in {spelling(name) for name in names}
                or any(isinstance(alias,dict) and spelling(alias.get('name')) in {spelling(name) for name in names} for alias in row.get('alternative_names') or []) for row in found.values()):
                parts=title['title'].split(':',1)
                if len(parts)==2:
                    tail=re.split(r'\s+in\s+',parts[1],flags=re.IGNORECASE)[-1].strip()
                    if tail not in expanded and parts[0].strip() and len(spelling(tail).split())>=3 and 12<=len(tail)<=500:
                        parents[tail]=parts[0].strip();expanded.append(tail)
        identity=[{field:row[field] for field in IDENTITY_FIELDS if field in row} for _,row in sorted(found.items())]
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return {'rows': list(found.values()), 'complete': all(row['complete'] for row in branches), 'branches': branches,
            'scope_key':key,'scope_names':sorted({spelling(name) for name in [*expanded,*parents.values()]}), 'scope_fingerprint':fingerprint}

    result=await sources.cache.get('igdb', 'game_title_candidates', key, load,
                                   ttl=timedelta(days=1), max_stale=timedelta(days=7))
    # Older cached scopes can supply the same compact facts without a provider
    # refetch or renewing their observation/expiry timestamps.
    if 'scope_observations' not in result['data']:
        import asyncio
        result={**result,'data':{**result['data'],'scope_observations':await asyncio.to_thread(observations,result['data']['rows'])}}
    return result
