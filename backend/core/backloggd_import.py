"""One-time Toolkittd imports into the personal Games list."""
import json
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import select

from core import catalogue, game_tracking, list_categories
from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_browse_sources import BASE_FIELDS, GameBrowseSources
from core.game_source_cache import session_factory
from core.game_title_match import title_formats
from core.game_types import candidate_fields, canonical_game, capabilities, igdb_game_class, mobile_only
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.games import GameEntry
from core.tracking_rules import normalize_score
from schemas_games import GameStatus, Seconds

MAX_BYTES = 5 * 1024 * 1024
MAX_GAMES = 5000
BATCH_SIZE = 50
FIELDS = candidate_fields(BASE_FIELDS + ',alternative_names.name,external_games.uid,external_games.url,external_games.external_game_source.name')
STATUSES: dict[str, GameStatus] = {
    'completed': 'completed', 'mastered': 'completed', 'played': 'played',
    'shelved': 'paused', 'abandoned': 'dropped', 'retired': 'dropped',
}


class ImportGame(BaseModel):
    model_config = ConfigDict(extra='forbid')

    work_id: Annotated[int, Field(gt=0, le=2147483647)]
    status: GameStatus
    score: Annotated[float, Field(ge=0, le=10, allow_inf_nan=False), AfterValidator(normalize_score)] | None = None
    total_seconds: Seconds | None = None


class ImportSelection(BaseModel):
    games: list[ImportGame] = Field(min_length=1, max_length=MAX_GAMES)


class ImportResultGame(BaseModel):
    work_id: int
    title: str
    status: GameStatus
    score: float | None
    total_seconds: int | None


class IgnoredGame(BaseModel):
    title: str
    reason: str


class ImportSummary(BaseModel):
    games: list[ImportResultGame] = Field(default_factory=list)
    ignored: list[IgnoredGame] = Field(default_factory=list)

    def ignore(self, title, reason):
        self.ignored.append(IgnoredGame(title=title, reason=reason))


def parse_export(data):
    try:
        rows = json.loads(data.decode('utf-8-sig'))
    except (ValueError, RecursionError) as exc:
        raise ValueError('Choose a valid Toolkittd Backloggd JSON export.') from exc
    if not isinstance(rows, list) or rows and not any(isinstance(row, dict) and (
            'game_log' in row or {'id', 'name', 'playthroughs'} <= row.keys()) for row in rows):
        raise ValueError('Choose a Toolkittd Backloggd JSON export.')
    if len(rows) > MAX_GAMES:
        raise ValueError(f'Choose a Backloggd export with at most {MAX_GAMES:,} games.')
    return rows


def status_for(log):
    if log.get('is_playing') is True:
        return 'playing'
    status = log.get('status')
    if log.get('is_play') is True and isinstance(status, str):
        return STATUSES.get(status.strip().lower())
    if log.get('is_backlog') is True or log.get('is_wishlist') is True:
        return 'planning'
    if isinstance(status, str):
        return STATUSES.get(status.strip().lower())
    return None


def score_for(value):
    # Toolkittd stores half-stars as units on a 0–10 scale.
    if type(value) not in (int, float) or not 0 < value <= 10:
        return None
    return max(.5, float((Decimal(str(value)) * 2).quantize(Decimal('1'), rounding=ROUND_HALF_UP) / 2))


def total_seconds(log):
    hours, minutes = log.get('total_hours', 0), log.get('total_minutes', 0)
    if all(type(value) is int and value >= 0 for value in (hours, minutes)):
        seconds = hours * 3600 + minutes * 60
        if 0 < seconds < 2**53:
            return seconds
    return None


def matches_title(title, raw):
    names = [raw.get('name'), *(name.get('name') for name in raw.get('alternative_names') or [] if isinstance(name, dict))]
    forms = title_formats(title)
    return any(forms & title_formats(name) for name in names)


async def catalogue_works(db, natives):
    if not natives:
        return {}
    rows = await db.execute(select(CatalogueIdentity.external_id, CatalogueEntity.id,
        CatalogueEntity.name, CatalogueEntity.attributes)
        .join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id)
        .where(CatalogueIdentity.namespace == 'igdb.game', CatalogueIdentity.external_id.in_(natives),
               CatalogueEntity.kind == 'game'))
    return {row.external_id: row for row in rows}


async def resolve_missing(db, natives):
    if not natives:
        return {}
    source = GameBrowseSources(session_factory(db.bind))
    found = {}
    for start in range(0, len(natives), BATCH_SIZE):
        batch = natives[start:start + BATCH_SIZE]
        ids = ','.join(batch)
        rows = await source.igdb('games', f'fields {FIELDS}; where id=({ids}); limit {len(batch)};')
        if not isinstance(rows, list) or any(not isinstance(row, dict) or type(row.get('id')) is not int
                or str(row['id']) not in batch or not row.get('name') for row in rows):
            raise ProviderError('bad_response')
        found.update((str(row['id']), row) for row in rows)
    return found


async def existing_entries(db, user_id, work_ids):
    return set(await db.scalars(select(GameEntry.work_id).where(
        GameEntry.user_id == user_id, GameEntry.work_id.in_(work_ids)))) if work_ids else set()


async def preview(db, user_id, rows):
    summary = ImportSummary()
    candidates = {}
    for index, row in enumerate(rows, 1):
        item = row if isinstance(row, dict) else {}
        title = item.get('name')
        title = title.strip()[:500] if isinstance(title, str) else ''
        native = str(item.get('id', ''))
        log = item.get('game_log')
        if not title or not (native.isascii() and native.isdecimal() and len(native) <= 10 and 0 < int(native) <= 2147483647):
            summary.ignore(title or f'Row {index}', 'Missing game title or invalid ID')
            continue
        native = str(int(native))
        status = status_for(log) if isinstance(log, dict) else None
        if not status:
            summary.ignore(title, 'No supported Backloggd status')
        elif native in candidates:
            summary.ignore(title, 'Duplicate in this export')
        else:
            candidates[native] = (title, log, status)

    works = await catalogue_works(db, candidates)
    need_provider = {native for native, (title, _, _) in candidates.items()
        if native not in works or not matches_title(title, {'name': works[native].name})
        or capabilities(works[native].attributes).game_class == 'unknown'}
    # Release the read connection while waiting for provider admission.
    if need_provider:
        await db.commit()
    raw = await resolve_missing(db, sorted(need_provider))
    pending = {}
    publication = {}
    for native, (title, _, _) in candidates.items():
        if native not in need_provider:
            pending[native] = native
            continue
        source = raw.get(native)
        if not source or not matches_title(title, source):
            summary.ignore(title, 'No matching catalogue game')
            continue
        root = canonical_game(source)
        if not root or not capabilities({'game_class': igdb_game_class(root)}).independent or mobile_only(source):
            summary.ignore(title, 'Unavailable as a standalone AnyList game')
            continue
        root_native = str(root['id'])
        pending[native] = root_native
        previous = publication.get(root_native)
        if previous:
            preferred = root if native == root_native else previous
            ports = {port['id']: port for record in (previous, root) for port in record.get('ports') or []
                     if isinstance(port, dict) and type(port.get('id')) is int}
            root = {**preferred, 'ports': list(ports.values())}
        publication[root_native] = root

    roots = set(pending.values())
    missing = roots - works.keys()
    refresh = {native for native in roots & works.keys()
               if native in publication and capabilities(works[native].attributes).game_class == 'unknown'}
    to_publish = sorted(missing | refresh)
    for start in range(0, len(to_publish), BATCH_SIZE):
        batch = to_publish[start:start + BATCH_SIZE]
        await catalogue.write_lock(db)
        works.update(await catalogue_works(db, batch))
        for native in batch:
            if native in works and capabilities(works[native].attributes).game_class != 'unknown':
                continue
            try:
                async with db.begin_nested():
                    works[native] = await catalogue.upsert_entity(db, normalize_igdb(publication[native])['work'], 'igdb')
            except catalogue.IdentityConflict:
                continue
        await db.commit()

    existing = await existing_entries(db, user_id, {works[native].id for native in roots & works.keys()})
    seen = set()
    for native, root in pending.items():
        title, log, status = candidates[native]
        work = works.get(root)
        if work is None:
            summary.ignore(title, 'Conflicting catalogue identities')
        elif not capabilities(work.attributes).independent:
            summary.ignore(title, 'Unavailable as a standalone AnyList game')
        elif work.id in existing:
            summary.ignore(title, 'Already on your list')
        elif work.id in seen:
            summary.ignore(title, 'Same AnyList game as another export entry')
        else:
            seen.add(work.id)
            summary.games.append(ImportResultGame(work_id=work.id, title=work.name, status=status,
                score=score_for(log.get('rating')), total_seconds=total_seconds(log)))
    await db.commit()
    return summary


async def apply(db, user_id, games):
    summary = ImportSummary()
    await game_tracking.lock_owner(db, user_id)
    ids = {game.work_id for game in games}
    works = {work.id: work for work in await db.execute(select(CatalogueEntity.id,
        CatalogueEntity.name, CatalogueEntity.attributes)
        .where(CatalogueEntity.id.in_(ids), CatalogueEntity.kind == 'game'))}
    existing = await existing_entries(db, user_id, ids)
    for game in games:
        work = works.get(game.work_id)
        if work is None or not capabilities(work.attributes).independent:
            summary.ignore(work.name if work else f'Game #{game.work_id}', 'Game is no longer available')
        elif work.id in existing:
            summary.ignore(work.name, 'Already on your list')
        else:
            existing.add(work.id)
            db.add(GameEntry(user_id=user_id, work_id=work.id, status=game.status, score=game.score,
                total_seconds=game.total_seconds, completion_source='unknown' if game.status == 'completed' else None))
            summary.games.append(ImportResultGame(work_id=work.id, title=work.name, status=game.status,
                score=game.score, total_seconds=game.total_seconds))
    if summary.games:
        await list_categories.restore_populated(db, user_id)
    await db.commit()
    return summary
