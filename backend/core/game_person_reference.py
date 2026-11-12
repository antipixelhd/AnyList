"""Narrow Wikidata reference candidates require independent corroboration.

References cite statements; they are not general subject identifiers. Only a
TMDB-sourced full birth-date reference is considered here. Title text merely
prioritises a bounded proof fetch: exact title IDs and specific performances
establish career overlap. No name-only merge or generic reference promotion.
"""
import asyncio
import re
import unicodedata
from datetime import date, timedelta

from core import tmdb
from core.catalogue_providers import ProviderError
from core.game_imdb_discovery_source import GameIMDbDiscoverySource
from core.game_imdb_source import IMDbGameSource, PERSON_CREDITS, TITLE_ID, page, identifier
from core.wikidata_claims import claims, snak_value, values

VERSION = 1
NUMERIC = re.compile(r'[1-9][0-9]{0,9}\Z')
CAST = {'actor', 'actress', 'cast', 'voice_actor'}
GENERIC = {'self', 'himself', 'herself', 'themselves', 'additional voices', 'various', 'various characters', 'voice', 'voices'}
EVIDENCE_QUERY = PERSON_CREDITS.replace('category { id }',
    'category { id } ... on Cast { characters { name } attributes { text } }')


def normal(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split()) if isinstance(text, str) else ''


def specific(value):
    return len(value) >= 4 and value not in GENERIC and not any(
        value.startswith(prefix + ' ') for prefix in GENERIC)


def birthday(value):
    if not isinstance(value, dict) or value.get('precision') != 11 or value.get('calendarmodel') != 'http://www.wikidata.org/entity/Q1985727':
        return None
    raw = value.get('time')
    try:
        return date.fromisoformat(raw[1:11]).isoformat() if isinstance(raw, str) and re.fullmatch(r'\+[0-9]{4}-[0-9]{2}-[0-9]{2}T00:00:00Z', raw) else None
    except ValueError:
        return None


def candidate(item, native):
    proof = {'wikidata_id': item.get('id'), 'revision': item.get('lastrevid'), 'P345': claims(item, 'P345')}
    if 'Q5' not in values(item, 'P31') or set(values(item, 'P345')) != {native}:
        return 'conflict', None, proof
    direct = values(item, 'P4985')
    references = []
    for statement in claims(item, 'P569'):
        day = birthday(snak_value(statement.get('mainsnak')))
        if not day:
            continue
        for reference in statement.get('references') or []:
            snaks = reference.get('snaks') or {}
            if {snak_value(snak) for snak in snaks.get('P248', [])} != {'Q20828898'}:
                continue
            ids = [snak_value(snak) for snak in snaks.get('P4985', [])]
            if ids:
                references.append({'statement_id': statement.get('id'), 'property': 'P569',
                    'rank': statement.get('rank'), 'birthday': day, 'reference': reference, 'ids': ids})
    proof.update(P4985=claims(item, 'P4985'), references=references)
    all_ids = direct + [value for row in references for value in row['ids']]
    if not all_ids:
        return 'unresolved', None, proof
    if any(not isinstance(value, str) or not NUMERIC.fullmatch(value) or int(value) > 2147483647 for value in all_ids) or len(set(all_ids)) != 1:
        return 'conflict', None, proof
    proof['method'] = 'direct_identifiers' if direct else 'birth_reference'
    return 'candidate', int(all_ids[0]), proof


def assess(native, item, profile, person, shared):
    state, target, proof = candidate(item, native)
    if state != 'candidate':
        return state, proof
    if type(profile.get('id')) is not int or profile.get('id') != target or profile.get('imdb_id') not in (None, '', native):
        return 'conflict', proof
    source_name = normal((person.get('nameText') or {}).get('text'))
    target_names = {normal(profile.get('name')), *(normal(value) for value in profile.get('also_known_as') or [])} - {''}
    wiki_names = {normal(row.get('value')) for row in (item.get('labels') or {}).values() if isinstance(row, dict)}
    wiki_names |= {normal(row.get('value')) for rows in (item.get('aliases') or {}).values() for row in rows if isinstance(row, dict)}
    if not source_name or source_name not in target_names or source_name not in wiki_names:
        return 'conflict', proof
    if proof['method'] == 'direct_identifiers':
        return 'verified', proof
    raw = (person.get('birthDate') or {}).get('dateComponents') or {}
    source_birth = None
    try:
        if all(type(raw.get(key)) is int for key in ('year', 'month', 'day')):
            source_birth = date(raw['year'], raw['month'], raw['day']).isoformat()
    except ValueError:
        pass
    target_birth = profile.get('birthday')
    try:
        target_birth = date.fromisoformat(target_birth).isoformat()
    except (ValueError, TypeError):
        target_birth = None
    proof['birthdays'] = {'imdb': source_birth, 'tmdb': target_birth,
        'wikidata_references': sorted({row['birthday'] for row in proof['references']})}
    if source_birth and target_birth and source_birth != target_birth:
        return 'conflict', proof
    reference_dates = {row['birthday'] for row in proof['references']}
    proof['birthdate_discrepancy'] = bool(target_birth and reference_dates != {target_birth})
    if target_birth and reference_dates == {target_birth}:
        proof['corroboration'] = 'full_birthdate'
        return 'verified', proof
    exact = {row['imdb_id'] for row in shared}
    characters = {row['character'] for row in shared if row.get('character')}
    proof['shared_works'] = shared
    if len(exact) >= 3 and len(characters) >= 2:
        proof['corroboration'] = 'exact_work_ids_and_specific_characters'
        return 'verified', proof
    return 'unresolved', proof


class GamePersonReference:
    def __init__(self, sessions, *, discovery=None, source=None):
        self.discovery = discovery or GameIMDbDiscoverySource(sessions)
        self.sources = self.discovery.sources
        self.source = source or IMDbGameSource(self.sources.api.http)

    @staticmethod
    def usable(cached):
        if cached.get('error') or cached.get('stale'):
            raise ProviderError('identity_unavailable')
        return cached['data']

    async def performance(self, native):
        async def load():
            rows, cursor, seen, total = [], None, set(), None
            for _ in range(12):
                body = await self.source.query(EVIDENCE_QUERY, {'id': native, 'after': cursor, 'profile': cursor is None})
                raw = body.get('name')
                if not isinstance(raw, dict) or raw.get('id') != native:
                    raise ProviderError('identity_conflict')
                if any(value.startswith(('Name.credits', 'Name.nameText')) for value in body.get('_denied_fields') or []):
                    raise ProviderError('identity_unavailable')
                current = page(raw.get('credits'), 50)
                if total is not None and total != current['total']:
                    raise ProviderError('changed_connection')
                total = current['total']
                rows.extend(current['rows'])
                if cursor is None:
                    person = {key: raw.get(key) for key in ('id', 'nameText', 'birthDate')}
                    if any(value.startswith('Name.birthDate') for value in body.get('_denied_fields') or []):
                        person.pop('birthDate', None)
                if not current['has_next']:
                    if len(rows) != total:
                        raise ProviderError('incomplete_cache')
                    return {'person': person, 'rows': rows}
                cursor = current['cursor']
                if cursor in seen:
                    raise ProviderError('changed_connection')
                seen.add(cursor)
            raise ProviderError('coverage_limited')
        cached = await self.sources.cache.get('imdb', 'game_person_proof', native, load,
            ttl=timedelta(days=30), max_stale=timedelta(days=30))
        return {**self.usable(cached), 'fetched_at': cached.get('fetched_at')}

    async def shared(self, profile, rows, key):
        index = {}
        for row in rows:
            title = row.get('title') or {}
            if (row.get('category') or {}).get('id') not in CAST or not isinstance(title.get('id'), str) or not TITLE_ID.fullmatch(title['id']):
                continue
            for character in row.get('characters') or []:
                value = normal(character.get('name'))
                if specific(value):
                    index.setdefault(title.get('id'), set()).add(value)
        # Candidate title names only choose the proof budget. They never count
        # as matching works: each accepted row must confirm the exact IMDb ID.
        names = {normal((row.get('title') or {}).get('titleText', {}).get('text')) for row in rows}
        candidates = {}
        for row in (profile.get('combined_credits') or {}).get('cast') or []:
            kind, native = row.get('media_type'), row.get('id')
            if kind in ('movie', 'tv') and type(native) is int and native > 0 and normal(row.get('title') or row.get('name')) in names:
                candidates[(kind, native)] = row
        slots = asyncio.Semaphore(3)
        async def proof(kind, native, row):
            async with slots:
                raw = await tmdb.get_external_ids(native, 'movie' if kind == 'movie' else 'series', api_key=key)
            if not isinstance(raw, dict) or type(raw.get('id')) is not int or raw.get('id') != native:
                raise ProviderError('bad_response')
            title = raw.get('imdb_id')
            character = normal(row.get('character'))
            if title in index and character in index[title]:
                return {'imdb_id': title, 'tmdb_id': native, 'media_type': kind, 'character': character}
            return None
        return [row for row in await asyncio.gather(*(proof(kind, native, row)
            for (kind, native), row in sorted(candidates.items())[:12])) if row]

    async def resolve(self, native, key):
        identifier(native, person=True)
        result = self.usable(await self.discovery.items([native]))
        qids = result['items'][native]
        if len(qids) != 1:
            return ('ambiguous' if qids else 'unresolved'), None, {'method': 'wikidata_reference', 'candidates': qids}
        cached = await self.discovery.wiki.entities(qids, person_references=True)
        fetched = self.usable(cached)
        item = fetched['entities'].get(qids[0])
        if not item:
            raise ProviderError('incomplete_cache')
        state, target, proof = candidate(item, native)
        if state != 'candidate':
            return state, None, proof
        profile = await tmdb.get_person(target, api_key=key)
        if (not isinstance(profile, dict) or type(profile.get('id')) is not int or profile.get('id') != target
                or not isinstance(profile.get('name'), str) or not profile['name'].strip()):
            raise ProviderError('bad_response')
        performance = await self.performance(native)
        state, proof = assess(native, item, profile, performance['person'], [])
        if state == 'unresolved':
            shared = await self.shared(profile, performance['rows'], key)
            state, proof = assess(native, item, profile, performance['person'], shared)
        proof = {**proof, 'version': VERSION, 'imdb_id': native, 'tmdb_id': target,
            'wikidata_fetched_at': cached.get('fetched_at'), 'performance_fetched_at': performance.get('fetched_at')}
        return state, profile if state == 'verified' else None, proof
