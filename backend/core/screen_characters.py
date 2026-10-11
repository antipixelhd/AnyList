"""Local actor/role registry and title-first artwork, without provider I/O."""
import re
import unicodedata
import hashlib
from collections import defaultdict
from sqlalchemy import select
from models.catalogue import CatalogueCredit, CatalogueEntity, ScreenCharacter
from core.catalogue_normalize import image

GENERIC = {"self", "himself", "herself", "themselves", "narrator", "host", "announcer", "voice", "unknown", "uncredited",
           "man", "woman", "boy", "girl", "child", "officer", "police officer", "doctor", "guard", "soldier", "reporter", "waiter", "waitress"}


def role_name(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s*\((?:voice|uncredited|archive (?:footage|sound))\)\s*", " ", value, flags=re.I).strip()


def normalized_role(value):
    return " ".join(re.findall(r"\w+", role_name(value).casefold(), flags=re.UNICODE))


def role_parts(value):
    return {part for raw in re.split(r"\s*/\s*", role_name(value)) if (part := normalized_role(raw))}


def reusable_role(value):
    parts = role_parts(value)
    return bool(parts) and not any(p in GENERIC or p.startswith(('self ', 'himself ', 'herself ', 'uncredited ')) for p in parts)


def bounded_key(value):
    if len(value) <= 500:
        return value
    return value[:430] + ':' + hashlib.sha256(value.encode()).hexdigest()


async def project_characters(db, work_id):
    """Project existing credits, linking variants only with same-title evidence.

    Actor IDs are already verified. A secondary name may alias a single primary
    role only when an explicit slash component matches. Multiple-role ambiguity
    stays separate. Generic roles are scoped to their title.
    """
    work = await db.get(CatalogueEntity, work_id)
    if not work or work.kind not in ('movie', 'series'):
        return 0
    credits = list(await db.scalars(select(CatalogueCredit).where(
        CatalogueCredit.work_id == work_id, CatalogueCredit.role == 'actor').order_by(CatalogueCredit.id)
        .execution_options(populate_existing=True)))
    actors = {c.contributor_id for c in credits}
    registry = list(await db.scalars(select(ScreenCharacter).where(ScreenCharacter.actor_id.in_(actors)))) if actors else []
    by_actor = defaultdict(list)
    for row in registry:
        by_actor[row.actor_id].append(row)
    primary = defaultdict(set)
    for c in credits:
        if c.provider == 'tmdb' and normalized_role(c.character_label):
            primary[c.contributor_id].add(role_name(c.character_label))
    selected = []
    for c in sorted(credits, key=lambda c: (c.provider != 'tmdb', c.id)):
        label = role_name(c.character_label)
        if not normalized_role(label):
            c.screen_character_id = None
            continue
        labels = {normalized_role(label)}
        p = primary[c.contributor_id]
        if c.provider != 'tmdb' and len(p) == 1:
            canonical = next(iter(p))
            if reusable_role(label) and reusable_role(canonical) and role_parts(label) & role_parts(canonical):
                labels.add(normalized_role(canonical))
                label = canonical
        key = normalized_role(label)
        reusable = reusable_role(label)
        if not reusable:
            key = f'work:{work_id}:{key}'
        key = bounded_key(key)
        rows = by_actor[c.contributor_id]
        exact = [r for r in rows if r.role_key == key]
        matches = exact or [r for r in rows if reusable and not r.role_key.startswith('work:') and labels.intersection(r.aliases or [])]
        if len(matches) == 1:
            row = matches[0]
            aliases = sorted(set(row.aliases or []) | labels)
            if aliases != row.aliases:
                row.aliases = aliases
        else:
            row = ScreenCharacter(actor_id=c.contributor_id, role_key=key, name=label[:500], aliases=sorted(labels))
            db.add(row); rows.append(row)
        selected.append((c, row))
    await db.flush()
    for credit, row in selected:
        credit.screen_character_id = row.id
    await db.flush()
    return len(selected)


async def character_images(db, ids):
    if not ids:
        return {}
    rows = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(CatalogueEntity,
        CatalogueEntity.id == CatalogueCredit.work_id).where(CatalogueCredit.screen_character_id.in_(ids),
        CatalogueCredit.character_image_url.isnot(None)).order_by(CatalogueCredit.work_id, CatalogueCredit.id))).all()
    result = defaultdict(list)
    for credit, work in rows:
        url = image(credit.character_image_url)
        if url and not credit.screen_character_id is None:
            result[credit.screen_character_id].append({'url': url, 'work_id': work.id, 'provider': credit.provider,
                'name': work.name, 'attributes': work.attributes or {}})
    return result


async def character_detail(db, character_id):
    row = await db.get(ScreenCharacter, character_id)
    if not row:
        return None
    actor = await db.get(CatalogueEntity, row.actor_id)
    credits = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(CatalogueEntity,
        CatalogueEntity.id == CatalogueCredit.work_id).where(CatalogueCredit.screen_character_id == row.id)
        .order_by(CatalogueCredit.work_id, CatalogueCredit.id))).all()
    works = {}
    for credit, work in credits:
        target = works.setdefault(work.id, {'id': work.id, 'name': work.name, 'kind': work.kind, 'images': []})
        url = image(credit.character_image_url)
        if url and url not in [i['url'] for i in target['images']]:
            target['images'].append({'url': url, 'provider': credit.provider})
    return {'id': row.id, 'name': row.name, 'actor_id': actor.id, 'actor_name': actor.name,
            'aliases': row.aliases or [], 'media': list(works.values())}
