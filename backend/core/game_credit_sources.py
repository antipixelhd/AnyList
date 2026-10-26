"""Partial game credit evidence from verified Wikidata identities, never names."""
import hashlib
import json
import re
from datetime import timedelta

from core.catalogue_normalize import document, identity
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources, SLUG, positive
from core.wikidata_claims import ITEM, claims, item_ids, qualified, snak_value, values

STATEMENT = re.compile(r'Q[1-9][0-9]*\$[a-zA-Z0-9-]{1,100}\Z')
ROLES = {'P725':('voice_actor','Voice actor'), 'P161':('actor','Actor'),
         'P57':('director','Director'), 'P58':('writer','Writer'),
         'P86':('composer','Composer'), 'P170':('creator','Creator'),
         'P50':('writer','Writer'), 'P162':('producer','Producer')}
PROPERTIES = set(ROLES) | {'P5794','P9043','P1733','P4985','P345','P31','P18','P674','P424'}


def title(item):
    labels=item.get('labels') or {}
    return next((labels[lang]['value'] for lang in ('en','mul') if isinstance(labels.get(lang),dict)
                 and isinstance(labels[lang].get('value'),str) and labels[lang]['value'].strip()),item['id'])


def unique_value(item,prop,pattern):
    found={value for value in values(item,prop) if isinstance(value,str) and pattern.fullmatch(value)}
    return next(iter(found)) if len(found)==1 else None


def record(item,kind):
    native=item['id']
    identifiers=[identity('wikidata.item',native)]
    if kind=='person':
        if tmdb:=unique_value(item,'P4985',re.compile(r'[1-9][0-9]*\Z')):
            identifiers.append(identity('tmdb.person',tmdb))
        if imdb:=unique_value(item,'P345',re.compile(r'nm[0-9]+\Z')):
            identifiers.append(identity('imdb.person',imdb))
    # P18 names are evidence only. Commons file rights/attribution must be
    # verified separately before any image becomes displayable.
    return {'kind':kind,'identities':identifiers,'name':title(item),
            'attributes':{'wikidata_id':native,'wikidata_revision':item.get('lastrevid'),
                'also_known_as':[value['value'] for value in (item.get('aliases') or {}).get('en',[])
                    if isinstance(value,dict) and isinstance(value.get('value'),str)],
                'image_candidates':[value for value in values(item,'P18') if isinstance(value,str)],
                'source_url':f'https://www.wikidata.org/wiki/{native}'}}


def language(item):
    return unique_value(item,'P424',re.compile(r'[a-z]{2,3}(?:-[a-zA-Z0-9]+)*\Z')) or f'wikidata:{item["id"]}'


def verify_game(item,igdb_id,slug):
    from core.game_imdb_mapping import igdb_evidence
    return igdb_evidence(item,igdb_id,slug)[0]=='verified'


def entity_redirects(body,ids):
    redirects={}
    for row in body.get('redirects') or []:
        if not isinstance(row,dict) or any(not isinstance(row.get(key),str) or not ITEM.fullmatch(row[key]) for key in ('from','to')):
            raise ProviderError('bad_response')
        if row['from'] in redirects and redirects[row['from']]!=row['to']:
            raise ProviderError('identity_conflict')
        redirects[row['from']]=row['to']
    result={}
    for requested in ids:
        target=requested;seen=set()
        while target in redirects:
            if target in seen or len(seen)>=8:raise ProviderError('identity_conflict')
            seen.add(target);target=redirects[target]
        raw=body['entities'].get(target,body['entities'].get(requested))
        if isinstance(raw,dict) and 'missing' not in raw:
            if not isinstance(raw.get('id'),str) or not ITEM.fullmatch(raw['id']):raise ProviderError('bad_response')
            if target!=requested and raw['id']!=target:raise ProviderError('identity_conflict')
            result[requested]=raw
    return result


def project(game,items,igdb_id,slug):
    """All retrieved statements survive as evidence; absence never means no cast."""
    if not verify_game(game,igdb_id,slug):
        raise ProviderError('identity_conflict')
    doc=document(record(game,'game'))
    doc['work']['identities'].insert(0,identity('igdb.game',igdb_id))
    doc['performances']=[]
    doc['performance_keys_complete']=[]
    doc['retired_credit_keys']=[row['id'] for prop in ROLES for row in (game.get('claims') or {}).get(prop,[])
        if row.get('rank')=='deprecated' and isinstance(row.get('id'),str) and STATEMENT.fullmatch(row['id'])]
    characters=item_ids(values(game,'P674'))
    omitted=0
    for prop,(role,label) in ROLES.items():
        for row in claims(game,prop):
            native=snak_value(row.get('mainsnak'))
            person=items.get(native) if isinstance(native,str) else None
            key=row.get('id')
            # An explicit human claim establishes entity kind. Missing entity
            # evidence is partial coverage, never a cast exclusion assertion.
            if not person or 'Q5' not in values(person,'P31') or not isinstance(key,str) or not STATEMENT.fullmatch(key):
                omitted+=1;continue
            roles=item_ids(qualified(row,'P453'))
            characters.update(roles)
            text=[]
            for value in qualified(row,'P4633'):
                value=value.get('text') if isinstance(value,dict) else value
                if isinstance(value,str) and value.strip():text.append(value.strip())
            names=[title(items[q]) for q in sorted(roles) if q in items]+text
            langs=item_ids(qualified(row,'P407'))
            languages=[language(items[q]) if q in items else f'wikidata:{q}' for q in sorted(langs)]
            ordinal=qualified(row,'P1545')
            position=int(ordinal[0])-1 if len(ordinal)==1 and str(ordinal[0]).isdigit() and 0<int(ordinal[0])<100000 else None
            doc['credits'].append({'contributor':record(person,'person'),'role':role,'role_label':label,
                'source_key':key,'position':position,'character_label':' / '.join(dict.fromkeys(names))[:500] or None,
                'attributes':{'work_domain':'game','source_active':True,'languages':languages,
                    'game_role':{'characters':names,'text_characters':text},'statement':row,
                    'source_url':f'https://www.wikidata.org/wiki/{game["id"]}#{prop}',
                    'coverage':'partial'}})
            if role in ('actor','voice_actor'):
                doc['performance_keys_complete'].append(key)
            for character in sorted(roles):
                if character in items:
                    for lang in languages or ['']:
                        doc['performances'].append({'credit_key':key,
                            'character_identity':identity('wikidata.item',character),'language':lang})
    for character in sorted(characters):
        if character not in items:
            omitted+=1;continue
        doc['characters'].append({'character':record(items[character],'character'),
            'attributes':{'work_domain':'game','source_url':f'https://www.wikidata.org/wiki/{game["id"]}',
                'coverage':'partial'}})
    doc['coverage']={'contributors':'partial' if doc['credits'] else 'unknown',
        'cast':'partial' if any(c['role'] in ('actor','voice_actor') for c in doc['credits']) else 'unknown',
        'crew':'partial' if any(c['role'] not in ('actor','voice_actor') for c in doc['credits']) else 'unknown',
        'characters':'partial' if doc['characters'] else 'unknown','credits_complete':False,
        'source_claims_retrieved':True,'omitted_statements':omitted,'wikidata_id':game['id'],
        'wikidata_revision':game.get('lastrevid')}
    return doc


class GameCreditSources:
    def __init__(self,sources=None):
        # Reuse the public HTTP budget, network slots, durable leases, stale
        # evidence and failure backoff already used for game metadata sources.
        self.sources=sources or GameBrowseSources()

    async def entities(self,ids,*,person_references=False):
        if not isinstance(ids,(list,tuple,set)) or not ids or len(ids)>50 or any(not isinstance(q,str) or not ITEM.fullmatch(q) for q in ids):
            raise ProviderError('invalid_request')
        ids=sorted(set(ids))
        properties=PROPERTIES | {'P569'} if person_references else PROPERTIES
        key=hashlib.sha256(json.dumps(['person-reference-v1' if person_references else 'identity-v2',ids]).encode()).hexdigest()
        async def load():
            body=await self.sources.request('wikidata','https://www.wikidata.org/w/api.php',params={
                'action':'wbgetentities','ids':'|'.join(ids),'props':'labels|descriptions|aliases|claims|info',
                'languages':'en|mul','languagefallback':1,'format':'json','maxlag':5,'redirects':'yes'})
            if isinstance(body,dict) and (body.get('error') or {}).get('code')=='maxlag':
                await self.sources.defer('wikidata',60)
                raise ProviderError('rate_limited',60)
            if not isinstance(body,dict) or body.get('error') or not isinstance(body.get('entities'),dict):
                raise ProviderError('bad_response')
            result={}
            resolved=entity_redirects(body,ids)
            for q in ids:
                raw=resolved.get(q)
                if raw:
                    if not isinstance(raw.get('claims'),dict):raise ProviderError('bad_response')
                    if any(not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows)
                           for prop,rows in raw['claims'].items() if prop in properties):raise ProviderError('bad_response')
                    result[q]={**{field:raw.get(field) for field in ('id','labels','aliases','lastrevid')},
                        'claims':{prop:rows for prop,rows in raw['claims'].items() if prop in properties}}
            return {'entities':result,'missing':sorted(set(ids)-set(result))}
        return await self.sources.cache.get('wikidata','game_credit_items',key,load,
            ttl=timedelta(days=7),max_stale=timedelta(days=30))

    async def game_item(self,igdb_id,slug):
        if not positive(igdb_id) or (slug is not None and (not isinstance(slug,str) or not SLUG.fullmatch(slug))):
            raise ProviderError('invalid_request')
        key=hashlib.sha256(json.dumps(['identity-v2',igdb_id,slug]).encode()).hexdigest()
        async def load():
            slug_query='''{ ?item p:P5794 ?statement. ?statement ps:P5794 '''+json.dumps(slug)+'''.
                    FILTER NOT EXISTS {?statement wikibase:rank wikibase:DeprecatedRank}
                    OPTIONAL { ?statement pq:P9043 ?id } } UNION ''' if slug is not None else ''
            query='''SELECT DISTINCT ?item ?id WHERE { '''+slug_query+'''
                { ?item p:P5794 ?statement. ?statement pq:P9043 '''+json.dumps(str(igdb_id))+'''.
                    FILTER NOT EXISTS {?statement wikibase:rank wikibase:DeprecatedRank}
                    BIND('''+json.dumps(str(igdb_id))+''' AS ?id) }
                UNION { ?item p:P9043 ?numeric. ?numeric ps:P9043 '''+json.dumps(str(igdb_id))+'''.
                    FILTER NOT EXISTS {?numeric wikibase:rank wikibase:DeprecatedRank}
                    BIND('''+json.dumps(str(igdb_id))+''' AS ?id) } } LIMIT 20'''
            body=await self.sources.request('wikidata','https://query.wikidata.org/sparql',
                params={'query':query,'format':'json'})
            rows=(body.get('results') or {}).get('bindings') if isinstance(body,dict) else None
            if not isinstance(rows,list) or len(rows)>=20:raise ProviderError('bad_response')
            matches={}
            for row in rows:
                q=(row.get('item') or {}).get('value','').rsplit('/',1)[-1]
                if not ITEM.fullmatch(q):raise ProviderError('bad_response')
                matches.setdefault(q,set()).add((row.get('id') or {}).get('value'))
            if any(any(v is not None and v!=str(igdb_id) for v in ids) for ids in matches.values()):
                raise ProviderError('identity_conflict')
            if not matches:return {'item':None}
            fetched=await self.entities(list(matches))
            items=fetched['data']['entities']
            if set(items)!=set(matches):raise ProviderError('bad_response')
            if any(not verify_game(raw,igdb_id,slug) for raw in items.values()):raise ProviderError('identity_conflict')
            canonical={raw['id'] for raw in items.values()}
            if len(canonical)!=1:raise ProviderError('identity_conflict')
            return {'item':next(iter(canonical))}
        return await self.sources.cache.get('wikidata','game_credit_bridge',key,load,
            ttl=timedelta(days=7),max_stale=timedelta(days=30))

    async def game(self,igdb_id,slug):
        bridge=await self.game_item(igdb_id,slug)
        q=bridge['data']['item']
        if not q:return {'document':None,'coverage':'unmatched','stale':bridge['stale']}
        fetched=await self.entities([q])
        game=fetched['data']['entities'].get(q)
        if not game:raise ProviderError('bad_response')
        ids=item_ids(values(game,'P674'))
        for prop in ROLES:
            for row in claims(game,prop):
                ids.update(item_ids([snak_value(row.get('mainsnak'))]))
                ids.update(item_ids(qualified(row,'P453')))
                ids.update(item_ids(qualified(row,'P407')))
        items={};stale=bridge['stale'] or fetched['stale']
        # Retain every known source role; batching is not a display/storage cap.
        ordered=sorted(ids)
        for offset in range(0,len(ordered),50):
            batch=await self.entities(ordered[offset:offset+50])
            items.update(batch['data']['entities']);stale|=batch['stale']
        doc=project(game,items,igdb_id,slug)
        return {'document':doc,'coverage':doc['coverage']['contributors'],'stale':stale}
