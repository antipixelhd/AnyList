"""Game work capabilities, independent of provider display labels and viewer state."""
from dataclasses import dataclass
import re


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
INDEPENDENT = frozenset({'game', 'bundle', 'standalone_expansion', 'remake', 'remaster', 'expanded_game', 'port'})
PLAYABLE_CHILDREN = frozenset({'dlc', 'expansion'})
CREDIT_CHILDREN = frozenset({'dlc', 'expansion', 'update', 'episode'})
# Provider platform identities, including pre-smartphone releases. Dedicated
# handheld consoles (DS, PSP, Switch, etc.) are not mobile-phone platforms.
MOBILE_PLATFORMS = frozenset({'34','39','42','44','55','73','74','405','417'})
MOBILE_NAMES = frozenset({'android','ios','n-gage','tapwave zodiac','legacy mobile device',
    'blackberry os','windows phone','windows mobile','palm os'})


def mobile_platform(platform):
    return isinstance(platform,dict) and (str(platform.get('id')).removeprefix('igdb.platform:') in MOBILE_PLATFORMS
        or str(platform.get('name','')).casefold() in MOBILE_NAMES)


def mobile_only(raw):
    """Only positive platform evidence excludes a title; unknown stays unknown."""
    if (raw or {}).get('mobile_only') is True:return True
    platforms = list((raw or {}).get('platforms') or [])
    # A phone-origin work can also have a real PC/console release. Individual
    # phone ports remain excluded, but non-mobile ports qualify their parent.
    if igdb_game_class(raw or {}) != 'port':
        for port in (raw or {}).get('ports') or []:
            if isinstance(port,dict) and igdb_game_class(port)=='port':
                platforms.extend(port.get('platforms') or [])
    return bool(platforms) and all(mobile_platform(platform) for platform in platforms)
# IGDB's general DLC type also contains item/cosmetic products. Keep source
# identity/type intact; conservatively omit those products from playable lists.
# Explicit Expansions take precedence over ambiguous product-name exclusions.
NONPLAYABLE_DLC_NAME = r'\m(pack|soundtrack|artbook|costume|cosmetic|skin)\M|\mart[ -]?book\M|\mseason[ -]?pass\M'
_NONPLAYABLE_DLC = re.compile(r'\b(pack|soundtrack|artbook|costume|cosmetic|skin)\b|\bart[ -]?book\b|\bseason[ -]?pass\b',re.I)


def playable_child(attributes,name=''):
    if mobile_only(attributes):return False
    kind=(attributes or {}).get('game_class')
    return kind=='expansion' or kind=='dlc' and not _NONPLAYABLE_DLC.search(name)


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
    if (attributes or {}).get('mobile_only') is True or mobile_only(attributes):
        return GameCapabilities(kind,False,False,False,False)
    return GameCapabilities(kind, kind in INDEPENDENT, kind in PLAYABLE_CHILDREN,
                            kind in INDEPENDENT or kind == 'expansion', kind == 'expansion')


def candidate_fields(fields: str) -> str:
    """Project authoritative edition/port parents without per-result fan-out."""
    fields += ',themes,version_parent,parent_game.game_type.type'
    return ','.join(prefix+field for prefix in ('','version_parent.','parent_game.','version_parent.parent_game.')
                    for field in fields.split(','))


def canonical_game(raw: dict) -> dict | None:
    work,seen,ports = raw,set(),[]
    for _ in range(4):
        if not isinstance(work,dict):
            return None
        identifier=work.get('id')
        if isinstance(identifier,bool) or not isinstance(identifier,int) or identifier<=0 or identifier in seen:
            return None
        seen.add(identifier)
        if work.get('version_parent') is not None:
            work=work['version_parent']
        elif igdb_game_class(work)=='port':
            ports.append({key:work[key] for key in ('id','game_type','platforms') if key in work})
            work=work.get('parent_game')
        else:
            if ports:
                work={**work,'ports':[*work.get('ports',[]),*ports]}
            return None if mobile_only(work) else work
    return None


def game_platforms(raw: dict) -> list[dict]:
    """A port contributes availability to its original work, never another list row."""
    rows=list(raw.get('platforms') or [])
    for port in raw.get('ports') or []:
        if isinstance(port,dict) and igdb_game_class(port)=='port':
            rows.extend(port.get('platforms') or [])
    return list({row['id']:row for row in rows if isinstance(row,dict) and not mobile_platform(row) and isinstance(row.get('id'),int)
                 and not isinstance(row['id'],bool) and row['id']>0}.values())


def search_candidate(raw: dict) -> dict | None:
    """Resolve verified edition/port identity before evaluating eligibility."""
    from core.catalogue_normalize import image
    from core.game_release_dates import game_release
    from core.game_content import erotic_game
    work=canonical_game(raw)
    if work is None:
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
    released = game_release(work)
    return dict(external_id=str(identifier), name=work['name'], image_url=image(work.get('cover')),
                **{key:released[key] for key in ('release_date','release_date_precision','release_quarter')},
                game_class=kind,is_erotic=erotic_game(work))


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
