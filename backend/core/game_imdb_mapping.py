"""Explicit Wikidata game mappings. Titles and years never establish identity."""
import re

from core.wikidata_claims import best_claims, qualified, snak_value

TITLE_ID = re.compile(r'tt[0-9]+\Z')
NUMERIC_ID = re.compile(r'[1-9][0-9]*\Z')


def igdb_evidence(item, igdb_id, slug):
    """Prefer numeric identity, accepting an exact explicit slug if IDs are absent.

    One item referring to different IGDB games is ambiguous even if a slug
    happens to match. This prevents a shared work/edition item collapsing games.
    """
    rows = best_claims(item, 'P5794')
    standalone = best_claims(item, 'P9043')
    identifiers = [snak_value(row.get('mainsnak')) for row in standalone]
    identifiers += [value for row in rows for value in qualified(row, 'P9043')]
    identifiers = [value for value in identifiers if value is not None]
    raw_slugs = [value for row in rows if (value := snak_value(row.get('mainsnak'))) is not None]
    evidence = {'wikidata_id': item.get('id'), 'revision': item.get('lastrevid'),
                'P5794': rows, 'P9043': standalone}
    if any(not isinstance(value, str) or not NUMERIC_ID.fullmatch(value) for value in identifiers) or any(not isinstance(value,str) for value in raw_slugs):
        return 'conflict', evidence
    slugs = set(raw_slugs)
    identifiers = set(identifiers)
    if identifiers:
        return ('verified' if identifiers == {str(igdb_id)} else 'conflict'), evidence
    if not slugs:
        return 'unmapped', evidence
    if len(slugs) != 1:
        return 'conflict', evidence
    return ('verified' if slugs == {slug} else 'conflict'), evidence


def game_mapping(item, igdb_id, slug):
    status, evidence = igdb_evidence(item, igdb_id, slug)
    rows = best_claims(item, 'P345')
    evidence = {**evidence, 'P345': rows}
    values = [value for row in rows if (value := snak_value(row.get('mainsnak'))) is not None]
    if status == 'verified':
        if not values:
            status = 'unmapped'
        elif any(not isinstance(value, str) or not TITLE_ID.fullmatch(value) for value in values) or len(set(values)) != 1:
            status = 'conflict'
    return {'status': status, 'imdb_id': values[0] if status == 'verified' else None,
            'evidence': evidence}
