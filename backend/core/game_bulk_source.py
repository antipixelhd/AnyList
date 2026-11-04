"""Compact public portfolio acquisition; never certifies IMDb identities."""
import asyncio
import json
import re
import httpx
from datetime import datetime, timezone
from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
from core.game_title_match import spelling, title_formats
from core.game_types import igdb_game_class, INDEPENDENT, MOBILE_PLATFORMS
from core.game_imdb_source import IMDbGameSource

independent=INDEPENDENT-{'port'}
PARENT_FIELDS = 'id,name,game_type,platforms,cover.image_id,themes'
FIELDS = ('id,name,slug,alternative_names.name,game_type,first_release_date,'
    'release_dates.y,release_dates.date_format.format,release_dates.status.name,'
    'platforms,ports.game_type,ports.platforms,cover.image_id,themes,' +
    ','.join(prefix + field for prefix in ('parent_game.', 'version_parent.') for field in PARENT_FIELDS.split(',')))
QUERY = ('query($id:ID!, $after:ID) { name(id:$id) { id '
    'credits(first:250,after:$after,filter:{titleType:"videoGame"}) { '
    'total pageInfo { hasNextPage endCursor } edges { node { category { id } '
    'title { id titleText { text } titleType { id } releaseYear { year } primaryImage { url } } } } } } }')


def header_query(source):
    source_names = set()
    written = 'One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen Twenty'.split()
    for row in source:
        original = row['title']
        forms = {original, original.replace(':', ''), original.replace(',', '')}
        for form in list(forms):
            forms.add(re.sub(r'\b(Chapter|Part|Episode)\s+([1-9]|1[0-9]|20)\b',
                lambda match: match[1] + ' ' + written[int(match[2]) - 1], form))
        source_names.update(' '.join(form.split()) for form in forms)
    names = '(' + ','.join(json.dumps(name, ensure_ascii=True) for name in sorted(source_names)) + ')'
    slugs = set()
    extra_slugs = set()
    numbers = {roman: str(index) for index, roman in enumerate('i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx'.split(), 1)}
    for row in source:
        form = spelling(row['title'])
        slug = re.sub(r'[^a-z0-9]+', '-', form).strip('-')
        if slug:
            slugs.add(slug)
            if row['year'] and len(form.split()) <= 4: slugs.add(slug + '-' + str(row['year']))
        alternative = ' '.join(numbers.get(word, word) for word in form.split())
        alternative = alternative.replace(' & ', ' and ')
        alt_slug = re.sub(r'[^a-z0-9]+', '-', alternative).strip('-')
        if alt_slug and alt_slug != slug:
            extra_slugs.add(alt_slug)
            if row['year']: extra_slugs.add(alt_slug + '-' + str(row['year']))
        if '&' in row['title']:
            extra_slugs.add(re.sub(r'[^a-z0-9]+', '-', spelling(row['title'].replace('&', ' and '))).strip('-'))
    slug_values = '(' + ','.join(json.dumps(value) for value in sorted(slugs)) + ')'
    query = f'fields {FIELDS}; where name={names} | slug={slug_values}; limit 500;'
    for extra in sorted(extra_slugs - slugs):
        proposed = query.replace(' | slug=(', ' | slug=(' + json.dumps(extra) + ',')
        if len(proposed.encode()) <= 30000: query = proposed
    return query



def years(row):
    dates = row.get('release_dates') or []
    result = set()
    for date in dates:
        form = (date.get('date_format') or {}).get('format')
        year = date.get('y')
        # Match source credit years, including early-access release years. This
        # does not claim a day precision or a final-release premiere date.
        if form and form != 'TBD' and type(year) is int:
            result.add(year)
    if not dates and isinstance(row.get('first_release_date'), int):
        result.add(datetime.fromtimestamp(row['first_release_date'], timezone.utc).year)
    return result


def root(row):
    if row.get('version_parent'): return row['version_parent']
    if igdb_game_class(row) in {'port', 'dlc', 'expansion', 'update', 'episode'}:
        return row.get('parent_game')
    parent = row.get('parent_game')
    if (igdb_game_class(row) == 'expanded_game' and isinstance(parent, dict)
            and igdb_game_class(parent) in independent
            and spelling(row['name']) == spelling(parent.get('name'))):
        return parent
    return row


def platforms(row):
    result=set(row.get('platforms') or [])
    if igdb_game_class(row)!='port':
        for port in row.get('ports') or []:
            if igdb_game_class(port)=='port':result.update(port.get('platforms') or [])
    return result


def eligible(row,kind):
    available=platforms(row)
    if not available or all(str(value) in MOBILE_PLATFORMS for value in available):return False
    if 42 in row.get('themes',[]):return False
    game_class=igdb_game_class(row)
    if game_class in independent:return True
    return kind!='actor' and game_class=='expansion' and isinstance(row.get('parent_game'),dict) and igdb_game_class(row['parent_game']) in independent


def person_candidates(source,rows,kind):
    index={}
    for row in rows:
        for name in [row['name'],*[alias['name'] for alias in row.get('alternative_names',[])]]:
            for key in {spelling(name),*title_formats(name)}:index.setdefault(key,{})[row['id']]=row
    result=[]
    from core.game_credit_roles import SOURCE_ACTING
    for title in source:
        roles=[role for role in title['roles'] if role!='thanks' and (role in SOURCE_ACTING)==(kind=='actor')]
        if not roles or type(title.get('year')) is not int:continue
        matching={}
        for key in {spelling(title['title']),*title_formats(title['title'])}:matching.update(index.get(key,{}))
        plausible=[row for row in matching.values() if title['year'] in years(row) or not years(row)]
        roots=[root(row) if kind=='actor' else studio_root(row) for row in plausible]
        if not roots or any(not isinstance(row,dict) or not row.get('id') for row in roots):continue
        if len({row['id'] for row in roots})!=1 or not any(title['year'] in years(row) for row in plausible):continue
        winner=roots[0]
        if eligible(winner,kind):
            result.append({'game':winner,'source':title['imdb_id'],'roles':['actor'] if kind=='actor' else roles})
    return result


def studio_root(row):
    seen=set()
    for _ in range(4):
        if not isinstance(row,dict) or not row.get('id') or row['id'] in seen:return None
        seen.add(row['id'])
        if row.get('version_parent'):row=row['version_parent']
        elif igdb_game_class(row)=='port':row=row.get('parent_game')
        else:return row
    return None


class GameBulkSource:
    def __init__(self,sessions,client=None):
        self.sessions=sessions
        self.http=ProviderHTTP(client=client,session_factory=sessions)
        self.api=CatalogueProviders(self.http)

    async def acquire(self,native,kind):
        async with httpx.AsyncClient(timeout=5) as client:
            return await GameBulkSource(self.sessions,client)._acquire(native,kind)

    async def _acquire(self,native,kind):
        """A failed/expired deadline returns usable positives, never negatives."""
        self.api.validate('igdb','game')
        source=[];rows=[];tasks=[];candidates=[];complete=False
        try:
            async with asyncio.timeout(3.85):
                if kind=='studio':
                    from core.game_company_source import ROLES
                    after=0
                    compact='id,name,game_type,platforms,ports.game_type,ports.platforms,cover.image_id,first_release_date,themes'
                    fields=','.join('game.'+prefix+field for prefix in ('','parent_game.','version_parent.',
                        'parent_game.parent_game.','parent_game.version_parent.',
                        'version_parent.parent_game.','version_parent.version_parent.') for field in compact.split(','))
                    for _ in range(20):
                        page=await self.api.igdb('involved_companies',f'fields id,company,developer,publisher,supporting,porting,{fields}; where company={int(native)} & id>{after}; sort id asc; limit 500;')
                        if not isinstance(page,list) or len(page)>500:raise ProviderError('bad_response')
                        for record in page:
                            if record.get('company')!=int(native) or type(record.get('id')) is not int or record['id']<=after:raise ProviderError('bad_response')
                            after=record['id'];game=record.get('game')
                            if not isinstance(game,dict):raise ProviderError('bad_response')
                            available=platforms(game)
                            if available and all(str(value) in MOBILE_PLATFORMS for value in available):continue
                            winner=studio_root(game)
                            roles=[role for role in ROLES if record.get(role) is True]
                            if winner and roles and eligible(winner,kind):candidates.append({'game':winner,'source':str(record['id']),'roles':roles})
                        if len(page)<500:complete=True;break
                else:
                    imdb=IMDbGameSource(self.http);after=None;seen=set();received=0;expected=None
                    async def headers(batch):
                        data=await self.api.igdb('games',header_query(batch))
                        if not isinstance(data,list) or len(data)>=500:raise ProviderError('coverage_limited')
                        rows.extend(data)
                    for _ in range(40):
                        body=await imdb.query(QUERY,{'id':native,'after':after})
                        from core.game_imdb_source import page as parse_page
                        value=parse_page((body.get('name') or {}).get('credits'),250)
                        if (body.get('name') or {}).get('id')!=native:raise ProviderError('identity_conflict')
                        if expected is not None and expected!=value['total']:raise ProviderError('changed_connection')
                        expected=value['total'];received+=len(value['rows']);batch={}
                        for item in value['rows']:
                            title=item.get('title') or {}
                            if (title.get('titleType') or {}).get('id')!='videoGame':raise ProviderError('bad_response')
                            entry=batch.setdefault(title['id'],{'imdb_id':title['id'],'title':title['titleText']['text'],'year':(title.get('releaseYear') or {}).get('year'),'roles':[]})
                            entry['roles'].append(item['category']['id'])
                        source.extend(batch.values());tasks.append(asyncio.create_task(headers(list(batch.values()))))
                        if not value['has_next']:
                            if received!=expected:raise ProviderError('incomplete_cache')
                            complete=True;break
                        after=value['cursor']
                        if after in seen:raise ProviderError('bad_response')
                        seen.add(after)
                    await asyncio.gather(*tasks)
                    names={key for row in rows for name in [row['name'],*[alias['name'] for alias in row.get('alternative_names',[])]] for key in {spelling(name),*title_formats(name)}}
                    missing=[row['title'] for row in source if not ({spelling(row['title']),*title_formats(row['title'])}&names)]
                    if missing:
                        values='('+','.join(json.dumps(name) for name in missing)+')'
                        extra=await self.api.igdb('games',f'fields {FIELDS}; where alternative_names.name={values}; limit 500;')
                        if isinstance(extra,list) and len(extra)<500:rows.extend(extra)
        except (ProviderError,TimeoutError):complete=False
        finally:
            for task in tasks:
                if not task.done():task.cancel()
            if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        if kind!='studio':candidates=await asyncio.to_thread(person_candidates,source,rows,kind)
        return candidates,complete
