"""Bounded, shared public title retrieval with explicit pagination evidence."""
import hashlib
import json
import re
from datetime import timedelta

from core.catalogue_providers import ProviderError
from core.game_imdb_discovery_source import FIELDS as DOCUMENT_FIELDS
from core.game_title_match import spelling,title_formats,written_words

# Identity discovery needs the primary poster for the accepted list card, but
# optional galleries/descriptions belong to ordinary game-detail enrichment.
# Keep identity fields unchanged so existing accepted observations stay valid.
FIELDS = ','.join(field for field in DOCUMENT_FIELDS.split(',')
    if field != 'summary' and not field.startswith(('artworks.', 'screenshots.')))
FIELDS += ',alternative_names.name,release_dates.date,release_dates.status.name,remakes,remasters,websites.url,external_games.platform,external_games.countries,platforms.id,parent_game.id'
PAGE = 100
MAX_PAGES = 3
# Matching rules may change while the provider query and stored facts stay valid.
RETRIEVAL_VERSION = 5
IDENTITY_FIELDS=('id','name','alternative_names','first_release_date','release_dates','game_type',
    'version_parent','parent_game','websites','external_games','involved_companies','platforms')


def identity_facts(raw):
    result={field:raw[field] for field in IDENTITY_FIELDS if field in raw}
    if re.search(r'\s(?:[0-9]{4}|\([0-9]{4}\))$',raw.get('name','')):
        result['annual_genres']=sorted({row['name'] for row in raw.get('genres') or [] if isinstance(row.get('name'),str)})
    return result


def observations(rows):
    """Order-insensitive public identity facts, shared across query spellings."""
    def normalized(value):
        if isinstance(value,dict):return {key:normalized(item) for key,item in value.items()}
        if isinstance(value,list):return sorted((normalized(item) for item in value),key=lambda item:json.dumps(item,sort_keys=True))
        return value
    return {str(row['id']):hashlib.sha256(json.dumps(normalized(identity_facts(row)),
        sort_keys=True).encode()).hexdigest() for row in rows}


async def retrieve(sources, title):
    names = list(dict.fromkeys([title['title'], *title.get('aliases', [])]))
    if not names or len(names) > 20 or any(not isinstance(name, str) or not 1 <= len(name) <= 500 for name in names):
        raise ProviderError('coverage_limited')
    annual_year=title.get('year') if type(title.get('year')) is int and not re.search(r'[0-9]{4}',title['title']) else None
    key = hashlib.sha256(json.dumps([RETRIEVAL_VERSION,names,annual_year], ensure_ascii=True).encode()).hexdigest()
    source_names={spelling(name) for name in names}
    source_formats=set().union(*(title_formats(name) for name in names))
    episode=re.fullmatch(r'(.+?)(?::\s*|\s+[-–—]\s+)(episode\s+(?:[0-9]+|[a-z]+))\s*',title['title'],re.IGNORECASE)
    def format_only(raw):
        labels=[raw['name'],*[row.get('name') for row in raw.get('alternative_names') or [] if isinstance(row,dict)]]
        return (not any(spelling(value) in source_names for value in labels)
            and any(source_formats & title_formats(value) for value in labels))

    def sports_family(rows):
        return any(any(genre.get('name')=='Sport' for genre in raw.get('genres') or [])
            and any(source_formats & title_formats(re.sub(r'\s+(?:[0-9]{4}|\([0-9]{4}\))$','',name)) for name in [raw['name'],
                *[row.get('name') for row in raw.get('alternative_names') or [] if isinstance(row,dict)]] if isinstance(name,str)) for raw in rows)

    async def load():
        found, branches = {}, []
        expanded=list(names);parents={};keyword=None;joined_keyword=None
        for position,name in enumerate(expanded):
            observed={}
            # Search supplies punctuation/alias candidates; exact case-insensitive
            # name filters are a separate branch, rather than a top-hit decision.
            quoted = json.dumps(name,ensure_ascii=False)
            queries=[('search', f'search {quoted};'),('exact', f'where name ~ {quoted} | alternative_names.name ~ {quoted};')]
            if name==keyword:queries=[('keyword',f'search {quoted};')]
            if name==joined_keyword:queries=[('joined_keyword',f'search {quoted};')]
            if name==title['title']+' '+str(annual_year):
                queries=[('annual_'+kind,query) for kind,query in queries]
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
                    observed.update((row['id'], row) for row in rows)
                    if len(rows) < PAGE:
                        complete = True
                        break
                branches.append({'name': name, 'kind': branch, 'complete': complete, 'pages': page + 1})
            if position==len(names)-1 and not any(spelling(row['name']) in {spelling(name) for name in names}
                or any(isinstance(alias,dict) and spelling(alias.get('name')) in {spelling(name) for name in names} for alias in row.get('alternative_names') or []) for row in found.values()):
                parts=[episode[1],episode[2]] if episode else title['title'].split(':',1)
                if len(parts)==2:
                    tail=re.split(r'\s+in\s+',parts[1],flags=re.IGNORECASE)[-1].strip()
                    if tail not in expanded and parts[0].strip() and (episode or len(spelling(tail).split())>=3 and 12<=len(tail)<=500):
                        parents[tail]=parts[0].strip();expanded.append(tail)
            # A zero-hit or newly formatted title needs a wider family search.
            # One keyword proposes candidates; full title/year rules still
            # decide identity, and truncated keyword searches stay incomplete.
            needs_format=any(format_only(raw) for raw in found.values()) if position==len(expanded)-1 and keyword is None else False
            if position==len(expanded)-1 and keyword is None and (not found or needs_format):
                text=re.sub(r'^The(?=[A-Z][a-z])','',title['title'])
                words=re.findall(r'[^\W_]+',re.sub("['’]",'',text),re.UNICODE)
                while words and words[0].casefold() in ('the','a','an'):words.pop(0)
                if words and spelling(words[0]) in {spelling(value) for value in expanded}:
                    words=re.findall(r'[^\W_]+',written_words(re.sub("['’]",'',text)),re.UNICODE)
                    while words and words[0].casefold() in ('the','a','an'):words.pop(0)
                if (words and len(words[0])>=(1 if needs_format else 4)
                        and spelling(words[0]) not in {spelling(value) for value in expanded}):
                    keyword=words[0];expanded.append(keyword)
            if name==keyword and len(words)>1 and len(words[1])>=3 and words[1].isalpha():
                matches=any(source_formats & title_formats(value) for raw in observed.values() for value in
                    [raw['name'],*[row.get('name') for row in raw.get('alternative_names') or [] if isinstance(row,dict)]])
                joined=words[0]+words[1]
                if not matches and joined not in expanded:
                    joined_keyword=joined;expanded.append(joined)
            annual_name=title['title']+' '+str(annual_year)
            if (position==len(expanded)-1 and annual_year and annual_name not in expanded
                    and all(row['complete'] for row in branches) and sports_family(found.values())):
                expanded.append(annual_name)
        identity=[identity_facts(row) for _,row in sorted(found.items())]
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        return {'rows': list(found.values()), 'complete': all(row['complete'] for row in branches), 'branches': branches,
            'scope_key':key,'scope_names':sorted({spelling(name) for name in [*expanded,*parents.values()]}),
            'scope_fingerprint':fingerprint,'keyword_scope_version':1,'episode_scope_version':1}

    result=await sources.cache.get('igdb', 'game_title_candidates', key, load,
                                   ttl=timedelta(days=1), max_stale=timedelta(days=7),
                                   accept=lambda data:(not episode or data.get('episode_scope_version')==1) and (
                                       data.get('keyword_scope_version')==1 or (
                                       bool(data.get('rows')) and not any(format_only(raw) for raw in data['rows']))))
    # Older cached scopes can supply the same compact facts without a provider
    # refetch or renewing their observation/expiry timestamps.
    if 'scope_observations' not in result['data']:
        import asyncio
        result={**result,'data':{**result['data'],'scope_observations':await asyncio.to_thread(observations,result['data']['rows'])}}
    return result
