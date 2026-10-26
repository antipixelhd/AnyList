"""Bounded reverse discovery from explicit IMDb/Wikidata/IGDB identifiers."""
import asyncio
import hashlib
import json
import re
from datetime import timedelta

from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources,positive,SLUG
from core.game_credit_sources import GameCreditSources
from core.game_imdb_mapping import game_mapping
from core.game_imdb_source import TITLE_ID
from core.game_types import capabilities,igdb_game_class
from core.game_release_dates import FIELDS as RELEASE_FIELDS
from core.wikidata_claims import best_claims,qualified,snak_value

SIZE=20
FIELDS=('id,name,slug,summary,first_release_date,game_type.type,version_parent,parent_game.name,parent_game.game_type.type,'
    'cover.url,artworks.url,genres.name,platforms.name,platforms.platform_family.name,'
    'involved_companies.developer,involved_companies.publisher,involved_companies.supporting,involved_companies.porting,'
    'involved_companies.company.name,involved_companies.company.logo.url,involved_companies.company.country,'
    'external_games.uid,external_games.url,external_games.external_game_source.name,'+RELEASE_FIELDS)


def game_reference(item):
    """Numeric claims/qualifiers outrank renamed slugs; ambiguity stays unresolved."""
    rows=best_claims(item,'P5794')
    ids=[snak_value(row.get('mainsnak')) for row in best_claims(item,'P9043')]
    ids += [value for row in rows for value in qualified(row,'P9043')]
    ids=[value for value in ids if value is not None]
    if ids:
        if any(not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]*',value) for value in ids) or len(set(ids))!=1:
            return None
        value=int(ids[0])
        return {'id':value} if value<=2147483647 else None
    slugs=[snak_value(row.get('mainsnak')) for row in rows]
    slugs=[value for value in slugs if value is not None]
    if not slugs or any(not isinstance(value,str) or not SLUG.fullmatch(value) for value in slugs) or len(set(slugs))!=1:return None
    return {'slug':slugs[0]}


def project(title_id,item,raw,games):
    mapped=game_mapping(item,str(raw['id']),raw.get('slug'))
    if mapped['status']!='verified' or mapped['imdb_id']!=title_id:
        return {'id':title_id,'status':'conflict'}
    kind=igdb_game_class(raw)
    if raw.get('version_parent') or kind=='port':
        # Preserve source identity; an edition-specific IMDb record must not
        # replace the base game's primary IMDb identity.
        return {'id':title_id,'status':'unresolved','reason':'edition_or_port','igdb_id':raw['id']}
    if kind=='unknown':return {'id':title_id,'status':'unresolved','reason':'unknown_type','igdb_id':raw['id']}
    if not capabilities({'game_class':kind}).searchable:
        return {'id':title_id,'status':'excluded','reason':kind,'igdb_id':raw['id']}
    parent=None
    if kind=='expansion':
        value=raw.get('parent_game');native=value.get('id') if isinstance(value,dict) else value
        parent=games.get(native)
        if not parent or parent.get('version_parent') or not capabilities({'game_class':igdb_game_class(parent)}).independent:
            return {'id':title_id,'status':'unresolved','reason':'expansion_parent','igdb_id':raw['id']}
    doc=normalize_igdb(raw)
    if parent:
        for edge in doc['relationships']:
            if edge['relation']=='expansion' and not edge.get('reverse'):edge['target']=normalize_igdb(parent)['work']
    doc['coverage']={'person_discovery':True,'credits_complete':False,'characters_complete':False}
    return {'id':title_id,'status':'verified','igdb_id':raw['id'],'document':doc,
        'evidence':mapped['evidence']}


class GameIMDbDiscoverySource:
    def __init__(self,sessions,*,sources=None,wiki=None):
        self.sources=sources or GameBrowseSources(sessions)
        self.wiki=wiki or GameCreditSources(self.sources)

    async def items(self,ids):
        key=hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
        async def load():
            values=' '.join(json.dumps(value) for value in sorted(ids))
            query=('SELECT DISTINCT ?title ?item WHERE { VALUES ?title { '+values+' } '
                '?item p:P345 ?statement. ?statement ps:P345 ?title; wikibase:rank ?rank. '
                'FILTER(?rank != wikibase:DeprecatedRank) } LIMIT 401')
            # Documented form POST keeps batch queries out of URL length limits.
            raw=await self.sources.api.http.request('wikidata','POST','https://query.wikidata.org/sparql',
                data={'query':query,'format':'json'},headers={
                    'User-Agent':'AnyList catalogue/1 (https://github.com/antipixelhd/AnyList)',
                    'Accept':'application/sparql-results+json'})
            rows=(raw.get('results') or {}).get('bindings') if isinstance(raw,dict) else None
            if not isinstance(rows,list) or len(rows)>=401:raise ProviderError('bad_response')
            result={value:[] for value in ids}
            for row in rows:
                if not isinstance(row,dict) or not all(isinstance(row.get(key),dict) for key in ('title','item')):raise ProviderError('bad_response')
                title=row['title'].get('value');url=row['item'].get('value')
                match=re.fullmatch(r'https?://www\.wikidata\.org/entity/(Q[1-9][0-9]*)',url) if isinstance(url,str) else None
                if not isinstance(title,str) or title not in result or not match:raise ProviderError('bad_response')
                item=match[1]
                if item not in result[title]:result[title].append(item)
            return {'items':result}
        return await self.sources.cache.get('wikidata','game_imdb_reverse',key,load,
            ttl=timedelta(days=7),max_stale=timedelta(days=30))

    async def games(self,ids=(),slugs=()):
        clauses=[]
        if ids:clauses.append('id=('+','.join(map(str,sorted(ids)))+')')
        if slugs:clauses.append('slug=('+','.join(json.dumps(value) for value in sorted(slugs))+')')
        if not clauses:return {}
        raw=await self.sources.igdb('games',f'fields {FIELDS}; where ({" | ".join(clauses)}); limit 100;')
        if not isinstance(raw,list) or len(raw)>100:raise ProviderError('bad_response')
        result={}
        for row in raw:
            if (not isinstance(row,dict) or not positive(row.get('id')) or not isinstance(row.get('name'),str)
                or not row['name'].strip() or row['id'] in result
                or row['id'] not in ids and row.get('slug') not in slugs):raise ProviderError('bad_response')
            result[row['id']]=row
        return result

    async def page(self,ids):
        if not isinstance(ids,list) or not ids or len(ids)>SIZE or any(
            not isinstance(value,str) or not TITLE_ID.fullmatch(value) for value in ids) or len(set(ids))!=len(ids):raise ProviderError('invalid_request')
        reverse=await self.items(ids);candidates=reverse['data']['items']
        qids=sorted({q for values in candidates.values() for q in values})
        if len(qids)>50:raise ProviderError('coverage_limited')
        fetched=await self.wiki.entities(qids) if qids else {'data':{'entities':{}},'stale':False}
        entities=fetched['data']['entities'];references={};outcomes={}
        for native,items in candidates.items():
            found=[entities.get(q) for q in items]
            if not items:outcomes[native]={'id':native,'status':'unmapped'};continue
            if any(value is None for value in found):outcomes[native]={'id':native,'status':'unresolved','reason':'missing_entity'};continue
            roots={value['id']:value for value in found}
            if len(roots)!=1:outcomes[native]={'id':native,'status':'conflict'};continue
            item=next(iter(roots.values()));reference=game_reference(item)
            if not reference:outcomes[native]={'id':native,'status':'unresolved','reason':'igdb_reference'};continue
            references[native]=(item,reference)
        games=await self.games({value['id'] for _,value in references.values() if 'id' in value},
            {value['slug'] for _,value in references.values() if 'slug' in value})
        parents={value['parent_game']['id'] for value in games.values() if igdb_game_class(value)=='expansion'
            and isinstance(value.get('parent_game'),dict) and positive(value['parent_game'].get('id'))}-set(games)
        if parents:games.update(await self.games(parents))
        def resolve():
            for native,(item,reference) in references.items():
                matches=[value for value in games.values() if value['id']==reference.get('id') or value.get('slug')==reference.get('slug') and 'slug' in reference]
                outcomes[native]=project(native,item,matches[0],games) if len(matches)==1 else {
                    'id':native,'status':'unresolved','reason':'igdb_metadata'}
            return [outcomes[native] for native in ids]
        return {'outcomes':await asyncio.to_thread(resolve),'stale':reverse['stale'] or fetched['stale'],
            'fetched_at':reverse.get('fetched_at')}
