"""Collect provider evidence before applying category decisions to AnyList.

Source snapshots remain observations, not copies of canonical state. A provider
that cannot receive pushes therefore does not repeatedly propose an old value.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from core.status_provenance import naive_utc

_MISSING = object()


@dataclass(frozen=True)
class Observation:
    source: str
    value: Any
    changed_at: datetime | None = None
    previous: Any = field(default_factory=lambda: _MISSING)
    observed_after: datetime | None = None
    inferred: bool = False


@dataclass(frozen=True)
class Decision:
    value: Any
    sources: frozenset[str] = frozenset()
    changed_at: datetime | None = None
    conflict: bool = False
    stale: bool = False


def resolve(local, local_at, observations, *, started_at=None, compatible=None):
    """Three-way resolve real deltas, using mutation clocks, never fetch order.

    An unknown clock may be ordered after a local choice already seen in that
    provider's baseline. Otherwise it cannot defeat a different known value.
    """
    local_at = naive_utc(local_at)
    if started_at and local_at and local_at > started_at:
        return Decision(local, stale=True)
    changed = [o for o in observations if o.previous is _MISSING or o.value != o.previous]
    # A snapshot delta has an inferred clock shared by every provider in this
    # cycle. It orders successive cycles, never competing edits within one pull.
    changed = [replace(o, changed_at=started_at, inferred=True)
               if started_at and o.changed_at is None and o.previous is not _MISSING else o
               for o in changed]
    if not changed:
        return Decision(local)
    # A known older edit is useful history, but cannot replace current state.
    candidates = [o for o in changed if not (local_at and o.changed_at and naive_utc(o.changed_at) < local_at)]
    if not candidates:
        return Decision(local, stale=True)
    values = []
    for o in candidates:
        if o.value not in values:
            values.append(o.value)
    if len(values) == 1:
        value = values[0]
        if value != local and local is not None:
            ordered = any(o.changed_at and (not local_at or naive_utc(o.changed_at) > local_at)
                          or o.previous is not _MISSING and o.previous == local
                          and (not local_at or o.observed_after and naive_utc(o.observed_after) >= local_at)
                          for o in candidates)
            if not ordered:
                return Decision(local, conflict=True)
        clocks = [naive_utc(o.changed_at) for o in candidates if o.changed_at]
        return Decision(value, frozenset(o.source for o in candidates), max(clocks, default=None))
    if compatible:
        merged = compatible(values)
        if merged is not _MISSING:
            return Decision(merged, frozenset(o.source for o in candidates),
                            max((naive_utc(o.changed_at) for o in candidates if o.changed_at), default=None))
    # Every disagreement must have an orderable clock to select a winner.
    if all(o.changed_at is not None and not o.inferred for o in candidates):
        latest = max(naive_utc(o.changed_at) for o in candidates)
        winners = [o for o in candidates if naive_utc(o.changed_at) == latest]
        if all(o.value == winners[0].value for o in winners):
            return Decision(winners[0].value, frozenset(o.source for o in winners), latest)
    return Decision(local, conflict=True)


@dataclass
class Reconciliation:
    user_id: int
    started_at: datetime = field(default_factory=datetime.utcnow)
    collecting: bool = True
    ratings: dict[tuple[int, int | None], list[Observation]] = field(default_factory=dict)
    snapshots: list[dict] = field(default_factory=list)
    histories: list[dict] = field(default_factory=list)
    raw_progress: dict[int, list[Observation]] = field(default_factory=dict)
    initial_import: bool = False
    playback: dict[int, Decision] = field(default_factory=dict)
    library_before: dict[tuple[int, int], bool] = field(default_factory=dict)
    library_local: dict[int, tuple] = field(default_factory=dict)
    library_observed: dict[int, datetime | None] = field(default_factory=dict)
    entries_before: dict[int, dict] = field(default_factory=dict)
    watch_before: set[int] = field(default_factory=set)
    staged_watches: list[Any] = field(default_factory=list)
    watch_confirmations: list[tuple] = field(default_factory=list)
    date_corrections: list[tuple] = field(default_factory=list)
    watch_evidence: dict[int, dict[str, datetime | None]] = field(default_factory=dict)
    watch_previous: dict[str, dict[str, str | None]] = field(default_factory=dict)
    watch_decisions: dict[int, Decision] = field(default_factory=dict)
    deferred_push_back: dict[int, dict[int, str]] = field(default_factory=dict)
    server_imports: dict[int, tuple[dict, dict]] = field(default_factory=dict)
    cloud_rating_baselines: dict[str, tuple[dict, datetime | None]] = field(default_factory=dict)
    source_approvals: dict[str, bool] = field(default_factory=dict)
    protected_fields: dict[int, set[str]] = field(default_factory=dict)
    connection_versions: dict[int, int] = field(default_factory=dict)
    tracking_roots: dict[int, int] = field(default_factory=dict)
    collection_files: dict[tuple, tuple] = field(default_factory=dict)
    collection_removals: list[tuple] = field(default_factory=list)
    collection_deleted: set[int] = field(default_factory=set)


_current: ContextVar[Reconciliation | None] = ContextVar("account_reconciliation", default=None)
_source: ContextVar[str] = ContextVar("reconciliation_source", default="")


def add_watch_event(db, row):
    """Keep imported viewing records invisible until the account decision commits."""
    state = collecting(row.user_id)
    if state and _source.get():
        state.staged_watches.append((_source.get(), row))
        if row.completed:
            collect_watch(row.media_id, None if row.date_inferred else row.watched_at)
    else:
        db.add(row)


def collecting(user_id):
    state = _current.get()
    return state if state and state.user_id == user_id and state.collecting else None


def source_key(provider, connection_id=None):
    return f"connection:{connection_id}" if connection_id else provider


async def collect_rating(db, user_id, provider, media_id, season_number, score, rated_at, *, conn=None, previous=_MISSING, observed_after=None):
    state = collecting(user_id)
    if state is None:
        return False
    source = source_key(provider, conn.id if conn else None)
    if conn is None:
        if provider not in state.cloud_rating_baselines:
            from sqlalchemy import select
            from models.tracking import CloudBaseline
            baseline = (await db.execute(select(CloudBaseline).where(
                CloudBaseline.user_id == user_id, CloudBaseline.provider == provider))).scalar_one_or_none()
            state.cloud_rating_baselines[provider] = (
                (baseline.snapshot or {}).get("rating_observations", {}) if baseline else {},
                baseline.observed_at if baseline else None)
        values, observed_after = state.cloud_rating_baselines[provider]
        saved = values.get(f"{media_id}:{season_number}")
        if saved is not None:
            previous = saved["value"]
    state.ratings.setdefault((media_id, season_number), []).append(
        Observation(source, float(score), naive_utc(rated_at), previous, observed_after))
    return True


def collect_watch(media_id, changed_at):
    """Retain source evidence even when a play is already deduplicated locally."""
    state = _current.get()
    source = _source.get()
    if not state or not state.collecting or not source:
        return
    values = state.watch_evidence.setdefault(media_id, {})
    clock = naive_utc(changed_at)
    if source not in values or clock and (values[source] is None or clock > values[source]):
        values[source] = clock


async def initialize(db, state):
    from sqlalchemy import select
    from models import Collection, CollectionFile, WatchEvent
    from models.tracking import TrackedEntry, CloudBaseline
    from models import MediaServerConnection
    state.connection_versions = {conn.id: conn.identity_version for conn in (await db.execute(
        select(MediaServerConnection).where(MediaServerConnection.user_id == state.user_id))).scalars()}
    state.entries_before = {entry.media_id: {field: getattr(entry, field) for field in
        ("status", "progress", "start_date", "finish_date")} for entry in (await db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == state.user_id))).scalars()}
    state.watch_before = set((await db.execute(select(WatchEvent.media_id).where(
        WatchEvent.user_id == state.user_id, WatchEvent.completed.is_(True)))).scalars())
    for baseline in (await db.execute(select(CloudBaseline).where(CloudBaseline.user_id == state.user_id))).scalars():
        state.watch_previous[baseline.provider] = (baseline.snapshot or {}).get("watch_observations", {})
        state.cloud_rating_baselines[baseline.provider] = ((baseline.snapshot or {}).get("rating_observations", {}), baseline.observed_at)
    from models.streaming_library import StreamingLibraryIntent
    rows = (await db.execute(select(CollectionFile.connection_id, Collection.media_id)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .where(Collection.user_id == state.user_id, CollectionFile.connection_id.isnot(None)))).all()
    state.library_before = {(connection_id, media_id): True for connection_id, media_id in rows}
    state.library_local = {media_id: (True, None) for _, media_id in rows}
    from models.tracking import StreamBaseline
    from models import Media
    for baseline in (await db.execute(select(StreamBaseline).where(StreamBaseline.user_id == state.user_id))).scalars():
        state.library_observed[baseline.connection_id] = baseline.observed_at
        snapshot = baseline.snapshot or {}
        # A prior successful snapshot remains the delta base after an interrupted
        # pull, even if that pull already updated its physical source rows.
        if "library" in snapshot:
            mappings = snapshot.get("mappings", {})
            tmdb_ids = {mappings.get(str(key)) for key in snapshot["library"]} - {None}
            media_rows = (await db.execute(select(Media).where(Media.tmdb_id.in_(tmdb_ids),
                Media.media_type.in_(("movie", "series"))))).scalars().all()
            kinds = {str(row.get("content_id")): row.get("content_type") for row in snapshot.get("records", {}).get("library", [])}
            prior = {(baseline.connection_id, media.id) for key in snapshot["library"] for media in media_rows
                     if media.tmdb_id == mappings.get(str(key)) and (not kinds.get(str(key)) or media.media_type.value == kinds[str(key)])}
            state.library_before = {key: value for key, value in state.library_before.items() if key[0] != baseline.connection_id}
            state.library_before.update({key: True for key in prior})
            for _, media_id in prior:
                state.library_local.setdefault(media_id, (True, None))
        state.watch_previous[source_key("", baseline.connection_id)] = (baseline.snapshot or {}).get("watch_observations", {})

    intents = (await db.execute(select(StreamingLibraryIntent).where(StreamingLibraryIntent.user_id == state.user_id))).scalars()
    for intent in intents:
        state.library_local[intent.media_id] = (intent.desired, intent.updated_at)


async def _review(db, state, media_id, category, decision, observations, *, season_number=None):
    from sqlalchemy import select
    from models.tracking import SyncReview
    kind = {"rating": "rating_conflict", "library": "library_conflict",
            "watched state": "watch_conflict", "progress": "progress_conflict"}.get(category, "conflict")
    review = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == state.user_id, SyncReview.media_id == media_id,
        SyncReview.provider == "combined", SyncReview.kind == kind,
        SyncReview.season_number == season_number, SyncReview.state == "pending"))).scalar_one_or_none()
    payload = {"category": category, "observations": [dict(source=o.source, value=o.value,
        changed_at=o.changed_at.isoformat()+"Z" if o.changed_at else None) for o in observations]}
    if review is None:
        review = SyncReview(user_id=state.user_id, media_id=media_id, provider="combined", kind=kind,
                            season_number=season_number, message=f"Connected providers disagree about {category}; the current value was preserved.")
        db.add(review)
    payload["changes"] = [{"field": category, "previous": str(decision.value),
                           "proposed": "; ".join(f"{o.source}: {o.value}" for o in observations)}]
    review.payload = payload
    if category == "rating":
        review.previous_score = decision.value
        review.proposed_score = observations[0].value
    elif category == "playback":
        review.previous_status = decision.value[0] if decision.value else None
        review.proposed_status = observations[0].value[0]


async def _persist_observations(db, state, category, updates):
    """Batch JSON baselines once per source rather than once per imported item."""
    from sqlalchemy import select
    from models.tracking import CloudBaseline, StreamBaseline
    connection_ids = {int(source.split(":")[1]) for source in updates if source.startswith("connection:")}
    providers = {source for source in updates if not source.startswith("connection:")}
    baselines = {source_key("", row.connection_id): row for row in (await db.execute(select(StreamBaseline).where(
        StreamBaseline.user_id == state.user_id, StreamBaseline.connection_id.in_(connection_ids)))).scalars()} if connection_ids else {}
    if providers:
        baselines.update({row.provider: row for row in (await db.execute(select(CloudBaseline).where(
            CloudBaseline.user_id == state.user_id, CloudBaseline.provider.in_(providers)))).scalars()})
    for source, values in updates.items():
        baseline = baselines.get(source)
        if baseline:
            snapshot = dict(baseline.snapshot or {})
            baseline.snapshot = {**snapshot, category: {**snapshot.get(category, {}), **values}}


async def _retire_review(db, state, media_id, kind, season_number=None):
    from sqlalchemy import update
    from models.tracking import SyncReview
    await db.execute(update(SyncReview).where(SyncReview.user_id == state.user_id,
        SyncReview.media_id == media_id, SyncReview.provider == "combined", SyncReview.kind == kind,
        SyncReview.season_number == season_number, SyncReview.state == "pending").values(state="dismissed"))


async def _ratings(db, state, cycle):
    from sqlalchemy import select
    from models import Rating
    from models.tracking import TrackedEntry
    from core.cloud_rating_reconciliation import _entry_score, _set_entry_score
    from core.rating_projection import is_projected_echo
    ids = {key[0] for key in state.ratings}
    if not ids:
        return
    entries = {e.media_id: e for e in (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == state.user_id, TrackedEntry.media_id.in_(ids)))).scalars()}
    rows = {(r.media_id, r.season_number): r for r in (await db.execute(select(Rating).where(
        Rating.user_id == state.user_id, Rating.media_id.in_(ids), Rating.episode_order.is_(None)))).scalars()}
    updates = {}
    for key, observations in state.ratings.items():
        observations.sort(key=lambda item: item.source)
        row, entry = rows.get(key), entries.get(key[0])
        local = _entry_score(entry, key[1])
        if local is None and row:
            local = row.rating
        clock = row.rated_at if row else entry.updated_at if entry else None
        raw_observations = list(observations)
        observations = []
        for observation in raw_observations:
            value = local if local is not None and is_projected_echo(local, observation.value) else observation.value
            previous = value if observation.previous is not _MISSING and observation.previous == observation.value else observation.previous
            observations.append(Observation(observation.source, value, observation.changed_at, previous, observation.observed_after))
        decision = resolve(local, clock, observations, started_at=state.started_at)
        if entry and key[1] is None and entry.rating_mode == "average" and decision.value != local:
            decision = Decision(local, conflict=True)
        if decision.conflict:
            await _review(db, state, key[0], "rating", decision, observations, season_number=key[1])
        elif decision.sources and decision.value != local:
            await _retire_review(db, state, key[0], "rating_conflict", key[1])
            if row is None:
                row = Rating(user_id=state.user_id, media_id=key[0], season_number=key[1])
                db.add(row)
            row.rating = decision.value
            row.rated_at = decision.changed_at or state.started_at
            if entry:
                _set_entry_score(entry, key[1], decision.value)
            if any([await approved_source(db, state.user_id, source) for source in decision.sources]):
                cycle.new_ratings[key] = decision.value
                cycle.rating_sources[key] = set(decision.sources)
        # Observe even rejected values. An unchanged pull-only source is context,
        # not a new edit on the next cycle. Pending reviews remain available.
        for observation in raw_observations:
            if observation.source.startswith("connection:"):
                continue
            updates.setdefault(observation.source, {})[f"{key[0]}:{key[1]}"] = {"value": observation.value}
    await _persist_observations(db, state, "rating_observations", updates)
    await db.commit()


class _ReconciliationSession:
    """Keep existing helper commit boundaries inside one account transaction."""
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def commit(self):
        await self.session.flush()


async def finalize(db, state, cycle, *, job_id=None):
    await _finalize(_ReconciliationSession(db), state, cycle)
    cycle.push_back = state.deferred_push_back
    cycle.connection_versions = state.connection_versions
    if job_id is not None:
        from core.sync_delivery import persist_delivery
        await persist_delivery(db, job_id, cycle)
    await db.commit()


async def _finalize(db, state, cycle):
    """Apply only collected deltas after every available provider has settled."""
    from sqlalchemy import select
    from models import User
    from models.tracking import TrackedEntry
    await db.execute(select(User.id).where(User.id == state.user_id).with_for_update())
    for entry in (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == state.user_id))).scalars():
        before = state.entries_before.get(entry.media_id)
        if before:
            state.protected_fields[entry.media_id] = {field for field, value in before.items() if getattr(entry, field) != value}
        elif entry.status_source == "local":
            state.protected_fields[entry.media_id] = {"status", "progress", "start_date", "finish_date"}
    from models import MediaServerConnection
    connections = {conn.id: conn.identity_version for conn in (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == state.user_id))).scalars()}
    def valid(source):
        return not source.startswith("connection:") or connections.get(int(source.split(":")[1])) == state.connection_versions.get(int(source.split(":")[1]))
    state.staged_watches = [item for item in state.staged_watches if valid(item[0])]
    state.watch_confirmations = [item for item in state.watch_confirmations if valid(item[0])]
    state.date_corrections = [item for item in state.date_corrections if valid(item[0])]
    state.ratings = {key: [o for o in values if valid(o.source)] for key, values in state.ratings.items()}
    state.raw_progress = {key: [o for o in values if valid(o.source)] for key, values in state.raw_progress.items()}
    state.watch_evidence = {key: {source: clock for source, clock in values.items() if valid(source)}
                            for key, values in state.watch_evidence.items()}
    state.histories = [item for item in state.histories if valid(source_key(item["provider"], item["connection_id"]))]
    state.collecting = False
    from core.sync_collection import apply_memberships
    await apply_memberships(db, state)
    from core.media_server_reconciliation import record_media_server_import
    for connection_id, (stats, ratings) in state.server_imports.items():
        conn = await db.get(MediaServerConnection, connection_id)
        if conn and valid(source_key(conn.type, conn.id)):
            await record_media_server_import(db, conn, stats, complete=True, observed_ratings=ratings)
    # Worker delivery requests are proposals. Rebuild delivery from accepted
    # shared decisions rather than retaining a raw provider's fan-out request.
    cycle.new_watched_ids.clear()
    cycle.watched_exclusions.clear()
    cycle.new_ratings.clear()
    await _ratings(db, state, cycle)
    from core.tracking_import import import_tracking_history
    added = set()
    await _watches(db, state, cycle)
    await _watch_dates(db, state)
    await import_tracking_history(db, state.user_id, added, initial_import=state.initial_import)
    await _playback(db, state, cycle, added)
    await _raw_progress(db, state)
    await import_tracking_history(db, state.user_id, added, initial_import=state.initial_import)
    await _history(db, state, cycle, added)
    from models.base import CollectionSource
    for media_id in set(cycle.new_watched_ids):
        decision = state.watch_decisions.get(media_id)
        if decision and (decision.conflict or decision.stale or decision.value is False):
            cycle.new_watched_ids.discard(media_id)
            cycle.watched_exclusions.pop(media_id, None)
            continue
        evidence = state.watch_evidence.get(media_id, {})
        clocks = [clock for clock in evidence.values() if clock is not None]
        if clocks:
            winners = {source for source, clock in evidence.items() if clock == max(clocks)}
            cycle.watched_exclusions[media_id] = (
                {int(source.split(":")[1]) for source in winners if source.startswith("connection:")},
                {CollectionSource(source) for source in winners if not source.startswith("connection:")})
    await _library(db, state, cycle)


def playback_allowed(user_id, connection_id, media_id, row=None):
    state = _current.get()
    if not state or state.user_id != user_id or state.collecting:
        return None
    decision = state.playback.get(media_id)
    if decision is None:
        watched = state.watch_decisions.get(media_id)
        if watched and (watched.conflict or watched.stale):
            return False
        return None
    if source_key("", connection_id) not in decision.sources or decision.conflict or decision.stale:
        return False
    if row and row.get("duration") and 0 < float(row.get("position") or 0) / float(row["duration"]) < 0.9:
        return decision.value == ("watching", row.get("season"), row.get("episode"), row.get("position"), row.get("duration"))
    return True


def reconcile_completions(observations, completions):
    """A newer completion covers resume evidence for that movie/episode only."""
    result = list(observations)
    for completion in completions:
        def covers(observation):
            return completion.value[0] == "completed" or (
                observation.value[0] == "watching" and all(value is not None for value in observation.value[1:3])
                and observation.value[1:3] <= completion.value[1:3])
        # Earlier episode history enriches the series without replacing a later resume.
        if completion.value[0] != "completed" and any(
                o.value[0] == "watching" and all(value is not None for value in o.value[1:3])
                and o.value[1:3] > completion.value[1:3] for o in result):
            continue
        result = [o for o in result if not (covers(o) and (
            o.changed_at and completion.changed_at and completion.changed_at > o.changed_at
            or o.source == completion.source and (not o.changed_at or not completion.changed_at
                                                  or completion.changed_at >= o.changed_at)))]
        result.append(completion)
    return result


async def _playback(db, state, cycle, added):
    from sqlalchemy import select
    from models import Media, MediaServerConnection
    from models.tracking import StreamBaseline, TrackedEntry
    from core.tracking_snapshot import _active, same_playback, provider_changed_at, observe_stream_snapshot, completed_progress, completed_rows
    from models import PlaybackProgress, Show
    from core.status_provenance import status_changed_at
    candidates = {}
    completions = {}
    records = {}
    tmdb_ids = {int(value) for snapshot in state.snapshots for value in snapshot["tmdb_ids"].values() if value is not None}
    for snapshot in state.snapshots:
        baseline = await db.get(StreamBaseline, snapshot["connection_id"])
        tmdb_ids.update(int(value) for value in ((baseline.snapshot or {}).get("mappings", {}) if baseline else {}).values() if value is not None)
    media_rows = (await db.execute(select(Media).where(Media.tmdb_id.in_(tmdb_ids)))).scalars()
    lookup = {(m.tmdb_id, m.media_type.value): m.id for m in media_rows}
    for snapshot in state.snapshots:
        conn = await db.get(MediaServerConnection, snapshot["connection_id"])
        if conn is None or conn.user_id != state.user_id or conn.identity_version != snapshot.get("identity_version", conn.identity_version):
            continue
        baseline = await db.get(StreamBaseline, conn.id)
        previous = baseline.snapshot or {} if baseline else {}
        active = _active(snapshot["progress"]) if snapshot["sync_playback"] else {}
        old = previous.get("progress", {})
        mappings = {**previous.get("mappings", {}), **snapshot["tmdb_ids"]}
        completed = [*snapshot["watched"], *(completed_rows(snapshot["progress"]) if snapshot["sync_playback"] else [])]
        previous_progress = {str(row.get("content_id")): row for row in previous.get("records", {}).get("progress", [])}
        for row in completed_rows(snapshot["progress"]) if snapshot["sync_playback"] else []:
            key = str(row.get("content_id"))
            prior = previous_progress.get(key)
            if prior and same_playback(prior, row) and provider_changed_at(prior) == provider_changed_at(row):
                continue
            media_id = lookup.get((mappings.get(key), row.get("content_type")))
            if media_id is None:
                continue
            value = ("completed", None, None, None, None) if row.get("content_type") == "movie" else (
                "watching", row.get("season"), row.get("episode"), None, None)
            prior_value = ("watching", prior.get("season"), prior.get("episode"), prior.get("position"), prior.get("duration")) if prior else _MISSING
            completions.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id), value,
                provider_changed_at(row), prior_value, baseline.observed_at if baseline else None))
        for key, row in active.items():
            prior = old.get(key)
            if prior and same_playback(prior, row) and prior.get("last_watched") == row.get("last_watched"):
                continue
            media_id = lookup.get((mappings.get(key), row.get("content_type")))
            if media_id is None:
                continue
            records[(media_id, conn.id)] = row
            value = ("watching", row.get("season"), row.get("episode"), row.get("position"), row.get("duration"))
            prior_value = ("watching", prior.get("season"), prior.get("episode"), prior.get("position"), prior.get("duration")) if prior else _MISSING
            candidates.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id), value,
                provider_changed_at(row), prior_value, baseline.observed_at if baseline else None))
        if conn.type == "nuvio" and baseline and snapshot["complete"]:
            previous_visibility = previous.get("nuvio_visibility", {})
            for surface, values in (snapshot.get("cw_visibility") or {}).items():
                if surface not in previous_visibility:
                    continue
                for content_id in set(values) - set(previous_visibility[surface]):
                    key = str(content_id).split("|", 1)[0] if surface == "mobile" else str(content_id)
                    media_id = lookup.get((mappings.get(key), "series")) or lookup.get((mappings.get(key), "movie"))
                    if media_id is None:
                        continue
                    is_movie = (mappings.get(key), "movie") in lookup
                    if is_movie and any(str(row.get("content_id")) == key for row in completed):
                        continue
                    candidates.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id),
                        ("dropped" if is_movie else "paused", None, None, None, None), None,
                        ("watching", None, None, None, None), baseline.observed_at))
        if snapshot["complete"] and baseline and snapshot["sync_playback"]:
            for key in set(old) - set(active):
                row = old[key]
                if completed_progress(row, completed):
                    continue
                media_id = lookup.get((mappings.get(key), row.get("content_type")))
                if media_id is not None:
                    value = ("dropped" if row.get("content_type") == "movie" else "paused", None, None, None, None)
                    candidates.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id), value,
                        None, ("watching", row.get("season"), row.get("episode"), row.get("position"), row.get("duration")), baseline.observed_at))
    for actual_id, observations in state.raw_progress.items():
        media = await db.get(Media, actual_id)
        if media is None:
            continue
        root = media
        if media.show_id:
            show = await db.get(Show, media.show_id)
            root = (await db.execute(select(Media).where(Media.tmdb_id == show.tmdb_id,
                Media.media_type == "series"))).scalar_one_or_none() if show else None
        if root is None:
            continue
        for observation in observations:
            if observation.previous is not _MISSING and observation.value == observation.previous:
                continue
            seconds, percent = observation.value
            duration = seconds / (percent / 100) if percent > 0 else 0
            record = dict(content_type=root.media_type.value, season=media.season_number,
                episode=media.episode_number, position=seconds*1000, duration=duration*1000,
                updated_at=observation.changed_at)
            connection_id = int(observation.source.split(":")[1])
            records[(root.id, connection_id)] = record
            prior = observation.previous
            value = ("watching", media.season_number, media.episode_number, record["position"], record["duration"])
            if prior is not _MISSING:
                prior_seconds, prior_percent = prior
                prior = ("watching", media.season_number, media.episode_number,
                         prior_seconds*1000, prior_seconds/(prior_percent/100)*1000 if prior_percent else 0)
            candidates.setdefault(root.id, []).append(Observation(observation.source, value,
                observation.changed_at, prior, observation.observed_after))
    # A movie completion competes with an active resume in the same status
    # decision. Viewing history remains additive; only current status is ordered.
    for media_id, decision in state.watch_decisions.items():
        if decision.value is not True or decision.conflict or decision.stale or not decision.sources:
            continue
        media = await db.get(Media, media_id)
        if media and media.media_type.value in ("movie", "episode"):
            root_id = await _tracking_root(db, media_id)
            value = ("completed", None, None, None, None) if media.media_type.value == "movie" else (
                "watching", media.season_number, media.episode_number, None, None)
            for source in decision.sources:
                clock = state.watch_evidence.get(media_id, {}).get(source)
                completions.setdefault(root_id, []).append(Observation(source, value,
                    clock or decision.changed_at, inferred=clock is None and decision.changed_at is not None))
    for media_id, values in completions.items():
        # A series needs status reconciliation only when it has competing
        # resume evidence; additive episode history handles the other cases.
        if values[0].value[0] != "completed" and media_id not in candidates:
            continue
        candidates[media_id] = reconcile_completions(candidates.get(media_id, []),
            sorted(values, key=lambda item: (item.changed_at or datetime.min, item.value[1:3], item.source)))
    entries = {e.media_id: e for e in (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == state.user_id, TrackedEntry.media_id.in_(set(candidates))))).scalars()}
    for media_id, observations in candidates.items():
        observations.sort(key=lambda item: item.source)
        entry = entries.get(media_id)
        local = (entry.status, None, None, None, None) if entry else None
        local_at = status_changed_at(entry) if entry and media_id not in added else None
        if entry and entry.status == "watching":
            media = await db.get(Media, media_id)
            actual_ids = {media_id}
            if media and media.media_type.value == "series":
                show = (await db.execute(select(Show).where(Show.tmdb_id == media.tmdb_id))).scalar_one_or_none()
                if show:
                    actual_ids.update((await db.execute(select(Media.id).where(Media.show_id == show.id))).scalars())
            progress_row = (await db.execute(select(PlaybackProgress, Media).join(Media, Media.id == PlaybackProgress.media_id)
                .where(PlaybackProgress.user_id == state.user_id, PlaybackProgress.media_id.in_(actual_ids))
                .order_by(PlaybackProgress.updated_at.desc()).limit(1))).first()
            if progress_row:
                progress, actual = progress_row
                percent = progress.progress_percent
                percent = percent/100 if percent and percent > 1 else percent
                seconds = progress.progress_seconds or 0
                duration = seconds/percent*1000 if percent else None
                local = (entry.status, actual.season_number, actual.episode_number, seconds*1000, duration)
                if progress.updated_at and (local_at is None or naive_utc(progress.updated_at) > local_at):
                    local_at = naive_utc(progress.updated_at)
        # Baseline equality for the status decision is independent of the
        # provider-specific playback coordinate retained in its snapshot.
        normalized = [Observation(o.source, o.value, o.changed_at,
            local if o.previous is not _MISSING and local and o.previous[0] == local[0] else o.previous,
            o.observed_after) for o in observations]
        decision = resolve(local, local_at, normalized, started_at=state.started_at)
        if decision.conflict and local and local[0] == "watching" and all(value is None for value in local[1:]) and all(o.value[0] == "watching" for o in normalized):
            enriched = resolve(None, local_at, normalized, started_at=state.started_at)
            if not enriched.conflict:
                decision = enriched
        if state.protected_fields.get(media_id, set()) & {"status", "progress"}:
            decision = Decision(local, stale=True)
        state.playback[media_id] = decision
        if decision.sources and not decision.conflict:
            await _retire_review(db, state, media_id, "conflict")
        if decision.conflict:
            await _review(db, state, media_id, "playback", decision, observations)
    from models.tracking import StreamAction
    for media_id, decision in state.playback.items():
        if not decision.sources or decision.conflict or decision.stale:
            continue
        pending = (await db.execute(select(StreamAction).where(StreamAction.user_id == state.user_id,
            StreamAction.media_id == media_id, StreamAction.state == "pending",
            StreamAction.action.in_(("dismiss", "restore", "upsert"))))).scalars().all()
        for action in pending:
            queued_at = provider_changed_at({"updated_at": (action.payload or {}).get("observed_at")})
            if queued_at and decision.changed_at and queued_at > decision.changed_at:
                continue
            action.state, action.payload = "cancelled", {}
    await db.commit()
    for media_id, decision in state.playback.items():
        if not decision.sources or decision.conflict or decision.stale:
            continue
        if decision.value[0] == "completed" or decision.value[0] == "watching" and decision.value[3] is None:
            await _clear_completed_playback(db, state, media_id, decision)
            continue  # Completion has no resume position to persist.
        if decision.value[0] != "watching":
            continue
        connection_ids = [int(source.split(":")[1]) for source in decision.sources if source.startswith("connection:")]
        if not connection_ids:
            continue
        connection_id = min(connection_ids)
        record = records.get((media_id, connection_id))
        if record:
            await _apply_playback(db, state, media_id, record, decision.changed_at)
            conn = await db.get(MediaServerConnection, connection_id)
            if conn and conn.type == "arvio":
                entry = entries.get(media_id)
                if entry:
                    from core.status_provenance import mark_status_change
                    entry.status = "watching"
                    mark_status_change(entry, f"arvio:{conn.id}", decision.changed_at or state.started_at)
                if await approved_source(db, state.user_id, source_key(conn.type, conn.id)):
                    from core.stream_actions import queue_progress_update
                    await queue_progress_update(db, conn, await db.get(Media, media_id), record)
    await db.commit()
    # Only selected observations can mutate tracked state. All raw snapshots
    # still advance, including losing and pull-only providers.
    for snapshot in sorted(state.snapshots, key=lambda item: item["connection_id"]):
        conn = await db.get(MediaServerConnection, snapshot["connection_id"])
        if conn is None or conn.user_id != state.user_id or conn.identity_version != snapshot.get("identity_version", conn.identity_version):
            continue
        args = {key: value for key, value in snapshot.items() if key not in {"connection_id", "identity_version"}}
        removed = set()
        accepted = await observe_stream_snapshot(db, conn, **args, removed_watched_ids=removed)
        from core.pull_propagation import propagate_media_server_pull
        await propagate_media_server_pull(db, conn=conn, watched_ids=accepted, ratings={}, removed_watched_ids=removed)


async def _history(db, state, cycle, added):
    from core.cloud_history_reconciliation import reconcile_cloud_watch_events
    ids = set().union(*(item["new_media_ids"] for item in state.histories)) if state.histories else set()
    ids.update(media_id for media_id, decision in state.watch_decisions.items() if decision.sources)
    ids -= {media_id for media_id, decision in state.playback.items() if decision.conflict or decision.stale}
    ids -= {media_id for media_id, decision in state.watch_decisions.items()
            if decision.conflict or decision.stale or decision.value is False}
    ids = {media_id for media_id in ids if not (
        (decision := state.playback.get(await _tracking_root(db, media_id)))
        and (decision.conflict or decision.stale))}
    if not ids:
        return
    accepted = set()
    # History is one additive evidence set, not a sequence of provider status
    # decisions. The existing date/episode rules now see the complete set once.
    await reconcile_cloud_watch_events(db, user_id=state.user_id, provider="combined",
        new_media_ids=ids, applied_media_ids=accepted, newly_tracked_ids=added,
        initial_import_override=all(item.get("initial", False) for item in state.histories))
    from models.base import CollectionSource
    for media_id in accepted:
        evidence = state.watch_evidence.get(media_id, {})
        decision = state.watch_decisions.get(media_id)
        if not decision or not decision.sources:
            continue
        clocks = [clock for clock in evidence.values() if clock is not None]
        sources = {source for source, clock in evidence.items() if not clocks or clock == max(clocks)}
        if not any([await approved_source(db, state.user_id, source) for source in sources]):
            continue
        cycle.new_watched_ids.add(media_id)
        cycle.watched_exclusions[media_id] = (
            {int(source.split(":")[1]) for source in sources if source.startswith("connection:")},
            {CollectionSource(source) for source in sources if not source.startswith("connection:")})


async def _library(db, state, cycle):
    from sqlalchemy import select
    from models import Collection, CollectionFile, MediaServerConnection
    from models.tracking import StreamBaseline
    from models.streaming_library import StreamingLibraryIntent
    rows = (await db.execute(select(CollectionFile.connection_id, Collection.media_id)
        .join(Collection, Collection.id == CollectionFile.collection_id)
        .where(Collection.user_id == state.user_id, CollectionFile.connection_id.isnot(None)))).all()
    current = {(connection_id, media_id): True for connection_id, media_id in rows}
    changed = set(current) ^ set(state.library_before)
    grouped = {}
    for connection_id, media_id in changed:
        conn = await db.get(MediaServerConnection, connection_id)
        if not conn or conn.type not in ("stremio", "nuvio") or not conn.sync_collection or conn.identity_version != state.connection_versions.get(conn.id):
            continue
        baseline = await db.get(StreamBaseline, connection_id)
        # A removal is actionable only after a previous successful snapshot;
        # the adapter itself enforces completeness before removing source rows.
        desired = (connection_id, media_id) in current
        grouped.setdefault(media_id, []).append(Observation(source_key(conn.type, connection_id), desired, None,
            (connection_id, media_id) in state.library_before, state.library_observed.get(connection_id)))
    cycle.library_new_ids.clear()
    cycle.library_removed_ids.clear()
    cycle.library_source_ids_by_media.clear()
    for media_id, observations in grouped.items():
        observations.sort(key=lambda item: item.source)
        intent = (await db.execute(select(StreamingLibraryIntent).where(
            StreamingLibraryIntent.user_id == state.user_id, StreamingLibraryIntent.media_id == media_id))).scalar_one_or_none()
        local, clock = (intent.desired, intent.updated_at) if intent else state.library_local.get(media_id, (None, None))
        decision = resolve(local, clock, observations, started_at=state.started_at)
        if decision.conflict:
            await _review(db, state, media_id, "library", decision, observations)
            continue
        if not decision.sources:
            continue
        await _retire_review(db, state, media_id, "library_conflict")
        source_ids = {int(source.split(":")[1]) for source in decision.sources}
        approved = {connection_id for connection_id in source_ids
                    if (baseline := await db.get(StreamBaseline, connection_id)) and baseline.approved}
        if intent is None:
            intent = StreamingLibraryIntent(user_id=state.user_id, media_id=media_id)
            db.add(intent)
        intent.desired = decision.value
        # Absence has no mutation clock: this is an acceptance time only, not
        # evidence that this provider's edit is newer than another observation.
        intent.updated_at = state.started_at
        cycle.library_desired[media_id] = decision.value
        if approved:
            (cycle.library_new_ids if decision.value else cycle.library_removed_ids).add(media_id)
            cycle.library_source_ids_by_media[media_id] = source_ids
            cycle.library_observed_at_by_media[media_id] = state.started_at
    await db.commit()


async def collect_raw_progress(db, user_id, media_id, seconds, percent, clock):
    state = collecting(user_id)
    if not state:
        return False
    source = _source.get()
    from models.tracking import StreamBaseline
    baseline = await db.get(StreamBaseline, int(source.split(":")[1])) if source.startswith("connection:") else None
    saved = ((baseline.snapshot or {}).get("raw_progress_observations", {}) if baseline else {}).get(str(media_id))
    previous = tuple(saved) if saved else _MISSING
    state.raw_progress.setdefault(media_id, []).append(Observation(source, (seconds, percent), naive_utc(clock),
        previous, baseline.observed_at if baseline else None))
    return True


async def _raw_progress(db, state):
    from models.tracking import StreamBaseline
    for media_id, observations in state.raw_progress.items():
        for observation in observations:
            if not observation.source.startswith("connection:"):
                continue
            conn_id = int(observation.source.split(":")[1])
            baseline = await db.get(StreamBaseline, conn_id)
            if not baseline:
                baseline = StreamBaseline(connection_id=conn_id, user_id=state.user_id, approved=False)
                db.add(baseline)
                from models.tracking import SyncReview
                db.add(SyncReview(user_id=state.user_id, connection_id=conn_id, kind="initial_import",
                    message="Initial import completed. Confirm its summary before outbound synchronization."))
            snapshot = dict(baseline.snapshot or {})
            values = dict(snapshot.get("raw_progress_observations", {}))
            values[str(media_id)] = list(observation.value)
            baseline.snapshot = {**snapshot, "raw_progress_observations": values}
            baseline.observed_at = state.started_at
    await db.commit()


async def approved_source(db, user_id, source):
    state = replaying(user_id)
    if state and source in state.source_approvals:
        return state.source_approvals[source]
    from sqlalchemy import select
    from models.tracking import StreamBaseline, CloudBaseline
    baseline = await db.get(StreamBaseline, int(source.split(":")[1])) if source.startswith("connection:") else (
        await db.execute(select(CloudBaseline).where(CloudBaseline.user_id == user_id,
                                                   CloudBaseline.provider == source))).scalar_one_or_none()
    approved = bool(baseline and baseline.approved)
    if state:
        state.source_approvals[source] = approved
    return approved


async def _apply_playback(db, state, media_id, record, changed_at):
    from sqlalchemy import select, or_
    from models import Media, Show, PlaybackProgress
    from models.base import MediaType
    media = await db.get(Media, media_id)
    if media.media_type == MediaType.series:
        conditions = []
        if media.tmdb_id:
            conditions.append(Show.tmdb_id == media.tmdb_id)
        if media.tvdb_id:
            conditions.append(Show.tvdb_id == media.tvdb_id)
        show = (await db.execute(select(Show).where(or_(*conditions)))).scalar_one_or_none() if conditions else None
        media = (await db.execute(select(Media).where(Media.show_id == show.id,
            Media.media_type == MediaType.episode, Media.season_number == record.get("season"),
            Media.episode_number == record.get("episode")))).scalar_one_or_none() if show else None
    if media is None:
        return
    duration, position = float(record.get("duration") or 0), float(record.get("position") or 0)
    if duration <= 0 or not 0 < position < duration:
        return
    row = (await db.execute(select(PlaybackProgress).where(
        PlaybackProgress.user_id == state.user_id, PlaybackProgress.media_id == media.id))).scalar_one_or_none()
    if row and row.updated_at and row.updated_at > state.started_at:
        return
    if row is None:
        row = PlaybackProgress(user_id=state.user_id, media_id=media.id)
        db.add(row)
    row.progress_seconds = int(position / 1000)
    row.progress_percent = position / duration
    row.updated_at = changed_at or state.started_at


async def _clear_completed_playback(db, state, media_id, decision):
    from sqlalchemy import select, delete, or_, and_
    from models import PlaybackProgress, Media, Show
    media = await db.get(Media, media_id)
    if media.media_type.value == "series":
        season, episode = decision.value[1:3]
        if season is None or episode is None:
            return
        shows = select(Show.id).where(or_(Show.tmdb_id == media.tmdb_id if media.tmdb_id else False,
            Show.tvdb_id == media.tvdb_id if media.tvdb_id else False))
        ids = select(Media.id).where(Media.show_id.in_(shows), Media.season_number > 0,
            or_(Media.season_number < season, and_(Media.season_number == season, Media.episode_number <= episode)))
    else:
        ids = select(Media.id).where(Media.id == media_id)
    await db.execute(delete(PlaybackProgress).where(PlaybackProgress.user_id == state.user_id,
        PlaybackProgress.media_id.in_(ids), or_(PlaybackProgress.updated_at.is_(None), PlaybackProgress.updated_at <= state.started_at)))


def replaying(user_id):
    state = _current.get()
    return state if state and state.user_id == user_id and not state.collecting else None


def changed_local_fields(user_id, entry):
    state = replaying(user_id)
    return state.protected_fields.get(entry.media_id, set()) if state else set()


async def _watch_dates(db, state):
    """Merge reliable viewing dates independently of source completion order."""
    from models import WatchEvent
    from core.watch_dates import replace_inferred_watch_date
    from core.watch_dedup import get_dedup_window_minutes, load_existing_watch_times, is_duplicate_watch_time
    grouped = {}
    for source, media_id, event_id, previous_date, normalized in state.date_corrections:
        decision = state.watch_decisions.get(media_id)
        if decision and (decision.conflict or decision.stale or decision.value is False):
            continue
        grouped.setdefault((media_id, event_id, previous_date), set()).add(normalized)
    window = await get_dedup_window_minutes(db, state.user_id) if grouped else 0
    times = await load_existing_watch_times(db, state.user_id) if grouped else {}
    for (media_id, event_id, previous_date), dates in grouped.items():
        event = await db.get(WatchEvent, event_id)
        if not event or event.watched_at != previous_date or not (event.date_inferred or event.date_shared):
            continue
        replace_inferred_watch_date(event, max(dates))
        existing_times = times.setdefault(media_id, [])
        previous_time = previous_date or event.created_at
        if previous_time in existing_times:
            existing_times.remove(previous_time)
        existing_times.append(event.watched_at)
        await db.flush()
        for at in sorted(dates, reverse=True):
            if not is_duplicate_watch_time(times, media_id, at, window):
                db.add(WatchEvent(user_id=state.user_id, media_id=media_id, watched_at=at, completed=True))
                times.setdefault(media_id, []).append(at)
                await db.flush()


async def _tracking_root(db, media_id):
    state = _current.get()
    if state and media_id in state.tracking_roots:
        return state.tracking_roots[media_id]
    root_id = await _lookup_tracking_root(db, media_id)
    if state:
        state.tracking_roots[media_id] = root_id
    return root_id


async def _lookup_tracking_root(db, media_id):
    from sqlalchemy import select, or_
    from models import Media, Show
    media = await db.get(Media, media_id)
    if media is None or media.show_id is None:
        return media_id
    show = await db.get(Show, media.show_id)
    if not show:
        return media_id
    conditions = []
    if show.tmdb_id:
        conditions.append(Media.tmdb_id == show.tmdb_id)
    if show.tvdb_id:
        conditions.append(Media.tvdb_id == show.tvdb_id)
    root = (await db.execute(select(Media.id).where(Media.media_type == "series", or_(*conditions)))).scalar_one_or_none() if conditions else None
    return root or media_id


async def _watches(db, state, cycle):
    from sqlalchemy import select, delete
    from models import WatchEvent, MediaServerConnection
    from models.tracking import StreamBaseline
    from core.tracking_snapshot import watch_key, media_ids_for_watch_rows
    from core.status_provenance import provider_changed_at
    candidates = {}
    for media_id, sources in state.watch_evidence.items():
        candidates.setdefault(media_id, [])
        for source, clock in sources.items():
            saved = state.watch_previous.get(source, {})
            prior_stamp = saved.get(str(media_id))
            prior_clock = naive_utc(datetime.fromisoformat(prior_stamp)) if prior_stamp else None
            if str(media_id) not in saved or prior_clock != clock:
                candidates.setdefault(media_id, []).append(Observation(source, True, clock,
                    False if source in state.watch_previous and str(media_id) not in saved else _MISSING))
    for snapshot in state.snapshots:
        if not snapshot["sync_watched"]:
            continue
        conn = await db.get(MediaServerConnection, snapshot["connection_id"])
        if not conn or conn.user_id != state.user_id or conn.identity_version != snapshot.get("identity_version", conn.identity_version):
            continue
        baseline = await db.get(StreamBaseline, conn.id)
        previous = (baseline.snapshot or {}) if baseline else {}
        old = previous.get("records", {}).get("watched", [])
        old_keys = {(watch_key(row), str(row.get("watched_at"))) for row in old}
        old_watch_keys = {watch_key(row) for row in old}
        current = snapshot["watched"]
        current_keys = {watch_key(row) for row in current}
        mapping = {**previous.get("mappings", {}), **snapshot["tmdb_ids"]}
        ids = await media_ids_for_watch_rows(db, mapping, old+current)
        for row in current:
            key = watch_key(row)
            media_id = ids.get(key)
            if media_id is not None and (key, str(row.get("watched_at"))) not in old_keys:
                candidates.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id), True,
                    provider_changed_at(row), False if baseline and key not in old_watch_keys else _MISSING,
                    baseline.observed_at if baseline else None))
        if baseline and snapshot["complete"]:
            for row in old:
                key = watch_key(row)
                media_id = ids.get(key)
                if media_id is not None and key not in current_keys:
                    candidates.setdefault(media_id, []).append(Observation(source_key(conn.type, conn.id), False,
                        None, True, baseline.observed_at))
    from models.tracking import TrackedEntry
    from core.status_provenance import status_changed_at
    for media_id, observations in candidates.items():
        observations.sort(key=lambda observation: observation.source)
        root_id = await _tracking_root(db, media_id)
        entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == state.user_id,
            TrackedEntry.media_id == root_id))).scalar_one_or_none()
        local = True if media_id in state.watch_before else None
        clock = status_changed_at(entry) if entry else None
        decision = resolve(local, clock, observations, started_at=state.started_at)
        protected = state.protected_fields.get(root_id, set()) & {"status", "progress"}
        if protected:
            decision = Decision(local, stale=True)
        elif decision.stale and local is True and all(o.value is True for o in observations):
            # Older distinct plays enrich history without competing with the
            # owner's newer current status or becoming a fresh outbound edit.
            decision = Decision(True)
        state.watch_decisions[media_id] = decision
        if decision.conflict:
            await _review(db, state, media_id, "watched state", decision, observations)
            continue
        if decision.sources and not decision.conflict:
            await _retire_review(db, state, media_id, "watch_conflict")
        if decision.value is False and decision.sources:
            # Delete local viewing records only. Remote writes use watched-state
            # adapters; no file-system deletion is involved anywhere here.
            await db.execute(delete(WatchEvent).where(WatchEvent.user_id == state.user_id,
                                                     WatchEvent.media_id == media_id))
            ids = {int(source.split(":")[1]) for source in decision.sources if source.startswith("connection:")}
            cycle.removed_watch_exclusions[media_id] = ids
            if entry:
                from core.status_provenance import mark_status_change
                from models import Media
                media = await db.get(Media, media_id)
                if root_id != media_id and media.show_id:
                    remaining = len(set((await db.execute(select(WatchEvent.media_id).join(Media, Media.id == WatchEvent.media_id)
                        .where(WatchEvent.user_id == state.user_id, WatchEvent.completed.is_(True),
                               Media.show_id == media.show_id, Media.season_number > 0))).scalars()))
                    entry.progress = remaining
                    if entry.status == "completed":
                        entry.status = "watching" if remaining else "planning"
                        entry.finish_date = None
                    mark_status_change(entry, "combined", state.started_at)
                elif entry.status == "completed":
                    entry.status, entry.progress, entry.finish_date = "planning", 0, None
                    mark_status_change(entry, "combined", state.started_at)
    # Keep observations separate from accepted state, also for pull-only clouds.
    updates = {}
    for media_id, sources in state.watch_evidence.items():
        for source, clock in sources.items():
            updates.setdefault(source, {})[str(media_id)] = clock.isoformat() if clock else None
    await _persist_observations(db, state, "watch_observations", updates)
    await _apply_staged_watches(db, state)
    await db.commit()


async def _apply_staged_watches(db, state):
    from sqlalchemy import select, func
    from models import WatchEvent, User
    from core.watch_dedup import get_dedup_window_minutes, is_duplicate_watch_time
    from core.rewatch import record_rewatch_progress
    if not state.staged_watches and not state.watch_confirmations:
        return
    # The account row lock also blocks concurrent watch inserts through their
    # user FK. Deduplicate the batch in memory without per-title advisory locks.
    await db.execute(select(User.id).where(User.id == state.user_id).with_for_update())
    for source, media_id, event_id, previous_date, confirmed_date in state.watch_confirmations:
        decision = state.watch_decisions.get(media_id)
        root_id = await _tracking_root(db, media_id)
        event = await db.get(WatchEvent, event_id)
        if (event and event.provisional and event.watched_at == previous_date
                and decision and decision.value is True and not decision.conflict and not decision.stale
                and not state.protected_fields.get(root_id, set()) & {"status", "progress"}):
            event.watched_at, event.provisional = confirmed_date, False
    window = await get_dedup_window_minutes(db, state.user_id) if state.staged_watches else 0
    times, completed_ids = {}, set()
    media_ids = sorted({event.media_id for _, event in state.staged_watches})
    for offset in range(0, len(media_ids), 2000):
        rows = (await db.execute(select(WatchEvent.media_id, func.coalesce(WatchEvent.watched_at, WatchEvent.created_at),
            WatchEvent.completed).where(WatchEvent.user_id == state.user_id,
                WatchEvent.media_id.in_(media_ids[offset:offset+2000])))).all()
        for media_id, at, completed in rows:
            times.setdefault(media_id, []).append(at)
            if completed:
                completed_ids.add(media_id)
    accepted = []
    # Reliable dates precede estimates, irrespective of provider completion order.
    for source, event in sorted(state.staged_watches, key=lambda item: (
            bool(item[1].date_inferred), item[1].media_id, item[1].watched_at or state.started_at, item[0])):
        decision = state.watch_decisions.get(event.media_id)
        root_id = await _tracking_root(db, event.media_id)
        if state.protected_fields.get(root_id, set()) & {"status", "progress"}:
            continue
        if event.completed and (not decision or decision.conflict or decision.stale or decision.value is not True):
            continue
        if event.date_inferred and event.media_id in completed_ids:
            continue  # An undated flag cannot establish a separate rewatch.
        if is_duplicate_watch_time(times, event.media_id, event.watched_at, window):
            continue
        db.add(event)
        if event.completed:
            completed_ids.add(event.media_id)
            accepted.append(event)
        times.setdefault(event.media_id, []).append(event.watched_at or state.started_at)
    await db.flush()
    from models.rewatch import ShowRewatch
    if accepted and (await db.execute(select(ShowRewatch.id).where(ShowRewatch.user_id == state.user_id).limit(1))).first():
        for event in accepted:
            await record_rewatch_progress(db, state.user_id, event.media_id, event.id)


async def resolve_category_review(db, event, action):
    """Resolve a combined membership/position review using existing outboxes."""
    from sqlalchemy import select, delete
    from models import WatchEvent, PlaybackProgress, Media
    from core.watch_intents import queue_watch_intents
    payload = event.payload or {}
    category = payload.get("category")
    observations = payload.get("observations", [])
    if category == "playback":
        # The existing status resolver handles status/change actions; retain the
        # selected resume coordinate as well when a Watching proposal is accepted.
        if action == "confirm" and observations:
            value = observations[0]["value"]
            if value[0] == "watching":
                record = dict(season=value[1], episode=value[2], position=value[3], duration=value[4],
                              updated_at=datetime.utcnow())
                state = Reconciliation(event.user_id)
                await _apply_playback(db, state, event.media_id, record, state.started_at)
                from types import SimpleNamespace
                from core.stream_actions import queue_progress_update
                media = await db.get(Media, event.media_id)
                await queue_progress_update(db, SimpleNamespace(id=0, type="local", user_id=event.user_id), media, record)
        return False
    if category not in {"library", "watched state", "progress"}:
        return False
    if action not in {"confirm", "keep"}:
        from fastapi import HTTPException
        raise HTTPException(422, "Accept the proposed value, keep the current value, or edit the title")
    if not observations:
        from fastapi import HTTPException
        raise HTTPException(409, "This conflict has no observations; sync again")
    proposed = observations[0]["value"]
    if category == "library":
        from core.streaming_library import library_state, set_library_intent
        current = await library_state(db, event.user_id, event.media_id)
        await set_library_intent(db, event.user_id, event.media_id,
                                 bool(proposed) if action == "confirm" else current["desired"])
    elif category == "watched state":
        watched = bool((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == event.user_id,
            WatchEvent.media_id == event.media_id, WatchEvent.completed.is_(True)).limit(1))).first())
        if action == "confirm":
            watched = bool(proposed)
            if not watched:
                await db.execute(delete(WatchEvent).where(WatchEvent.user_id == event.user_id, WatchEvent.media_id == event.media_id))
            elif not (await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == event.user_id,
                    WatchEvent.media_id == event.media_id, WatchEvent.completed.is_(True)).limit(1))).first():
                db.add(WatchEvent(user_id=event.user_id, media_id=event.media_id, completed=True))
        from models.tracking import TrackedEntry
        from core.status_provenance import mark_status_change
        root_id = await _tracking_root(db, event.media_id)
        entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == event.user_id,
            TrackedEntry.media_id == root_id))).scalar_one_or_none()
        media = await db.get(Media, event.media_id)
        if action == "confirm" and entry and media.media_type.value == "movie":
            entry.status, entry.progress = ("completed", 1) if watched else ("planning", 0)
            entry.finish_date = datetime.utcnow().date() if watched else None
            mark_status_change(entry, "local")
        elif entry:
            if action == "confirm" and not watched and media.show_id:
                await db.flush()
                remaining = len(set((await db.execute(select(WatchEvent.media_id).join(Media, Media.id == WatchEvent.media_id)
                    .where(WatchEvent.user_id == event.user_id, WatchEvent.completed.is_(True),
                           Media.show_id == media.show_id, Media.season_number > 0))).scalars()))
                entry.progress = remaining
                if entry.status == "completed":
                    entry.status, entry.finish_date = ("watching" if remaining else "planning"), None
            mark_status_change(entry, "local")
        await db.flush()
        await queue_watch_intents(db, event.user_id, {event.media_id})
    elif action == "confirm":
        row = (await db.execute(select(PlaybackProgress).where(PlaybackProgress.user_id == event.user_id,
            PlaybackProgress.media_id == event.media_id))).scalar_one_or_none()
        if row is None:
            row = PlaybackProgress(user_id=event.user_id, media_id=event.media_id)
            db.add(row)
        row.progress_seconds, row.progress_percent = proposed
        row.updated_at = datetime.utcnow()
    return True


async def deliver_category_review(user_id, media_id, category):
    if category == "library":
        from core.streaming_library import deliver_library_intent
        await deliver_library_intent(user_id, media_id)
        return
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        if category == "watched state":
            from core.watch_intents import dispatch_watch_intents
            await dispatch_watch_intents(db, user_id)
            from core.watch_delivery import push_watch_state
            from models import WatchEvent
            from sqlalchemy import select
            watched = bool((await db.execute(select(WatchEvent.id).where(WatchEvent.user_id == user_id,
                WatchEvent.media_id == media_id, WatchEvent.completed.is_(True)).limit(1))).first())
            await push_watch_state(db, user_id, [media_id], watched=watched, skip_stream_watch_writes=True)
        else:
            from core.stream_actions import dispatch_stream_actions
            await dispatch_stream_actions(db, user_id)
