"""Project screen cast with TMDB priority without inventing person identities."""
from collections import defaultdict
import unicodedata
from sqlalchemy import select, func, or_, and_
from models.catalogue import MetadataSnapshot


def label(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


async def complete_cast_works(db, work_ids):
    # Overall credits_complete also requires companies and creators. Cast
    # priority needs only the actual cast/crew response; inspect its shape in
    # SQL so historical snapshots need no rewrite or full payload hydration.
    payload = MetadataSnapshot.payload
    movie = and_(MetadataSnapshot.kind == 'movie',
                 func.jsonb_typeof(payload['credits']['cast']) == 'array',
                 func.jsonb_typeof(payload['credits']['crew']) == 'array')
    series = and_(MetadataSnapshot.kind == 'series',
                  func.jsonb_typeof(payload['aggregate_credits']['cast']) == 'array',
                  func.jsonb_typeof(payload['aggregate_credits']['crew']) == 'array')
    return set(await db.scalars(select(MetadataSnapshot.entity_id).where(
        MetadataSnapshot.entity_id.in_(work_ids), MetadataSnapshot.provider == 'tmdb',
        MetadataSnapshot.kind.in_(('movie', 'series')),
        or_(MetadataSnapshot.coverage['credits_complete'].as_boolean().is_(True), movie, series))))


def preferred_cast(credits, complete_work_ids=()):
    """Retain fallback cast, but prefer a known complete TMDB cast response.

    A unique name AND character match can associate role artwork within one
    verified title. It is not evidence for a global person identity bridge.
    Ambiguous matches and empty character labels never associate artwork.
    Already verified aliases retain all their roles and artwork.
    """
    primary = defaultdict(set)
    candidates = defaultdict(dict)
    for credit, person in credits:
        if credit.role == 'actor' and credit.provider == 'tmdb':
            primary[credit.work_id].add(person.id)
            if label(person.name) and label(credit.character_label):
                candidates[(credit.work_id, label(person.name), label(credit.character_label))][person.id] = person
    secondary = defaultdict(set)
    for credit, person in credits:
        if credit.role == 'actor' and credit.provider == 'tvdb':
            secondary[(credit.work_id, label(person.name), label(credit.character_label))].add(person.id)
    result = []
    for credit, person in credits:
        if credit.role != 'actor' or not primary[credit.work_id] or credit.provider != 'tvdb' or person.id in primary[credit.work_id]:
            result.append((credit, person))
            continue
        key = (credit.work_id, label(person.name), label(credit.character_label))
        matches = candidates.get(key, {})
        if credit.provider == 'tvdb' and len(matches) == 1 and len(secondary[key]) == 1:
            result.append((credit, next(iter(matches.values()))))
        elif credit.work_id not in complete_work_ids:
            result.append((credit, person))
    return result
