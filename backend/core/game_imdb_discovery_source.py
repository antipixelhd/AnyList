"""Bounded reverse discovery from explicit IMDb/Wikidata/IGDB identifiers."""
import asyncio
import hashlib
import json
import re
from time import monotonic
from datetime import timedelta

from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources,positive,SLUG
from core.game_credit_sources import GameCreditSources
from core.game_imdb_mapping import game_mapping
from core.game_imdb_source import TITLE_ID
from core.game_types import capabilities,igdb_game_class,CREDIT_CHILDREN
from core.game_release_dates import FIELDS as RELEASE_FIELDS
from core.wikidata_claims import best_claims,qualified,snak_value

SIZE=20
FIELDS=('id,name,slug,summary,first_release_date,game_type.type,version_parent,parent_game.name,parent_game.game_type.type,'
    'cover.url,artworks.url,artworks.width,artworks.height,screenshots.url,screenshots.width,screenshots.height,genres.name,platforms.name,platforms.platform_family.name,'
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
        return {'id':title_id,'status':'conflict','reason':'explicit_identity_conflict'}
    return project_game(title_id,raw,games,mapped['evidence'])


def project_game(title_id,raw,games,evidence):
    kind=igdb_game_class(raw)
    if raw.get('version_parent') or kind=='port':
        # Preserve source identity; an edition-specific IMDb record must not
        # replace the base game's primary IMDb identity.
        return {'id':title_id,'status':'unresolved','reason':'edition_or_port','igdb_id':raw['id']}
    if kind=='unknown':return {'id':title_id,'status':'unresolved','reason':'unknown_type','igdb_id':raw['id']}
    if not capabilities({'game_class':kind}).searchable and kind not in CREDIT_CHILDREN:
        return {'id':title_id,'status':'excluded','reason':kind,'igdb_id':raw['id'],'game_title':raw['name'],'evidence':evidence}
    parent=None
    if kind in CREDIT_CHILDREN:
        value=raw.get('parent_game');native=value.get('id') if isinstance(value,dict) else value
        parent=games.get(native)
        if not parent or parent.get('version_parent') or igdb_game_class(parent) not in {'game','standalone_expansion','remake','remaster','expanded_game'}:
            return {'id':title_id,'status':'unresolved','reason':'expansion_parent' if kind=='expansion' else 'credit_parent','igdb_id':raw['id']}
    doc=normalize_igdb(raw)
    if parent:
        for edge in doc['relationships']:
            if edge['relation']==kind and not edge.get('reverse'):edge['target']=normalize_igdb(parent)['work']
    doc['coverage']={'person_discovery':True,'credits_complete':False,'characters_complete':False}
    return {'id':title_id,'status':'verified','igdb_id':raw['id'],'document':doc,
        'evidence':evidence}


class GameIMDbDiscoverySource:
    def __init__(self,sessions,*,sources=None,wiki=None):
        self.sessions=sessions
        self.sources=sources or GameBrowseSources(sessions)
        self.wiki=wiki or GameCreditSources(self.sources)

    async def items(self,ids):
        if self.sessions is not None:
            from core.game_mapping_index import lookup
            records=await lookup(self.sessions,'wikidata',imdb_ids=ids,max_age=timedelta(days=7))
            if records and len(records)<101 and all(row['coverage'].get('scope')=='complete_wikidata_dump' for row in records):
                known={native:[] for native in ids}
                for record in records:
                    item=record['value']
                    claims={snak_value(row.get('mainsnak')) for row in best_claims(item,'P345')}
                    for native in ids:
                        if native in claims:known[native].append(item['id'])
                if all(known.values()):
                    return {'data':{'items':known},'stale':False,'fetched_at':records[0]['observed_at'].isoformat()}
        key=hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()
        async def load():
            values=' '.join(json.dumps(value) for value in sorted(ids))
            query=('SELECT DISTINCT ?title ?item WHERE { VALUES ?title { '+values+' } '
                '?item p:P345 ?statement. ?statement ps:P345 ?title; wikibase:rank ?rank. '
                'FILTER(?rank != wikibase:DeprecatedRank) } LIMIT 401')
            # Documented form POST keeps batch queries out of URL length limits.
            try:
                raw=await self.sources.api.http.request('wikidata','POST','https://query.wikidata.org/sparql',
                    data={'query':query,'format':'json'},headers={
                        'User-Agent':'AnyList catalogue/1 (https://github.com/antipixelhd/AnyList)',
                        'Accept':'application/sparql-results+json'})
            except ProviderError as error:
                if error.code not in ('rate_limited','unavailable','timeout'):raise
                return await self.indexed_items(ids)
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
        result=await self.sources.cache.get('wikidata','game_imdb_reverse',key,load,
            ttl=timedelta(days=7),max_stale=timedelta(days=30))
        return {**result,'support_snapshots':[{'kind':'game_imdb_reverse','key':key,'fetched_at':result['fetched_at']}]
            if result.get('fetched_at') else []}

    async def entities(self,ids):
        if self.sessions is None:return await self.wiki.entities(ids)
        from core.game_mapping_index import lookup
        records=await lookup(self.sessions,'wikidata',natives=ids,max_age=timedelta(days=7))
        known={record['native']:record['value'] for record in records}
        missing=set(ids)-set(known)
        if missing:
            result=await self.wiki.entities(missing)
            key=hashlib.sha256(json.dumps(['identity-v2',sorted(missing)]).encode()).hexdigest()
            return {**result,'data':{'entities':{**known,**result['data']['entities']}},
                'support_snapshots':[{'kind':'game_credit_items','key':key,'fetched_at':result['fetched_at']}]
                    if result.get('fetched_at') else []}
        return {'data':{'entities':known},'stale':False}

    async def indexed_items(self,ids):
        # WikibaseCirrusSearch supports exact external-ID statement queries.
        # Search only proposes QIDs; actual nondeprecated P345 claims below,
        # and the existing bidirectional IGDB mapping, still prove identity.
        raw=await self.sources.api.http.request('wikidata','GET','https://www.wikidata.org/w/api.php',params={
            'action':'query','list':'search','srsearch':'haswbstatement:'+'|'.join('P345='+value for value in sorted(ids)),
            'srnamespace':0,'srlimit':50,'srprop':'','format':'json','maxlag':5},headers={
            'User-Agent':'AnyList catalogue/1 (https://github.com/antipixelhd/AnyList)'})
        rows=(raw.get('query') or {}).get('search') if isinstance(raw,dict) else None
        if isinstance(raw,dict) and (raw.get('error') or {}).get('code')=='maxlag':
            raise ProviderError('rate_limited',60)
        if not isinstance(rows,list) or raw.get('error') or raw.get('continue') or len(rows)>=50:
            raise ProviderError('coverage_limited')
        qids=[row.get('title') for row in rows if isinstance(row,dict)]
        if len(qids)!=len(rows) or any(not isinstance(q,str) or not re.fullmatch(r'Q[1-9][0-9]*',q) for q in qids) or len(set(qids))!=len(qids):
            raise ProviderError('bad_response')
        fetched=await self.entities(qids) if qids else {'data':{'entities':{}},'stale':False}
        if fetched.get('stale'):raise ProviderError('stale_mapping')
        entities=fetched['data']['entities'];result={value:[] for value in ids}
        if any(q not in entities for q in qids):raise ProviderError('incomplete_cache')
        for q,item in entities.items():
            claims={snak_value(row.get('mainsnak')) for row in best_claims(item,'P345')}
            for native in ids:
                if native in claims:result[native].append(q)
        return {'items':result}

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

    async def page(self,ids,*,titles=None):
        if titles is None:
            return await self.explicit_page(ids)
        from core.game_title_candidates import retrieve
        from core.game_title_match import decide
        from core.game_mapping_index import lookup,wikidata_generation
        if not isinstance(titles,dict) or set(titles)!=set(ids):raise ProviderError('invalid_request')
        wiki_generation=await wikidata_generation(self.sessions)
        try:
            result=await self.explicit_page(ids)
        except ProviderError as error:
            if error.code not in ('rate_limited','unavailable','timeout','retry_pending','stale_mapping'):raise
            result={'outcomes':[{'id':native,'status':'unresolved','reason':error.code} for native in ids],'stale':False}
        outputs=[]
        deep_deadline=monotonic()+8
        indexed={row['native']:row for row in await lookup(self.sessions,'imdb',natives=ids)}
        for outcome in result['outcomes']:
            if outcome['status'] in ('verified','excluded') or outcome.get('reason')=='explicit_identity_conflict':
                outputs.append(outcome);continue
            native=outcome['id'];title=dict(titles[native])
            if title.get('id')!=native or not isinstance(title.get('title'),str) or not title['title'].strip():
                raise ProviderError('bad_response')
            local=indexed.get(native)
            if local:
                title['aliases']=local['value'].get('aliases',[])
                if title.get('year') is None:title['year']=local['value'].get('year')
            try:acquired=await retrieve(self.sources,title)
            except ProviderError as error:
                outputs.append({'id':native,'status':'unresolved','reason':error.code,
                    **({'retry_after':60} if error.code!='coverage_limited' else {})});continue
            data=acquired['data']
            decision=decide(title,data['rows'],complete=data['complete'] and not acquired['stale'],branches=data['branches'])
            deeper=[]
            if decision['status']=='unresolved' and data['complete'] and not acquired['stale']:
                from core.game_match_support import corroborate
                budget=min(4,deep_deadline-monotonic())
                try:
                    if budget<=.05:
                        outputs.append({'id':native,'status':'unresolved','reason':'corroboration_deferred','retry_after':2});continue
                    async with asyncio.timeout(budget):extra,deeper=await corroborate(self.sources,title,data['rows'])
                    decision=decide(title,data['rows'],complete=True,branches=data['branches'],corroboration=extra)
                except (ProviderError,TimeoutError) as error:
                    outputs.append({'id':native,'status':'unresolved','reason':getattr(error,'code','timeout'),'retry_after':60});continue
            if decision['status']!='verified':
                outputs.append({'id':native,**decision});continue
            games={row['id']:row for row in data['rows']}
            raw=games[decision['igdb_id']]
            parent=raw.get('parent_game')
            if igdb_game_class(raw) in CREDIT_CHILDREN and isinstance(parent,dict) and positive(parent.get('id')) and parent['id'] not in games:
                games.update(await self.games({parent['id']}))
            evidence={**decision['evidence'],'method':'title_match','source_outcome':{key:value for key,value in outcome.items() if key in ('status','reason')},
                'direction':'imdb_to_igdb','scope':'primary_title','dependencies':[f'imdb:{native}',f'igdb:{raw["id"]}',
                    f'wikidata-imdb:{native}',*[f'wikidata:{qid}' for qid in result.get('consulted',{}).get(native,[])],
                    f'igdb-query:{data.get("scope_key","")}',*[f'igdb-name:{name}' for name in data.get('scope_names',[])],
                    *[f'support:{row["provider"]}:{row["kind"]}:{row["key"]}' for row in deeper]],
                'support':{'imdb_generation':local['generation'] if local else None,
                    'candidate_key':data.get('scope_key'),'candidate_fingerprint':data.get('scope_fingerprint'),
                    'check_wikidata_generation':True,'wikidata_generation':wiki_generation,
                    'wiki_snapshots':result.get('support_snapshots',[]),'snapshots':deeper}}
            projected=project_game(native,raw,games,evidence)
            projected['binding_source']='matched';outputs.append(projected)
        return {**result,'outcomes':outputs}

    async def explicit_page(self,ids):
        if not isinstance(ids,list) or not ids or len(ids)>SIZE or any(
            not isinstance(value,str) or not TITLE_ID.fullmatch(value) for value in ids) or len(set(ids))!=len(ids):raise ProviderError('invalid_request')
        reverse=await self.items(ids);candidates=reverse['data']['items']
        qids=sorted({q for values in candidates.values() for q in values})
        if len(qids)>50:raise ProviderError('coverage_limited')
        fetched=await self.entities(qids) if qids else {'data':{'entities':{}},'stale':False}
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
        parents={value['parent_game']['id'] for value in games.values() if igdb_game_class(value) in CREDIT_CHILDREN
            and isinstance(value.get('parent_game'),dict) and positive(value['parent_game'].get('id'))}-set(games)
        if parents:games.update(await self.games(parents))
        def resolve():
            for native,(item,reference) in references.items():
                matches=[value for value in games.values() if value['id']==reference.get('id') or value.get('slug')==reference.get('slug') and 'slug' in reference]
                outcomes[native]=project(native,item,matches[0],games) if len(matches)==1 else {
                    'id':native,'status':'unresolved','reason':'igdb_metadata'}
            return [outcomes[native] for native in ids]
        return {'outcomes':await asyncio.to_thread(resolve),'stale':reverse['stale'] or fetched['stale'],
            'fetched_at':reverse.get('fetched_at'),'consulted':candidates,
            'support_snapshots':[*reverse.get('support_snapshots',[]),*fetched.get('support_snapshots',[])]}
