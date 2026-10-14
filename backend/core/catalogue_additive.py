"""Additive screen catalogue projection for safe admin repairs."""
from copy import deepcopy
from sqlalchemy import select
from core import catalogue
from models.catalogue import CatalogueCredit, CatalogueEntity


async def fill_document(db, document, provider):
    """Only add missing screen metadata; authoritative refresh/delete is not used.

    Lock before comparing fields. Known values, protected fields, source credits,
    and all personal facts are preserved, including credits absent from a reply.
    Unprotected TVDB default portraits may adopt the preferred TMDB image.
    Conflicting provider identities still fail rather than merge roots by name.
    """
    doc = deepcopy(document)
    if doc['work']['kind'] not in ('movie', 'series'):
        raise catalogue.IdentityConflict('Screen metadata required')
    await catalogue.write_lock(db)

    async def prepare(values):
        if values['kind'] == 'person':
            from core.person_identities import reconcile_person_identities
            await reconcile_person_identities(db, values.get('identities', []), provider)
        elif values['kind'] == 'organization':
            from core.person_identities import reconcile_company_identities
            await reconcile_company_identities(db, values.get('identities', []), provider)
        roots = {mapping.entity_id for identity in values.get('identities', [])
                 if (mapping := await catalogue.resolve_identity(db, identity['namespace'], identity['external_id']))}
        if len(roots) > 1:
            raise catalogue.IdentityConflict('Identity mappings require review')
        row = await db.get(CatalogueEntity, next(iter(roots))) if roots else None
        if row:
            if row.kind != values['kind']:
                raise catalogue.IdentityConflict('Identity kind requires review')
            for field in ('name', 'description', 'image_url'):
                if catalogue.has_value(getattr(row, field)) and not (
                        field == 'image_url' and catalogue.preferred_person_portrait(row, values.get(field), provider)):
                    values[field] = None
            values['attributes'] = {key: value for key, value in values.get('attributes', {}).items()
                                   if not catalogue.has_value((row.attributes or {}).get(key))}
            # Normal ingestion treats an existing [] as a supplied field. Remove
            # only empty, unprotected keys that a real incoming value can fill.
            row.attributes = {key: value for key, value in (row.attributes or {}).items()
                              if catalogue.has_value(value) or not catalogue.has_value(values['attributes'].get(key))
                              or 'attributes.' + key in (row.protected_fields or [])}
        return row

    work = await prepare(doc['work'])
    existing = {c.source_key: c for c in await db.scalars(select(CatalogueCredit).where(
        CatalogueCredit.work_id == work.id, CatalogueCredit.provider == provider))} if work else {}
    new_credits = []
    for credit in doc['credits']:
        person = await prepare(credit['contributor'])
        old = existing.get(str(credit['source_key'])[:200])
        if old:
            # A changed known performer or job is reviewable, never auto-replaced.
            if person and old.contributor_id == person.id and old.role == credit['role']:
                await catalogue.upsert_entity(db, credit['contributor'], provider)
                # Selection/counts describe this provider association, not a
                # protected title or user fact. Refresh them on cached repairs.
                old.attributes = {**(old.attributes or {}), **credit.get('attributes', {})}
                if old.position is None and credit.get('position') is not None:
                    old.position = credit['position']
                for field in ('role_label', 'character_label', 'character_image_url'):
                    if not catalogue.has_value(getattr(old, field)) and credit.get(field):
                        setattr(old, field, credit[field])
            continue
        new_credits.append(credit)
    doc['credits'] = new_credits
    doc['coverage'] = {**doc.get('coverage', {}), 'credits_complete': False}
    for field in ('characters', 'editions', 'releases', 'relationships', 'performances'):
        if field in doc:
            doc[field] = []
    return await catalogue.ingest_document(db, doc, provider)
