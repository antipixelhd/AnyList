"""Local actor/role registry and title-first artwork, without provider I/O."""
import re
import unicodedata
import hashlib
import asyncio
from collections import defaultdict
from sqlalchemy import select, true, case, update, bindparam, column, Integer
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm.attributes import set_committed_value
from models.catalogue import CatalogueCredit, CatalogueEntity, ScreenCharacter
from core.catalogue_normalize import image

GENERIC = {"self", "himself", "herself", "themselves", "narrator", "host", "announcer", "voice", "unknown", "uncredited",
           "man", "woman", "boy", "girl", "child", "officer", "police officer", "doctor", "guard", "soldier", "reporter", "waiter", "waitress"}

_projection_cpu = asyncio.Semaphore(1)


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
    from core.catalogue_batch import bounded_scalars
    credits = await bounded_scalars(db,select(CatalogueCredit).where(
        CatalogueCredit.work_id == work_id, CatalogueCredit.role == 'actor').order_by(CatalogueCredit.id)
        .execution_options(populate_existing=True))
    actors = {c.contributor_id for c in credits}
    registry = await bounded_scalars(db,select(ScreenCharacter).where(ScreenCharacter.actor_id.in_(actors))) if actors else []
    if len(credits) + len(registry) > 256:
        async with _projection_cpu:
            selected, created, aliases = await asyncio.to_thread(_character_plan, credits, registry, work_id)
    else:
        selected, created, aliases = _character_plan(credits, registry, work_id)
    # Bound ORM bookkeeping/bind construction per event-loop turn as well as
    # the SQL batches; thousands of new roles must not form one giant flush.
    for start in range(0, len(created), 200):
        db.add_all(created[start:start+200])
        await db.flush()
        await asyncio.sleep(0)
    for start in range(0, len(selected), 200):
        links = []
        for credit, row in selected[start:start+200]:
            if row in aliases:
                row.aliases = aliases.pop(row)
            character_id = row.id if row else None
            if credit.screen_character_id != character_id:
                links.append((credit,character_id))
        await db.flush()
        if links:
            from sqlalchemy import func
            records = func.jsonb_to_recordset(bindparam('role_links',type_=JSONB)).table_valued(
                column('id',Integer),column('character_id',Integer)).render_derived(name='links',with_types=True)
            table = CatalogueCredit.__table__
            await db.execute(update(table).where(table.c.id==records.c.id,
                table.c.screen_character_id.is_distinct_from(records.c.character_id))
                .values(screen_character_id=records.c.character_id),
                {'role_links':[{'id':credit.id,'character_id':identity} for credit,identity in links]})
            for credit,identity in links:
                set_committed_value(credit,'screen_character_id',identity)
        await asyncio.sleep(0)
    return sum(row is not None for _, row in selected)


def _character_plan(credits, registry, work_id):
    """Pure indexed matching; ambiguity is retained rather than picking a role."""
    exact, aliases, labels_by_row = {}, defaultdict(set), {}
    def index(row):
        exact[(row.actor_id, row.role_key)] = row
        labels_by_row[row] = set(row.aliases or [])
        if not row.role_key.startswith('work:'):
            for label in row.aliases or []:
                aliases[(row.actor_id, label)].add(row)
    for row in registry:
        index(row)
    primary = defaultdict(set)
    for c in credits:
        if c.provider == 'tmdb' and normalized_role(c.character_label):
            primary[c.contributor_id].add(role_name(c.character_label))
    selected, created = [], []
    for c in sorted(credits, key=lambda c: (c.provider != 'tmdb', c.id)):
        label = role_name(c.character_label)
        if not normalized_role(label):
            selected.append((c, None))
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
        match = exact.get((c.contributor_id, key))
        if match:
            matches = {match}
        elif reusable:
            matches = set().union(*(aliases[(c.contributor_id, label)] for label in labels))
        else:
            matches = set()
        if len(matches) == 1:
            row = next(iter(matches))
            labels_by_row[row].update(labels)
            if not row.role_key.startswith('work:'):
                for alias in labels:
                    aliases[(row.actor_id, alias)].add(row)
        else:
            row = ScreenCharacter(actor_id=c.contributor_id, role_key=key, name=label[:500], aliases=sorted(labels))
            created.append(row)
            index(row)
        selected.append((c, row))
    return selected, created, {row:sorted(labels) for row, labels in labels_by_row.items()
                               if sorted(labels) != row.aliases}


async def character_images(db, ids, *, work_id=None, show_anime=True):
    if not ids:
        return {}
    result = {}
    ordered = sorted(identity for identity in ids if identity is not None)
    # Statistics can span many titles. Bound bind parameters and each SQL
    # probe batch as well as each character's returned candidates.
    for start in range(0, len(ordered), 256):
        result.update(await _character_images(db, ordered[start:start+256], work_id=work_id, show_anime=show_anime))
    return result


async def _character_images(db, ids, *, work_id=None, show_anime=True):
    if not ids:
        return {}
    # An indexed lateral probe returns four distinct URLs per selected role.
    # Never hydrate credits or unrelated title attributes to choose four images.
    from core.statistics_facts import anime
    from sqlalchemy import func, literal
    attrs = (func.jsonb_build_object(*[part for key in ('genres','original_language','origin_countries','origin_country','production_countries')
                                     for part in (literal(key), CatalogueEntity.attributes[key])])
             if not show_anime else literal({},type_=JSONB))
    characters = select(ScreenCharacter.id).where(ScreenCharacter.id.in_(ids)).subquery()
    candidates = select(CatalogueCredit.character_image_url.label('url'), CatalogueCredit.work_id,
        CatalogueCredit.provider, attrs.label('attributes'), CatalogueCredit.id.label('credit_id')).where(
                CatalogueCredit.screen_character_id == characters.c.id,
                CatalogueCredit.character_image_url.like('https://%'))
    if not show_anime:
        candidates = candidates.join(CatalogueEntity, CatalogueEntity.id == CatalogueCredit.work_id)
        # Filter before LIMIT so excluded titles cannot crowd out usable images.
        from sqlalchemy import cast
        data = CatalogueEntity.attributes
        animated = func.jsonb_path_exists(data, cast(
            '$.genres[*] ? (@ like_regex "^animation$" flag "i" || @.name like_regex "^animation$" flag "i")', JSONPATH))
        # Normalized catalogue countries are ISO strings; accept alpha-3 too,
        # as the shared Python country formatter does.
        def origin(key):
            return func.nullif(func.nullif(func.nullif(data[key], cast('[]', JSONB)), cast('null', JSONB)), cast('""', JSONB))
        origins = func.coalesce(origin('origin_countries'), origin('origin_country'))
        japanese_origin = func.jsonb_path_exists(origins, cast('$[*] ? (@ like_regex "^ *(JP|JPN) *$" flag "i")', JSONPATH))
        japanese_production = func.jsonb_path_exists(data, cast(
            '$.production_countries[*] ? (@ like_regex "^ *(JP|JPN) *$" flag "i" || @.iso_3166_1 like_regex "^ *(JP|JPN) *$" flag "i")', JSONPATH))
        japanese = func.coalesce(data['original_language'].astext.in_(('ja','jpn')), False) | func.coalesce(
            japanese_origin, japanese_production, False)
        candidates = candidates.where(~(func.coalesce(animated, False) & japanese))
    # DISTINCT URL grouping prevents repeated provider associations taking slots.
    candidates = candidates.correlate(characters).distinct(CatalogueCredit.character_image_url).order_by(
        CatalogueCredit.character_image_url, case((CatalogueCredit.work_id == work_id, 0), else_=1),
        CatalogueCredit.work_id, CatalogueCredit.id).subquery()
    limited = select(candidates).order_by(case((candidates.c.work_id == work_id, 0), else_=1),
        candidates.c.work_id, candidates.c.credit_id).limit(4).correlate(characters).lateral()
    rows = (await db.execute(select(characters.c.id, limited.c.url, limited.c.work_id, limited.c.provider,
        limited.c.attributes).select_from(characters).join(limited, true()))).all()
    result = defaultdict(list)
    for character_id, raw_url, source_work_id, provider, attributes in rows:
        url = image(raw_url)
        if url and (show_anime or not anime({k:v for k,v in attributes.items() if v is not None})):
            result[character_id].append({'url': url, 'work_id': source_work_id, 'provider': provider,
                                         'attributes': {k:v for k,v in attributes.items() if v is not None}})
    return result


def role_image_candidates(credit, candidates):
    """Prefer this title's portrayal before the same actor/role elsewhere."""
    current = image(credit.character_image_url)
    return list(dict.fromkeys(([current] if current else [])
        + [i['url'] for i in candidates if i['work_id'] == credit.work_id]
        + [i['url'] for i in candidates if i['work_id'] != credit.work_id]))[:4]


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
