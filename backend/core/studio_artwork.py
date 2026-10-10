"""Prefer studio branding over work backdrops, without guessing company identities."""
import asyncio
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from core import catalogue, tmdb
from core.contributor_details import positive_id
from models.catalogue import CatalogueEntity, CatalogueIdentity

VERSION = 1


def studio_logo(raw, primary=None):
    candidates = []
    for logo in raw.get('logos') or []:
        if not isinstance(logo, dict):
            continue
        path = logo.get('file_path')
        width, height = logo.get('width'), logo.get('height')
        if not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9]+\.(?:png|svg)', path):
            continue
        if not isinstance(width, (int,float)) or not isinstance(height, (int,float)) or width <= 0 or height <= 0:
            continue
        if path.endswith('.png') and width < 300:
            continue
        ratio = width / height
        # Landscape first, then the provider's main logo and resolution.
        candidates.append(((ratio >= 1.5, path == primary, width, ratio), path))
    if not candidates:
        return None
    return 'https://image.tmdb.org/t/p/original' + max(candidates)[1]


async def backfill_studio_artwork(db, api_key, *, limit=50, force=False):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attrs = CatalogueEntity.attributes
    query = select(CatalogueEntity, CatalogueIdentity).join(CatalogueIdentity,
        CatalogueIdentity.entity_id == CatalogueEntity.id).where(
        CatalogueEntity.kind == 'organization', CatalogueIdentity.namespace == 'tmdb.company')
    if not force:
        query = query.where(or_(attrs['studio_artwork_checked_at'].astext.is_(None),
            attrs['studio_artwork_checked_at'].astext < (now-timedelta(days=30)).isoformat(),
            attrs['studio_artwork_version'].astext != str(VERSION)))
    rows = list((await db.execute(query.order_by(CatalogueEntity.id).limit(limit))).all()) if api_key else []
    result = {'examined':len(rows),'updated':0,'unavailable':0,'protected':0,'failed':0}
    semaphore = asyncio.Semaphore(4)

    async def fetch(row, identity):
        async with semaphore:
            try:
                native = positive_id(identity.external_id)
                if not native:
                    raise ValueError('Invalid studio identity')
                async with asyncio.timeout(25):
                    images = await tmdb.get_company_images(native, api_key=api_key)
                if positive_id(images.get('id')) != native or not isinstance(images.get('logos'), list):
                    raise ValueError('Studio artwork identity mismatch')
                main = (row.attributes or {}).get('logo_path')
                if not main and row.image_url:
                    main = '/' + row.image_url.rsplit('/',1)[-1]
                return identity.external_id, studio_logo(images, main)
            except Exception:
                return None

    replies = await asyncio.gather(*(fetch(row, identity) for row, identity in rows))
    await catalogue.write_lock(db)
    for (row, identity), reply in zip(rows, replies):
        await db.refresh(row, with_for_update=True)
        await db.refresh(identity)
        if reply is None or identity.entity_id != row.id or identity.external_id != reply[0]:
            result['failed'] += 1
            continue
        image = reply[1]
        if 'attributes.studio_banner' in (row.protected_fields or []):
            result['protected'] += 1
        elif image:
            old = (row.attributes or {}).get('studio_banner')
            source = (row.field_sources or {}).get('attributes.studio_banner')
            if not old or source == 'tmdb':
                catalogue.merge_fields(row, {'attributes':{'studio_banner':image}}, 'tmdb')
                result['updated'] += int(old != (row.attributes or {}).get('studio_banner'))
        else:
            result['unavailable'] += 1
        row.attributes = {**(row.attributes or {}), 'studio_artwork_version':VERSION, 'studio_artwork_checked_at':now.isoformat()}
    await db.commit()
    return result
