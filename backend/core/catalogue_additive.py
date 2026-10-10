"""Additive screen catalogue projection for safe admin repairs."""
from copy import deepcopy
from sqlalchemy import select
from core import catalogue
from models.catalogue import CatalogueCredit, CatalogueEntity


async def fill_document(db, document, provider):
    """Only add missing screen metadata; authoritative refresh/delete is not used.

    Lock before comparing fields. Known values, protected fields, source credits,
    and all personal facts are preserved, including credits absent from a reply.
    Conflicting provider identities still fail rather than merge roots by name.
    """
    doc = deepcopy(document)
    if doc['work']['kind'] not in ('movie', 'series'):
        raise catalogue.IdentityConflict('Screen metadata required')
    await catalogue.write_lock(db)

    async def prepare(values):
        roots = {mapping.entity_id for identity in values.get('identities', [])
                 if (mapping := await catalogue.resolve_identity(db, identity['namespace'], identity['external_id']))}
        if len(roots) > 1:
            raise catalogue.IdentityConflict('Identity mappings require review')
        row = await db.get(CatalogueEntity, next(iter(roots))) if roots else None
        if row:
            if row.kind != values['kind']:
                raise catalogue.IdentityConflict('Identity kind requires review')
            for field in ('name', 'description', 'image_url'):
                if catalogue.has_value(getattr(row, field)):
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

