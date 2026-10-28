"""Versioned game-title evidence; discovery spelling never merges catalogue roots."""
import re
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from core.game_types import igdb_game_class
from core.game_release_dates import game_release, release

VERSION = 8
# Existing positives retain their criteria; annual sports scopes require v8.
SUPPORTED_VERSIONS = (6, 7, VERSION)


def same_game_port(other,winner):
    """An explicit port parent shares the displayed game, not another namesake."""
    return (other.get('game_class')=='port' and not other.get('cancelled') and other.get('parent')==winner.get('id')
        and winner.get('game_class') in ('game','standalone_expansion','remake','remaster','expanded_game')
        and (spelling(other.get('name'))==spelling(winner.get('name'))
            or bool(title_formats(other.get('name')) & title_formats(winner.get('name')))))


def scoped_title(raw,title):
    """A decorated child title needs its explicit parent, not a loose suffix."""
    kind=igdb_game_class(raw)
    if kind not in ('dlc','expansion','standalone_expansion','episode'):return False
    parent=raw.get('parent_game')
    if not isinstance(parent,dict) or type(parent.get('id')) is not int or parent['id']<=0:return False
    if igdb_game_class(parent) not in ('game','standalone_expansion','remake','remaster','expanded_game'):return False
    if kind=='episode':
        # The source may spell out the series subtitle before the numbered
        # episode. Require the actual provider parent and the same episode,
        # rather than discarding arbitrary subtitles or numbers.
        pattern=r'^(.*?)(?::\s*|\s+[-–—]\s+)(episode\s+(?:[0-9]+|[a-z]+))\s*$'
        source=re.fullmatch(pattern,title.get('title',''),re.IGNORECASE)
        candidate=re.fullmatch(pattern,raw.get('name',''),re.IGNORECASE)
        return bool(source and candidate and title_formats(source[1]) & title_formats(parent.get('name'))
            and title_formats(candidate[1]) & title_formats(parent.get('name','').split(':',1)[0])
            and title_formats(source[2]) & title_formats(candidate[2]))
    source=title.get('title','').split(':',1);candidate=raw.get('name','').split(':',1)
    if len(source)!=2:return False
    if spelling(source[0])!=spelling(parent.get('name')):return False
    if len(candidate)==2 and spelling(candidate[0])!=spelling(parent.get('name')):return False
    tail=re.fullmatch(r"\s*([^:]+?)\s+in\s+(.+)",source[1],re.IGNORECASE)
    if not tail:return False
    decoration=spelling(tail[1].replace('&',' and '));suffix=spelling(tail[2])
    if not re.fullmatch(r'[^\W\d_]+(?: [^\W\d_]+){0,2} and [^\W\d_]+(?: [^\W\d_]+){0,2}',decoration,re.UNICODE):return False
    if re.search(r'\b(remake|remastered|remaster|edition|deluxe|collection|complete|part|episode|chapter|season)\b',decoration):return False
    return len(suffix.split())>=3 and len(suffix)>=12 and suffix==spelling(candidate[-1])


def cancelled_scope(other,winner,title):
    """An exact past launch identifies a released work against a scoped prototype."""
    scope=(winner.get('corroboration') or {}).get('release_scope') or {}
    if not store_agrees(winner.get('corroboration') or {},title):return False
    stamp=scope.get('imdb_date')
    try:released=date.fromisoformat(stamp)
    except (TypeError,ValueError):return False
    return (stamp==scope.get('store_date')==scope.get('igdb_date') and released<=datetime.now(timezone.utc).date()
        and released.year==title.get('year') and winner.get('game_class') in ('game','remake','remaster','expanded_game','standalone_expansion')
        and other.get('game_class')=='port' and other.get('cancelled') is True
        and type(other.get('parent')) is int and other['parent']>0 and other['parent']!=winner.get('id')
        and bool(other.get('platforms')) and bool(winner.get('platforms'))
        and not set(other['platforms']).intersection(winner['platforms']))


def store_agrees(proof,title):
    people={person.get('id') for person in proof.get('people',[]) if isinstance(person,dict) and re.fullmatch(r'nm[0-9]+',str(person.get('id','')))}
    company=proof.get('developer_agreement') is True or proof.get('publisher_agreement') is True and len(people)>=2
    if not company or not re.fullmatch(r'[1-9][0-9]*',str(proof.get('app',''))):return False
    return spelling(proof.get('store_title')) in {spelling(title.get('title')),*[spelling(name) for name in title.get('aliases',[])]}


def supported(row,title):
    proof=row.get('corroboration') or {}
    from core.game_wikidata_title_support import agrees as wiki_agrees
    if wiki_agrees(row,title):return True
    identity=proof.get('release_identity') or {}
    try:
        point=date.fromisoformat(identity['date'])
        detailed=(identity.get('id')==title.get('id') and identity.get('year')==title.get('year')
            and type(title.get('year')) is int and point.year!=title['year'] and point.year in row.get('years',[])
            and spelling(identity.get('title')) in {spelling(title.get('title')),*[spelling(name) for name in title.get('aliases',[])]})
    except (KeyError,TypeError,ValueError):detailed=False
    if detailed:return True
    if not store_agrees(proof,title):return False
    people={person.get('id') for person in proof.get('people',[]) if isinstance(person,dict) and re.fullmatch(r'nm[0-9]+',str(person.get('id','')))}
    early=proof.get('early_access') or {}
    full=proof.get('full_release') or {}
    try:
        launch=date.fromisoformat(full['date']);initial=date.fromisoformat(full['initial_date'])
        full_agrees=(initial<launch<=datetime.now(timezone.utc).date() and launch.year==title.get('year')
            and initial.year in row.get('years',[]) and launch.year>initial.year
            and type(full.get('announcement')) is str and bool(full['announcement']))
    except (KeyError,TypeError,ValueError):full_agrees=False
    return bool(full_agrees or proof.get('characters') or len(people)>=2 or proof.get('cast_character') or early.get('year')==title.get('year')
        and type(early.get('year')) is int and early.get('release_year') in row.get('years',[]) and early['year']<early['release_year'])


def valid_binding_evidence(evidence, imdb_id):
    proof=evidence.get('matching') or {}
    version=evidence.get('matcher_version')
    eligible=[row for row in proof.get('candidates',[]) if isinstance(row,dict)
        and (row.get('year_match') is True or supported(row,proof.get('imdb') or {}))
        and row.get('game_class')!='unknown' and row.get('rank') in (1,2,3) and not row.get('edition')]
    eligible=[row for row in eligible if not row.get('annual_title_scope') or version in SUPPORTED_VERSIONS and version>=8
        and isinstance(row['annual_title_scope'],dict) and row['annual_title_scope'].get('version')==1]
    if version==6:
        eligible=[row for row in eligible if row.get('year_match') is True
            or not (row.get('corroboration') or {}).get('publisher_agreement')
            or (row.get('corroboration') or {}).get('developer_agreement') is True]
    eligible=[row for row in eligible if not any(other['id']!=row['id'] and (same_game_port(row,other) or cancelled_scope(row,other,proof.get('imdb') or {})) for other in eligible)]
    winners=[row for row in eligible if row['rank']==max((entry['rank'] for entry in eligible),default=0)]
    winner=winners[0] if len(winners)==1 else {}
    renamed={row.get('id') for row in proof.get('rename_scope',[]) if row.get('shared_products')}
    year=(proof.get('imdb') or {}).get('year')
    competitors=[row for row in proof.get('candidates',[]) if row.get('id')!=winner.get('id') and not row.get('edition')
        and (not winner.get('year_match') or not row.get('years') or year in row['years'])]
    return (version in SUPPORTED_VERSIONS and proof.get('version')==version
        and proof.get('retrieval_complete') is True and (proof.get('imdb') or {}).get('id')==imdb_id
        and len(winners)==1 and str(winner.get('id'))==evidence.get('igdb_id')
        and all(same_game_port(row,winner) or cancelled_scope(row,winner,proof.get('imdb') or {}) or row.get('years') and row.get('game_class')!='unknown' and row.get('id') in renamed for row in competitors))


def spelling(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[^\W_]+|\+', re.sub("['’]", '', value), re.UNICODE))


_STEP_NUMBERS={name:str(number) for names in (
    'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty',
    'i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx')
    for number,name in enumerate(names.split(),1)}


def written_words(value):
    value=re.sub(r'(?<=[a-z])(?=[A-Z])',' ',value)
    return re.sub(r'(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])',' ',value)


def title_formats(value):
    """Written variants only; retain word boundaries and edition/part meaning."""
    if not isinstance(value,str):return set()
    forms=set()
    for text in {value,written_words(value)}:
        text=re.sub(r'(^|:\s*|[-–—]\s+)the\s+',r'\1',text,flags=re.IGNORECASE)
        text=text.replace('&',' and ')
        for variant in {text,re.sub(r'(?<=\w)[-–](?=\w)','',text)}:
            key=spelling(variant)
            key=re.sub(r'\b(chapter|part|episode|volume|vol|season|book|act)\s+([a-z]+)\b',
                lambda match:match[1]+' '+_STEP_NUMBERS.get(match[2],match[2]),key)
            if key:forms.add(key)
    return forms


def products(raw):
    result=set()
    for row in raw.get('external_games') or []:
        source=row.get('external_game_source') or {}
        if source.get('name')=='Steam' and isinstance(row.get('uid'),str) and re.fullmatch(r'[1-9][0-9]*',row['uid']):
            result.add(('steam.app',row['uid']))
    for row in raw.get('websites') or []:
        try:url=urlsplit(row.get('url') or '')
        except ValueError:continue
        match=re.match(r'^/app/([1-9][0-9]*)(?:/|$)',url.path)
        if url.scheme in ('https','http') and url.hostname=='store.steampowered.com' and not url.username and match:
            result.add(('steam.app',match[1]))
    return result


def same_renamed_product(one,two):
    aliases=lambda raw:{spelling(row.get('name')) for row in raw.get('alternative_names') or [] if isinstance(row,dict)}
    return (igdb_game_class(one)=='game' and igdb_game_class(two)=='game'
        and not one.get('version_parent') and not two.get('version_parent')
        and not one.get('parent_game') and not two.get('parent_game')
        and spelling(one.get('name')) in aliases(two) and spelling(two.get('name')) in aliases(one)
        and bool(products(one)&products(two)))


def years(raw):
    result = set()
    points = [game_release(raw), *[release(row) for row in raw.get('release_dates') or [] if isinstance(row, dict)]]
    for point in points:
        if value:=point.get('release_date'):
            result.add(int(value[:4]))
    return result


def annual_title(raw,title):
    """An omitted annual sports label needs the exact year, not year tolerance."""
    year=title.get('year');released=years(raw)
    if (type(year) is not int or not released or min(released)!=year or igdb_game_class(raw)!='game'
            or raw.get('parent_game') or raw.get('version_parent')
            or not any(row.get('name')=='Sport' for row in raw.get('genres') or [])):return None
    match=re.fullmatch(r'(.+?)\s+(?:\(([0-9]{4})\)|([0-9]{4}))',raw.get('name',''))
    if not match or int(match[2] or match[3])!=year:return None
    names=[title.get('title'),*title.get('aliases',[])]
    if not any(isinstance(name,str) and not re.search(r'[0-9]{4}',name)
            and title_formats(name)&title_formats(match[1]) for name in names):return None
    return {'version':1,'year':year,'base':match[1],'genre':'Sport'}


def decide(title, candidates, *, complete, branches, corroboration=None):
    """An incomplete provider result can rank candidates, but cannot accept one."""
    names = {spelling(title.get('title')): 'primary'}
    names.update({spelling(name): 'alias' for name in title.get('aliases', []) if isinstance(name, str)})
    names.pop('', None)
    source_formats=set().union(*(title_formats(name) for name in [title.get('title'),*title.get('aliases',[])]))
    year = title.get('year')
    ranked = []
    from core.game_wikidata_title_support import agrees as wiki_agrees
    for raw in candidates:
        primary = spelling(raw.get('name'))
        aliases = {spelling(row.get('name')) for row in raw.get('alternative_names') or [] if isinstance(row, dict)}
        scoped=scoped_title(raw,title)
        wiki_match=wiki_agrees({'id':raw['id'],'corroboration':(corroboration or {}).get(raw['id'],{})},title)
        matched = primary in names or bool(aliases & names.keys()) or scoped or wiki_match
        annual=annual_title(raw,title) if not matched else None
        matched=matched or bool(annual)
        format_keys=set() if matched else source_formats.intersection(set().union(*(title_formats(name) for name in
            [raw.get('name'),*[row.get('name') for row in raw.get('alternative_names') or [] if isinstance(row,dict)]])))
        matched=matched or bool(format_keys)
        if not matched:
            continue
        score = 3 if primary == spelling(title.get('title')) else 2 if primary in names else 1
        compatible = type(year) is int and year in years(raw)
        ranked.append({'id': raw['id'], 'name': raw['name'], 'rank': score,
                       'year_match': compatible, 'years':sorted(years(raw)), 'game_class': igdb_game_class(raw),
                       'edition': bool(raw.get('version_parent')),
                       'parent':(raw.get('parent_game') or {}).get('id') if isinstance(raw.get('parent_game'),dict) else raw.get('parent_game'),
                       'platforms':sorted({row['id'] for row in raw.get('platforms') or [] if type(row.get('id')) is int and row['id']>0}),
                       'cancelled':bool(raw.get('release_dates')) and all(isinstance(row.get('status'),dict)
                           and row['status'].get('name','').casefold() in ('cancelled','canceled') for row in raw.get('release_dates') or []),
                       **({'title_format':{'version':1,'key':min(format_keys)}} if format_keys else {}),
                       **({'annual_title_scope':annual} if annual else {}),
                       **({'parent_title_scope':{'parent':raw['parent_game']['id'],'title':raw['parent_game']['name']}} if scoped else {}),
                       **({'corroboration':corroboration[raw['id']]} if corroboration and raw['id'] in corroboration else {})})
    ranked.sort(key=lambda row: (-row['rank'], -row['year_match'], row['id']))
    evidence = {'version': VERSION, 'retrieval_complete': complete, 'branches': branches,
                'imdb': title, 'candidates': ranked}
    if not complete:
        return {'status': 'unresolved', 'reason': 'incomplete_candidates', 'evidence': evidence}
    eligible = [row for row in ranked if (row['year_match'] or supported(row,title)) and not row['edition'] and row['game_class'] != 'unknown']
    eligible=[row for row in eligible if not any(other['id']!=row['id'] and (same_game_port(row,other) or cancelled_scope(row,other,title)) for other in eligible)]
    if not eligible:
        return {'status': 'unmapped' if not ranked else 'unresolved', 'reason': 'title_year_evidence', 'evidence': evidence}
    best = eligible[0]
    if sum(row['rank'] == best['rank'] for row in eligible) != 1:
        return {'status': 'unresolved', 'reason': 'ambiguous_candidates', 'evidence': evidence}
    raw_by_id={row['id']:row for row in candidates}
    rename=[]
    for other in ranked:
        if other['id']==best['id'] or other['edition']:continue
        if same_game_port(other,best):continue
        if cancelled_scope(other,best,title):continue
        if best['year_match'] and other['years'] and year not in other['years'] and not supported(other,title):continue
        if not other['years'] or other['game_class']=='unknown':
            return {'status':'unresolved','reason':'unknown_competitor','evidence':evidence}
        if not same_renamed_product(raw_by_id[best['id']],raw_by_id[other['id']]):
            return {'status':'unresolved','reason':'unresolved_alias_scope','evidence':evidence}
        rename.append({'id':other['id'],'shared_products':sorted(products(raw_by_id[best['id']])&products(raw_by_id[other['id']]))})
    if rename:evidence['rename_scope']=rename
    return {'status': 'verified', 'igdb_id': best['id'], 'evidence': evidence}
