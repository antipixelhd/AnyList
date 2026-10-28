"""Verify candidate aliases and dated releases through explicit IGDB identities."""
import re
import hashlib
import json
from difflib import SequenceMatcher

from core.catalogue_providers import ProviderError
from core.game_credit_sources import GameCreditSources,verify_game
from core.game_title_match import spelling,decide
from core.game_types import INDEPENDENT,CREDIT_CHILDREN,igdb_game_class
from core.wikidata_claims import best_claims,claims,snak_value


def agrees(row,title):
    proof=(row.get('corroboration') or {}).get('wikidata_title') or {}
    return (proof.get('imdb_id')==title.get('id') and str(proof.get('igdb_id'))==str(row.get('id'))
        and bool(re.fullmatch(r'Q[1-9][0-9]*',str(proof.get('item',''))))
        and type(proof.get('revision')) is int and proof['revision']>0
        and type(title.get('year')) is int and title['year'] in proof.get('years',[])
        and bool(({spelling(title.get('title')),*[spelling(name) for name in title.get('aliases',[])]}-{''})
            & ({spelling(name) for name in proof.get('names',[])}-{''})))


def discovery_candidate(raw,title):
    """Similarity selects a lookup only; it never establishes a mapping."""
    if (not isinstance(raw.get('slug'),str) or not raw['slug'] or raw.get('version_parent')
            or igdb_game_class(raw) not in (INDEPENDENT-{'port'})|CREDIT_CHILDREN):return False
    names=[spelling(raw.get('name')),*[spelling(row.get('name')) for row in raw.get('alternative_names') or [] if isinstance(row,dict)]]
    targets=[spelling(title.get('title')),*[spelling(name) for name in title.get('aliases',[])]]
    return any(len(one)>=8 and len(two)>=8 and re.findall(r'\d+',one)==re.findall(r'\d+',two)
        and SequenceMatcher(None,one,two,autojunk=False).ratio()>=.72 for one in names for two in targets)


def project(item,raw,title):
    if not verify_game(item,raw['id'],raw['slug']):return {}
    imdb={value for row in claims(item,'P345') if (value:=snak_value(row.get('mainsnak'))) is not None}
    if imdb and imdb!={title['id']}:return {}
    names=[value.get('value') for value in (item.get('labels') or {}).values() if isinstance(value,dict)]
    names.extend(value.get('value') for rows in (item.get('aliases') or {}).values() for value in rows if isinstance(value,dict))
    dates=[]
    for statement in best_claims(item,'P577'):
        value=snak_value(statement.get('mainsnak'))
        if (not isinstance(value,dict) or type(value.get('precision')) is not int or value['precision']<9
                or value.get('before',0) or value.get('after',0)):continue
        match=re.fullmatch(r'\+([0-9]{4})-[0-9]{2}-[0-9]{2}T00:00:00Z',str(value.get('time')))
        if match and 1<=int(match[1])<=9999:dates.append(int(match[1]))
    proof={'wikidata_title':{'imdb_id':title['id'],'igdb_id':raw['id'],'item':item['id'],
        'revision':item.get('lastrevid'),'names':sorted({name for name in names if isinstance(name,str) and name.strip()}),
        'years':sorted(set(dates))}}
    return proof if agrees({'id':raw['id'],'corroboration':proof},title) else {}


async def corroborate(sources,title,candidates):
    from core.game_match_support import cached
    selected=[raw for raw in candidates if discovery_candidate(raw,title)]
    if type(title.get('year')) is not int or not selected:return {},[]
    # Loose DLC/sequel similarities must not consume the exact title's bounded
    # lookup slots. This only chooses lookups: the caller still decides against
    # its complete pool, including exact aliases and unknown-year competitors.
    matched={row['id'] for row in decide(title,selected,complete=False,branches=[])['evidence']['candidates']}
    if matched:selected=[raw for raw in selected if raw['id'] in matched]
    if len(selected)>3:return {},[]
    wiki=GameCreditSources(sources);evidence={};snapshots=[]
    for raw in selected:
        async def load():
            try:bridge=await wiki.game_item(raw['id'],raw['slug'])
            except ProviderError as error:
                if error.code!='identity_conflict':raise
                return {'proof':{},'state':'identity_conflict'}
            if bridge.get('stale'):raise ProviderError('stale_mapping')
            q=bridge['data']['item']
            if not q:return {'proof':{}}
            fetched=await wiki.entities([q],release_references=True)
            if fetched.get('stale'):raise ProviderError('stale_mapping')
            item=fetched['data']['entities'].get(q)
            if not item:raise ProviderError('incomplete_cache')
            proof=project(item,raw,title)
            return {'proof':proof,'state':'supported' if proof else 'unmatched'}
        key=hashlib.sha256(json.dumps([title['id'],title['title'],title.get('aliases',[]),title.get('year'),raw['id'],raw['slug']],ensure_ascii=True).encode()).hexdigest()
        value,snapshot=await cached(sources,'wikidata','game_match_wiki',key,load)
        snapshots.append(snapshot)
        if value['proof']:evidence[raw['id']]=value['proof']
    return evidence,snapshots
