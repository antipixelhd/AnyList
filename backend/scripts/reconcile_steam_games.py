"""Rehearse/apply exact IGDB bridges for legacy Steam-only game roots.

Provider acquisition happens before the catalogue transaction. By default the
catalogue transaction is rolled back; --apply persists successful bridges.
Conflicting or ambiguous roots remain unchanged and are reported explicitly.
"""
import argparse
import asyncio
import hashlib
import json

from sqlalchemy import select, text

from core import catalogue
from core.game_browse_sources import GameBrowseSources
from core.game_types import canonical_game, capabilities, igdb_game_class, mobile_only
from core.game_identity_reconciliation import reconcile
from db import AsyncSessionLocal, engine
from models.catalogue import CatalogueIdentity, CatalogueEntity


async def fingerprint(db, root_ids):
    """Compare private facts in-place without printing any private payloads."""
    digest = hashlib.sha256()
    for table in ('game_entries', 'game_child_states', 'game_favorites', 'game_activity',
                  'game_achievements', 'game_achievement_unlocks'):
        records = await db.scalars(text(f'SELECT row_to_json(r)::text FROM {table} r WHERE work_id = ANY(:roots) ORDER BY row_to_json(r)::text'), {'roots': root_ids})
        digest.update(table.encode())
        for record in records:
            digest.update(record.encode())
    # Progress checkpoints cannot change as a side-effect of catalogue repair.
    records = await db.scalars(text('SELECT row_to_json(r)::text FROM steam_connections r ORDER BY connection_id'))
    for record in records:
        digest.update(record.encode())
    return digest.hexdigest()


async def run(args, *, factory=AsyncSessionLocal, source=None):
    async with factory() as db:
        native = CatalogueIdentity.__table__.alias('native')
        query = select(CatalogueIdentity.external_id, CatalogueIdentity.entity_id).where(
            CatalogueIdentity.namespace == 'steam.app', ~select(native.c.id).where(
                native.c.entity_id == CatalogueIdentity.entity_id, native.c.namespace == 'igdb.game').exists())
        if args.app_id:
            query = query.where(CatalogueIdentity.external_id.in_(args.app_id))
        imported = dict((await db.execute(query.order_by(CatalogueIdentity.external_id))).all())
    source = source or GameBrowseSources(factory)
    references = {}
    apps = list(imported)
    for offset in range(0, len(apps), 100):
        rows = await source.steam_games([int(app) for app in apps[offset:offset + 100]])
        if len(rows) >= 500:
            raise catalogue.IdentityConflict('Capped IGDB references cannot certify a repair')
        for row in rows:
            if str(row.get('uid')) in imported:
                game = canonical_game(row.get('game'))
                if game:
                    references.setdefault(str(row['uid']), {})[str(game['id'])] = game
    results = []
    async with factory() as db:
        await catalogue.write_lock(db)
        roots = sorted(set(imported.values()))
        before = await fingerprint(db, roots)
        for app, expected in imported.items():
            candidates = references.get(app, {})
            if len(candidates) != 1:
                results.append({'app': app, 'root': expected, 'status': 'ambiguous' if candidates else 'unmapped'})
                continue
            native, game = next(iter(candidates.items()))
            kind = igdb_game_class(game)
            if not capabilities({'game_class': kind}).independent or mobile_only(game):
                results.append({'app': app, 'root': expected, 'status': 'excluded'})
                continue
            try:
                async with db.begin_nested():
                    mapping = await catalogue.resolve_identity(db, 'steam.app', app)
                    if not mapping or mapping.entity_id != expected:
                        raise catalogue.IdentityConflict('Steam identity changed during provider acquisition')
                    root = await db.get(CatalogueEntity, expected)
                    previous = await catalogue.resolve_identity(db, 'igdb.game', native)
                    duplicate_id = previous.entity_id if previous and previous.entity_id != expected else None
                    await reconcile(db, [{'namespace': 'steam.app', 'external_id': app},
                        {'namespace': 'igdb.game', 'external_id': native}], 'igdb', {'game_class': kind})
                    row = await catalogue.upsert_entity(db, {'kind': 'game', 'name': root.name,
                        'attributes': {'igdb_id': native, 'game_class': kind},
                        'identities': [{'namespace': 'steam.app', 'external_id': app},
                                       {'namespace': 'igdb.game', 'external_id': native}]}, 'igdb')
                    if row.id != expected:
                        raise catalogue.IdentityConflict('Imported root must be retained')
                results.append({'app': app, 'root': expected, 'igdb': native, 'status': 'reconciled',
                                **({'merged_from': duplicate_id} if duplicate_id else {})})
            except catalogue.IdentityConflict as error:
                results.append({'app': app, 'root': expected, 'status': 'conflict', 'reason': str(error)})
        after = await fingerprint(db, roots)
        if before != after:
            raise RuntimeError('Private tracking or achievement data changed; refusing repair')
        if args.apply:
            await db.commit()
        else:
            await db.rollback()
    report = {'applied': args.apply, 'private_facts_preserved': before == after, 'games': results}
    print(json.dumps(report, ensure_ascii=True))
    return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-id', action='append', default=[])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        await run(args)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
