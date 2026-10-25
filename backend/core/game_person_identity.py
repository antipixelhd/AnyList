"""Resolve requested IMDb people through explicit provider IDs, never names.

The credit component retains a small check header so warm pages need no extra
identity queries. Provider work runs in the existing person worker, independent
of credit completeness. A missing cross-reference never means an empty career.
"""
import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from core import catalogue, settings_store, tmdb
from core.game_imdb_enrichment import now, request as admit, snapshot, header, status_view
from core.game_imdb_source import identifier
from core.person_identities import verified_tmdb_person
from models.catalogue import CatalogueEntity, CatalogueIdentity

JOB = 'game_person_identity'
COMPONENT = 'game_person_credits'
VERSION = 1


def fresh(value):
    value = value or {}
    if not isinstance(value, dict):
        return False
    try:
        return value.get('version') == VERSION and datetime.fromisoformat(value['next_check_at']) > now()
    except (KeyError, TypeError, ValueError):
        return False


async def request(db, native, entity_id, checked, *, enqueue):
    identifier(native, person=True)
    if not fresh(checked):
        if enqueue:
            return await admit(db, native, kind=JOB, entity_id=entity_id)
        row = await header(db, native, kind=JOB)
        return status_view(row) if row else None
    return None


async def lookup(native, key):
    """Reverse lookup is discovery; the selected profile must confirm the ID."""
    raw = await tmdb.find_by_external_id(native, 'imdb_id', api_key=key)
    rows = raw.get('person_results') if isinstance(raw, dict) else None
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) or type(row.get('id')) is not int
            or not 0 < row['id'] <= 2147483647 for row in rows):
        raise ValueError('Invalid person identity response')
    ids = {row['id'] for row in rows}
    if len(ids) != 1:
        return ('ambiguous' if ids else 'unresolved'), None
    native_id = next(iter(ids))
    profile = await tmdb.get_person_profile(native_id, api_key=key)
    if (not isinstance(profile, dict) or type(profile.get('id')) is not int or profile['id'] != native_id
            or not isinstance(profile.get('name'), str) or not profile['name'].strip()):
        raise ValueError('Invalid person identity profile')
    if profile.get('imdb_id') != native:
        return 'conflict', None
    return 'verified', profile


class GamePersonIdentity:
    def __init__(self, sessions):
        self.sessions = sessions

    async def run(self, native):
        identifier(native, person=True)
        async with self.sessions() as db:
            job = await db.scalar(snapshot(JOB, native).with_for_update())
            if (not job or not job.next_attempt_at or job.next_attempt_at > now()
                    or job.lease_until and job.lease_until > now()):
                return 'cached'
            token = uuid.uuid4().hex
            job.lease_token, job.lease_until = token, now() + timedelta(minutes=2)
            await db.commit()
        try:
            async with self.sessions() as db:
                mapping = await catalogue.resolve_identity(db, 'imdb.person', native)
                if not mapping:
                    raise catalogue.IdentityConflict('IMDb person no longer exists')
                linked = list(await db.scalars(select(CatalogueIdentity.external_id).where(
                    CatalogueIdentity.entity_id == mapping.entity_id, CatalogueIdentity.namespace == 'tmdb.person')))
                key = settings_store.get_server_tmdb_key(await settings_store.get_global_settings(db))
            state, profile = ('verified', None) if len(linked) == 1 else ('conflict', None) if linked else ('unavailable', None)
            if not linked and key:
                async with asyncio.timeout(25):
                    state, profile = await lookup(native, key)
            return await self.publish(native, token, state, profile)
        except asyncio.CancelledError:
            await self.fail(native, token, cancelled=True)
            raise
        except Exception:
            await self.fail(native, token)
            return 'retry'

    async def publish(self, native, token, state, profile):
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            job = await db.scalar(snapshot(JOB, native).with_for_update())
            if not job or job.lease_token != token:
                return 'superseded'
            if profile:
                try:
                    await verified_tmdb_person(db, profile['id'], profile)
                except catalogue.IdentityConflict:
                    state = 'conflict'
            mapping = await catalogue.resolve_identity(db, 'imdb.person', native)
            if not mapping:
                raise catalogue.IdentityConflict('IMDb person no longer exists')
            root = await db.get(CatalogueEntity, mapping.entity_id)
            if root.kind != 'person' or root.canonical_person_id:
                raise catalogue.IdentityConflict('IMDb identity does not reference a canonical person')
            stamp = now()
            expiry = stamp + timedelta(days=30 if state == 'verified' else 1 if state == 'unavailable' else 7)
            evidence = {'version': VERSION, 'state': state, 'checked_at': stamp.isoformat(),
                'next_check_at': expiry.isoformat()}
            component = await db.scalar(snapshot(COMPONENT, native).with_for_update())
            if component:
                component.payload = {**(component.payload or {}), 'identity_bridge': evidence}
                if profile and state == 'verified':
                    component.coverage = {**(component.coverage or {}), 'projection_revision': uuid.uuid4().hex}
                component.entity_id = root.id
            # Preserve source-component freshness and full credits. This job is
            # only identity evidence, not a renewed IMDb filmography fetch.
            job.entity_id = root.id
            # Keep the last positive provider proof across subsequent checks,
            # including retries and reuse of an already stored binding.
            job.payload = {**(job.payload or {}), 'identity': evidence,
                **({'last_verified_binding': {'imdb_id': native, 'tmdb_id': profile['id'],
                    'provider': 'tmdb', 'verified_at': stamp.isoformat(),
                    'methods': ['external_id_lookup', 'person_details.imdb_id'],
                    'sources': [f'https://api.themoviedb.org/3/find/{native}?external_source=imdb_id',
                        f'https://api.themoviedb.org/3/person/{profile["id"]}']}}
                    if profile and state == 'verified' else {})}
            job.coverage = {'complete': True, 'state': state}
            job.fetched_at, job.expires_at = stamp, expiry
            job.next_attempt_at = job.lease_token = job.lease_until = job.error_code = None
            job.attempts = 0
            await db.commit()
            return state

    async def fail(self, native, token, *, cancelled=False):
        async with self.sessions() as db:
            job = await db.scalar(snapshot(JOB, native).with_for_update())
            if not job or job.lease_token != token:
                return
            job.lease_token = job.lease_until = None
            if not cancelled:
                job.attempts += 1
                job.error_code = 'identity_unavailable'
            job.next_attempt_at = now() + timedelta(seconds=2 if cancelled else min(3600, 60 * 2 ** min(job.attempts - 1, 6)))
            await db.commit()
