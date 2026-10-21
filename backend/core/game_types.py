"""Game work capabilities, independent of provider display labels and viewer state."""
from dataclasses import dataclass


# IGDB game_types identities, not the deprecated category field. Keep the source
# name too; an unknown future type must not gain tracking/search capabilities.
IGDB_TYPES = {
    0: 'game', 1: 'dlc', 2: 'expansion', 3: 'bundle',
    4: 'standalone_expansion', 5: 'mod', 6: 'episode', 7: 'season',
    8: 'remake', 9: 'remaster', 10: 'expanded_game', 11: 'port',
    12: 'fork', 13: 'pack', 14: 'update',
}
TYPE_NAMES = {
    'main game': 'game', 'dlc': 'dlc', 'dlc add-on': 'dlc',
    'expansion': 'expansion', 'standalone expansion': 'standalone_expansion',
    'remake': 'remake', 'remaster': 'remaster', 'port': 'port',
    'expanded game': 'expanded_game', 'pack / addon': 'pack',
    'pack / add-on': 'pack', 'update': 'update', 'bundle': 'bundle',
    'mod': 'mod', 'episode': 'episode', 'season': 'season', 'fork': 'fork',
}
INDEPENDENT = frozenset({'game', 'standalone_expansion', 'remake', 'remaster', 'expanded_game', 'port'})
PLAYABLE_CHILDREN = frozenset({'dlc', 'expansion'})


def igdb_game_class(raw: dict) -> str:
    value = raw.get('game_type')
    if isinstance(value, dict):
        identifier = value.get('id')
        if isinstance(identifier, int) and not isinstance(identifier, bool) and identifier in IGDB_TYPES:
            return IGDB_TYPES[identifier]
        return TYPE_NAMES.get(str(value.get('type') or '').strip().lower(), 'unknown')
    if isinstance(value, int) and not isinstance(value, bool):
        return IGDB_TYPES.get(value, 'unknown')
    return 'unknown'


@dataclass(frozen=True)
class GameCapabilities:
    game_class: str
    independent: bool
    playable_child: bool
    searchable: bool
    expansion_page: bool


def capabilities(attributes: dict | None) -> GameCapabilities:
    kind = (attributes or {}).get('game_class', 'unknown')
    return GameCapabilities(kind, kind in INDEPENDENT, kind in PLAYABLE_CHILDREN,
                            kind in INDEPENDENT or kind == 'expansion', kind == 'expansion')


def search_candidate(raw: dict) -> dict | None:
    """Resolve an edition before eligibility; never promote an unknown add-on."""
    from core.catalogue_normalize import date, image
    parent = raw.get('version_parent')
    work = raw if parent is None else parent
    if not isinstance(work, dict) or work.get('version_parent') is not None:
        return None
    identifier = work.get('id')
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0 or not work.get('name'):
        return None
    kind = igdb_game_class(work)
    caps = capabilities({'game_class': kind})
    if not caps.searchable:
        return None
    if caps.expansion_page:
        base = work.get('parent_game')
        base_id = base.get('id') if isinstance(base, dict) else None
        if isinstance(base_id, bool) or not isinstance(base_id, int) or base_id <= 0 or igdb_game_class(base) not in INDEPENDENT:
            return None
    return dict(external_id=str(identifier), name=work['name'], image_url=image(work.get('cover')),
                release_date=date(work.get('first_release_date')), game_class=kind)


def platform_group(platform: dict) -> str:
    """Exact provider identities remain internal; PC is one UI category."""
    identifier = str(platform.get('id') or '')
    family = platform.get('platform_family') or {}
    family_name = str(family.get('name') or '').lower()
    name = str(platform.get('name') or '')
    if identifier in {'3', '6', '14'} or name.lower() in {'linux', 'mac', 'pc (microsoft windows)', 'windows'}:
        return 'PC'
    for label in ('PlayStation', 'Xbox', 'Nintendo', 'Sega', 'Atari'):
        if family_name == label.lower():
            return label
    return name
