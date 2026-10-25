"""Direct IMDb website queries; no authentication, fallback transport or catalogue.

Technical access is not a supported API guarantee or permission to reuse data.
The caller owns durable component leases, cached evidence and authorization.
"""
import re
from urllib.parse import quote

from core.catalogue_providers import ProviderError, ProviderHTTP

TITLE_ID = re.compile(r'tt[0-9]+\Z')
PERSON_ID = re.compile(r'nm[0-9]+\Z')
ENDPOINT = 'https://caching.graphql.imdb.com/'
HEADERS = {'Content-Type':'application/json', 'User-Agent':'AnyList catalogue/1',
           'Origin':'https://www.imdb.com', 'Referer':'https://www.imdb.com/'}
TITLE_FIELDS = 'id titleText { text } titleType { id } releaseYear { year }'
TITLE_QUERY = 'query GameIdentity($id: ID!) { title(id: $id) { '+TITLE_FIELDS+' } }'
TITLE_CREDITS = '''query GameCredits($id: ID!, $after: ID) {
  title(id: $id) {
    '''+TITLE_FIELDS+'''
    credits(first: 100, after: $after) {
      total pageInfo { hasNextPage endCursor }
      edges { node {
        name { id nameText { text } } category { id text }
        ... on Cast { characters { name } attributes { text } }
        ... on Crew { jobs { text } }
      } }
    }
  }
}'''
PERSON_CREDITS = '''query GamePersonCredits($id: ID!, $after: ID, $profile: Boolean!) {
  name(id: $id) {
    id nameText { text }
    birthDate @include(if: $profile) { dateComponents { year month day } }
    deathDate @include(if: $profile) { dateComponents { year month day } }
    primaryProfessions @include(if: $profile) { category { text } }
    bio @include(if: $profile) { text { plainText } }
    credits(first: 50, after: $after) {
      total pageInfo { hasNextPage endCursor }
      edges { node {
        title { id titleText { text } titleType { id } releaseYear { year } }
        category { id }
      } }
    }
  }
}'''


def identifier(value, person=False):
    if not isinstance(value,str) or not (PERSON_ID if person else TITLE_ID).fullmatch(value):
        raise ProviderError('invalid_request')
    return value


def game_header(raw, native):
    if not isinstance(raw,dict) or raw.get('id')!=native or (raw.get('titleType') or {}).get('id')!='videoGame':
        raise ProviderError('identity_conflict')
    name=(raw.get('titleText') or {}).get('text')
    if not isinstance(name,str) or not name.strip():raise ProviderError('bad_response')
    return {key:raw.get(key) for key in ('id','titleText','titleType','releaseYear')}


def page(raw, maximum):
    if not isinstance(raw,dict):raise ProviderError('bad_response')
    total, edges, info = raw.get('total'), raw.get('edges'), raw.get('pageInfo')
    if isinstance(total,bool) or not isinstance(total,int) or total<0 or not isinstance(edges,list) or len(edges)>maximum:
        raise ProviderError('bad_response')
    if not isinstance(info,dict) or type(info.get('hasNextPage')) is not bool:
        raise ProviderError('bad_response')
    cursor=info.get('endCursor')
    if cursor is not None and (not isinstance(cursor,str) or not 1<=len(cursor)<=4096):
        raise ProviderError('bad_response')
    if info['hasNextPage'] and (not cursor or not edges):raise ProviderError('bad_response')
    if any(not isinstance(edge,dict) or not isinstance(edge.get('node'),dict) for edge in edges):
        raise ProviderError('bad_response')
    return {'rows':[edge['node'] for edge in edges], 'total':total,
            'has_next':info['hasNextPage'], 'cursor':cursor}


class IMDbGameSource:
    def __init__(self,http=None):
        self.http=http or ProviderHTTP()

    async def query(self,query,variables):
        # Ordinary website headers are not credentials. Never forward a failed
        # request, its headers or its body to another host or challenge solver.
        body=await self.http.request('imdb','POST',ENDPOINT,cache=False,
            headers=HEADERS,json={'query':query,'variables':variables})
        if not isinstance(body,dict) or body.get('errors') or not isinstance(body.get('data'),dict):
            raise ProviderError('graphql_error')
        fields=((body.get('extensions') or {}).get('entitlements') or {}).get('fields') or {}
        denied=[key for key,value in fields.items() if value=='DENY'] if isinstance(fields,dict) else []
        if 'credits(' in query and any(key.startswith(('Title.credits','Name.credits')) for key in denied):
            raise ProviderError('not_entitled')
        return {**body['data'],'_denied_fields':denied}

    async def title(self,native):
        identifier(native)
        raw=await self.query(TITLE_QUERY,{'id':native})
        return game_header(raw.get('title'),native)

    async def portraits(self,natives):
        """Small optional alias batch; never fetch a filmography for a headshot."""
        if not isinstance(natives,list) or not 1<=len(natives)<=16:
            raise ProviderError('invalid_request')
        for native in natives:identifier(native,person=True)
        if len(set(natives))!=len(natives):raise ProviderError('invalid_request')
        variables={f'id{i}':native for i,native in enumerate(natives)}
        query='query GamePersonImages('+', '.join(f'${key}: ID!' for key in variables)+') { '+ ' '.join(
            f'p{i}: name(id: $id{i}) {{ id primaryImage {{ url width height }} }}' for i in range(len(natives)))+' }'
        body=await self.query(query,variables)
        if any(field=='Name.primaryImage' or field.startswith('Name.primaryImage.') for field in body.get('_denied_fields',[])):
            raise ProviderError('not_entitled')
        from urllib.parse import urlsplit
        result={}
        for i,native in enumerate(natives):
            person=body.get(f'p{i}')
            if not isinstance(person,dict) or person.get('id')!=native:raise ProviderError('identity_conflict')
            if 'primaryImage' not in person:raise ProviderError('bad_response')
            raw=person['primaryImage']
            result[native]={'image':None,'width':None,'height':None}
            if raw is None:continue
            if not isinstance(raw,dict):raise ProviderError('bad_response')
            url=raw.get('url')
            try:parsed=urlsplit(url) if isinstance(url,str) else None
            except ValueError:parsed=None
            if (not parsed or parsed.scheme!='https' or parsed.netloc!='m.media-amazon.com'
                or not re.fullmatch(r'/images/M/[^/]+\._V1[^.]*\.(?:jpg|jpeg|png)',parsed.path) or parsed.query or parsed.fragment
                or not all(type(raw.get(key)) is int and 0<raw[key]<=100000 for key in ('width','height'))):
                raise ProviderError('bad_response')
            result[native]={'image':url,'width':raw['width'],'height':raw['height']}
        return result

    async def credits(self,native,after=None,*,person=False):
        identifier(native,person)
        if after is not None and (not isinstance(after,str) or not 1<=len(after)<=4096):
            raise ProviderError('invalid_request')
        body=await self.query(PERSON_CREDITS if person else TITLE_CREDITS,
            {'id':native,'after':after,**({'profile':after is None} if person else {})})
        raw=body.get('name' if person else 'title')
        if person:
            if not isinstance(raw,dict) or raw.get('id')!=native or not isinstance((raw.get('nameText') or {}).get('text'),str):
                raise ProviderError('identity_conflict')
            header={key:raw.get(key) for key in ('id','nameText','birthDate','deathDate','primaryProfessions','bio')}
        else:
            header=game_header(raw,native)
        if body.get('_denied_fields'):
            header['unavailable_fields']=body['_denied_fields']
        return {'header':header, **page(raw.get('credits'),50 if person else 100)}

    async def candidates(self,term):
        if not isinstance(term,str) or not 2<=len(term.strip())<=100:raise ProviderError('invalid_request')
        body=await self.http.request('imdb','GET',
            'https://v3.sg.media-imdb.com/suggestion/titles/x/'+quote(term.strip(),safe='')+'.json',
            headers=HEADERS)
        rows=body.get('d') if isinstance(body,dict) else None
        if not isinstance(rows,list):raise ProviderError('bad_response')
        # These are review candidates only. A selected record is validated by
        # GraphQL before a reviewer can explicitly bind it to an IGDB entity.
        return [{'id':row['id'],'name':row.get('l'),'year':row.get('y')}
            for row in rows if isinstance(row,dict) and isinstance(row.get('id'),str)
            and TITLE_ID.fullmatch(row['id']) and isinstance(row.get('l'),str)
            and (row.get('qid')=='videoGame' or row.get('q')=='video game')]
