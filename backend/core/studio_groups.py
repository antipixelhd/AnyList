"""Reviewed presentation groups, separate from provider identity and history.

Membership requires a documented common production brand and concurrent activity;
neither a shared prefix nor overlapping work dates establishes a relationship.
Keep predecessor/rebranded studios out of their successor's group.
"""
from sqlalchemy import or_, select
from models.catalogue import CatalogueEntity, CatalogueIdentity

# DC itself identifies DC Comics and Vertigo as concurrent DC Entertainment
# brands, rather than successive names:
# https://www.dc.com/blog/2012/01/19/dc-entertainment-reveals-new-brand-identity
# https://www.dc.com/blog/2015/06/24/dc-entertainment-takes-center-stage-at-comic-con-international-san-diego-2015
# Provider credits overlap during that period. DC Films (128064/10576) and
# DC Studios (184898) remain separate historical studio eras.
GROUPS = {429: {'name': 'DC Entertainment', 'members': (429, 9993, 165407),
    'search_names': ('DC', 'DC Comics', 'DC Entertainment', 'DC Vertigo', 'Vertigo')}}
ROOTS = {member: root for root, group in GROUPS.items() for member in group['members']}


def matching_studio_members(term):
    return tuple(member for group in GROUPS.values() if any(term.casefold() in name.casefold()
        for name in group['search_names']) for member in group['members'])


def studio_root(native):
    return ROOTS.get(native, native)


def studio_members(native):
    root = studio_root(native)
    return GROUPS[root]['members'] if root in GROUPS else (native,)


def studio_companies(native):
    members = studio_members(native)
    return '|'.join(map(str, members)) if len(members) > 1 else native


def studio_display_name(native, fallback):
    return GROUPS.get(studio_root(native), {}).get('name', fallback)


def studio_entity_ids(native, entity_id=None):
    roots = select(CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace == 'tmdb.company',
        CatalogueIdentity.external_id.in_([str(i) for i in studio_members(native) if i]),
    )
    return select(CatalogueEntity.id).where(CatalogueEntity.kind == 'organization', or_(
        CatalogueEntity.id == entity_id if entity_id else False,
        CatalogueEntity.id.in_(roots), CatalogueEntity.canonical_company_id.in_(roots),
        CatalogueEntity.canonical_company_id == entity_id if entity_id else False,
    )).correlate(None)
