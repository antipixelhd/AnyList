"""Versioned game-title evidence; discovery spelling never merges catalogue roots."""
import re
import unicodedata
from urllib.parse import urlsplit
from core.game_types import igdb_game_class
from core.game_release_dates import game_release, release

VERSION = 2


def supported(row,title):
    proof=row.get('corroboration') or {}
    if proof.get('developer_agreement') is not True or not re.fullmatch(r'[1-9][0-9]*',str(proof.get('app',''))):return False
    if spelling(proof.get('store_title')) not in {spelling(title.get('title')),*[spelling(name) for name in title.get('aliases',[])]}:return False
    people={person.get('id') for person in proof.get('people',[]) if isinstance(person,dict) and re.fullmatch(r'nm[0-9]+',str(person.get('id','')))}
    early=proof.get('early_access') or {}
    return bool(proof.get('characters') or len(people)>=2 or proof.get('cast_character') or early.get('year')==title.get('year')
        and type(early.get('year')) is int and early.get('release_year') in row.get('years',[]) and early['year']<early['release_year'])


def valid_binding_evidence(evidence, imdb_id):
    proof=evidence.get('matching') or {}
    eligible=[row for row in proof.get('candidates',[]) if isinstance(row,dict)
        and (row.get('year_match') is True or supported(row,proof.get('imdb') or {}))
        and row.get('game_class')!='unknown' and row.get('rank') in (1,2,3) and not row.get('edition')]
    winners=[row for row in eligible if row['rank']==max((entry['rank'] for entry in eligible),default=0)]
    winner=winners[0] if len(winners)==1 else {}
    renamed={row.get('id') for row in proof.get('rename_scope',[]) if row.get('shared_products')}
    year=(proof.get('imdb') or {}).get('year')
    competitors=[row for row in proof.get('candidates',[]) if row.get('id')!=winner.get('id') and not row.get('edition')
        and (not winner.get('year_match') or not row.get('years') or year in row['years'])]
    return (evidence.get('matcher_version')==VERSION and proof.get('version')==VERSION
        and proof.get('retrieval_complete') is True and (proof.get('imdb') or {}).get('id')==imdb_id
        and len(winners)==1 and str(winner.get('id'))==evidence.get('igdb_id')
        and all(row.get('years') and row.get('game_class')!='unknown' and row.get('id') in renamed for row in competitors))


def spelling(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return ' '.join(re.findall(r'[^\W_]+|\+', re.sub("['’]", '', value), re.UNICODE))


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


def decide(title, candidates, *, complete, branches, corroboration=None):
    """An incomplete provider result can rank candidates, but cannot accept one."""
    names = {spelling(title.get('title')): 'primary'}
    names.update({spelling(name): 'alias' for name in title.get('aliases', []) if isinstance(name, str)})
    names.pop('', None)
    year = title.get('year')
    ranked = []
    for raw in candidates:
        primary = spelling(raw.get('name'))
        aliases = {spelling(row.get('name')) for row in raw.get('alternative_names') or [] if isinstance(row, dict)}
        matched = primary in names or bool(aliases & names.keys())
        if not matched:
            continue
        score = 3 if primary == spelling(title.get('title')) else 2 if primary in names else 1
        compatible = type(year) is int and year in years(raw)
        ranked.append({'id': raw['id'], 'name': raw['name'], 'rank': score,
                       'year_match': compatible, 'years':sorted(years(raw)), 'game_class': igdb_game_class(raw),
                       'edition': bool(raw.get('version_parent')),
                       **({'corroboration':corroboration[raw['id']]} if corroboration and raw['id'] in corroboration else {})})
    ranked.sort(key=lambda row: (-row['rank'], -row['year_match'], row['id']))
    evidence = {'version': VERSION, 'retrieval_complete': complete, 'branches': branches,
                'imdb': title, 'candidates': ranked}
    if not complete:
        return {'status': 'unresolved', 'reason': 'incomplete_candidates', 'evidence': evidence}
    eligible = [row for row in ranked if (row['year_match'] or supported(row,title)) and not row['edition'] and row['game_class'] != 'unknown']
    if not eligible:
        return {'status': 'unmapped' if not ranked else 'unresolved', 'reason': 'title_year_evidence', 'evidence': evidence}
    best = eligible[0]
    if sum(row['rank'] == best['rank'] for row in eligible) != 1:
        return {'status': 'unresolved', 'reason': 'ambiguous_candidates', 'evidence': evidence}
    raw_by_id={row['id']:row for row in candidates}
    rename=[]
    for other in ranked:
        if other['id']==best['id'] or other['edition']:continue
        if best['year_match'] and other['years'] and year not in other['years'] and not supported(other,title):continue
        if not other['years'] or other['game_class']=='unknown':
            return {'status':'unresolved','reason':'unknown_competitor','evidence':evidence}
        if not same_renamed_product(raw_by_id[best['id']],raw_by_id[other['id']]):
            return {'status':'unresolved','reason':'unresolved_alias_scope','evidence':evidence}
        rename.append({'id':other['id'],'shared_products':sorted(products(raw_by_id[best['id']])&products(raw_by_id[other['id']]))})
    if rename:evidence['rename_scope']=rename
    return {'status': 'verified', 'igdb_id': best['id'], 'evidence': evidence}
