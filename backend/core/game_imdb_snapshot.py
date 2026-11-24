"""Fresh, positive-only game evidence from the IMDb principal-credit dataset."""
from datetime import timedelta

from sqlalchemy import select

from core.catalogue_providers import ProviderError
from models.game_mapping import GameMappingGeneration

DATASET = 'imdb_dataset'
FALLBACK_ERRORS = frozenset({'forbidden', 'unavailable', 'timeout', 'rate_limited',
    'retry_pending', 'graphql_error', 'not_entitled'})


def credit_rows(credits, people):
    seen = set()
    for native, category, job, characters in credits:
        key = (native, category, job, tuple(characters))
        if native not in people or key in seen:
            continue
        seen.add(key)
        yield {
            'name': {'id': native, 'nameText': {'text': people[native]['title']}},
            'category': {'id': category},
            'jobs': [{'text': job}] if job else [],
            'characters': [{'name': character} for character in characters if character.strip()],
        }


def title_header(value):
    return {'id': value['id'], 'titleText': {'text': value['title']},
        'titleType': {'id': 'videoGame'}, 'releaseYear': {'year': value['year']} if value.get('year') else None,
        'releaseDate': None}


async def title_reference(sessions, native, *, include_principals=False):
    from core.game_mapping_index import lookup
    rows = await lookup(sessions, 'imdb', native=native, include_principals=include_principals)
    return rows[0] if rows else None


async def title_with_fallback(sessions, fetch, native, reference=None):
    """Keep provider identity conflicts authoritative and propagate uncovered outages."""
    try:
        return await fetch(native)
    except (ProviderError, TimeoutError) as error:
        if isinstance(error, ProviderError) and error.code not in FALLBACK_ERRORS:
            raise
        reference = reference or await title_reference(sessions, native)
        if not reference:
            raise
        return title_header(reference['value'])


async def principals_available(sessions):
    from core.game_imdb_enrichment import now
    async with sessions() as db:
        return bool(await db.scalar(select(GameMappingGeneration.id).where(
            GameMappingGeneration.source == 'imdb', GameMappingGeneration.active.is_(True),
            GameMappingGeneration.observed_at >= now() - timedelta(days=2),
            GameMappingGeneration.coverage['principal_credits'].as_boolean().is_(True))))


async def people_samples(sessions, term):
    """Bound evidence for a search. An absent principal proves no negative."""
    from core.game_mapping_index import lookup
    people = await lookup(sessions, 'imdb', person=term)
    if not people:
        return {}, await principals_available(sessions)
    ids = {native for row in people for native in row['value']['game_ids'][:4]}
    games = {row['native']: row['value'] for row in await lookup(sessions, 'imdb', natives=ids,
        include_principals=True) if row['generation'] == people[0]['generation']}
    matches = {}
    for row in people:
        native = row['native']
        credits = [
            {**credit, 'title': title_header(games[title])}
            for title in row['value']['game_ids'][:4] if title in games
            for credit in games[title].get('principals', []) if credit['name']['id'] == native
        ]
        if credits:
            matches[native] = {'name': row['value']['title'],
                'sample': {'total': len(credits), 'rows': credits, 'complete': False}}
    return matches, True
