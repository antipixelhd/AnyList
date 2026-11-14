"""Early positive search evidence; never a substitute for a complete career."""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import aliased, load_only

from core.game_credit_roles import SOURCE_ACTING, source_role
from core.game_credit_parents import person_bindings
from core.game_types import INDEPENDENT, capabilities
from models.catalogue import CatalogueEntity, CatalogueIdentity


async def sample_matches(db, samples, kind, existing=None):
    groups = {'actor': [], 'staff': [], 'studio': []}
    if not samples:
        return groups
    # Only exact, currently verified canonical bindings qualify. Unknown title
    # matches continue through the existing mapping pipeline in the background.
    bindings = person_bindings({(r.get('title') or {}).get('id') for value in samples.values() for r in value['rows']})
    work = aliased(CatalogueEntity)
    verified = {native for native, attributes in await db.execute(select(bindings.c.imdb_id, work.attributes).join(work, work.id == bindings.c.work_id).where(
        bindings.c.credit_kind.is_(None), work.kind == 'game',
        work.attributes['game_class'].astext.in_(INDEPENDENT),
        work.attributes['adult'].astext.is_distinct_from('true'),
        work.attributes['mobile_only'].astext.is_distinct_from('true'))) if capabilities(attributes).searchable}
    people = (await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
            CatalogueIdentity.namespace == 'imdb.person', CatalogueIdentity.external_id.in_(samples))
        .options(load_only(CatalogueEntity.id, CatalogueEntity.kind, CatalogueEntity.name, CatalogueEntity.image_url,
                           CatalogueEntity.canonical_person_id, CatalogueEntity.canonical_company_id)))).all()
    from core.person_identities import canonical_people
    roots = await canonical_people(db, [p for _, p in people], lightweight=True)
    from core.contributor_search import result
    for native, person in people:
        person = roots[person.id]
        roles = defaultdict(set)
        for row in samples[native]['rows']:
            role = source_role(row)
            if (row.get('title') or {}).get('id') not in verified or not role or role == 'thanks':
                continue
            section = 'actor' if role in SOURCE_ACTING else 'staff'
            if kind in ('all', 'people', section):
                roles[section].add('Actor' if role in ('actor', 'actress', 'cast') else role.replace('_', ' ').title())
        for section, labels in roles.items():
            item = result(person, section, labels)
            if not any(row['key'] == item['key'] for row in (existing or {}).get(section, [])):
                groups[section].append({**item, 'url':item['url'] + '?media_type=game', '_works':1})
    return groups
