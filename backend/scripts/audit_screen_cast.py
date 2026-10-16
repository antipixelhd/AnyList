"""Read-only cast health check; same-name groups are candidates, not identities."""
import asyncio
import json
from collections import defaultdict
from sqlalchemy import select
from core.person_identities import canonical_people
from core.screen_cast import complete_cast_works, label, preferred_cast
from db import AsyncSessionLocal
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity


async def audit(db):
    credits = list((await db.execute(select(CatalogueCredit, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueCredit.contributor_id).where(
        CatalogueCredit.role == 'actor', CatalogueEntity.kind == 'person'))).all())
    canonical = await canonical_people(db, [p for _, p in credits])
    credits = [(c, canonical[p.id]) for c, p in credits]
    projected = preferred_cast(credits, await complete_cast_works(db, {c.work_id for c, _ in credits}))

    def duplicates(rows):
        groups = defaultdict(set)
        for credit, person in rows:
            groups[(credit.work_id, label(person.name))].add(person.id)
        return {key for key, ids in groups.items() if len(ids) > 1}

    before, after = duplicates(credits), duplicates(projected)
    original = {c.id: p.id for c, p in credits}
    identities = list((await db.execute(select(CatalogueIdentity.entity_id, CatalogueIdentity.namespace).where(
        CatalogueIdentity.namespace.in_(('tvdb.person', 'tmdb.person'))))).all())
    tmdb = {id for id, namespace in identities if namespace == 'tmdb.person'}
    tvdb = {id for id, namespace in identities if namespace == 'tvdb.person'}
    return {'cast_credits':len(credits), 'unlinked_tvdb_people':len(tvdb-tmdb),
            'candidate_duplicate_groups':len(before), 'candidate_affected_titles':len({w for w, _ in before}),
            'projected_duplicate_groups':len(after), 'projected_affected_titles':len({w for w, _ in after}),
            'title_local_artwork_associations':sum(bool(c.character_image_url) and original[c.id] != p.id for c, p in projected),
            'fallback_credits_omitted':len(credits)-len(projected)}


async def main():
    async with AsyncSessionLocal() as db:
        print(json.dumps(await audit(db), sort_keys=True))


if __name__ == '__main__':
    asyncio.run(main())
