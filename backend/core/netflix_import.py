"""Parse Netflix viewing-history exports and prepare conservative TMDB matches.

This module deliberately has no database dependency.  It keeps the Netflix
format handling and metadata matching reusable so an import-session API can
persist the returned draft, let a person correct it, and only then apply it.
TMDB season/episode positions are returned as aired-order coordinates; a caller
merging an existing show must translate them through that show's configured
episode order before applying progress.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable

import httpx

from core.scrob_import import MAX_TOTAL_SIZE


_HEADER_ALIASES = {
    "title": {"title", "titel", "titre", "titulo", "titolo", "nombre", "nom"},
    "date": {"date", "datum", "fecha", "datum uhrzeit", "date watched", "watch date"},
}
_SEASON_WORD = r"(?:season|staffel|saison|temporada|stagione|saison)"
_SEASON_NUMBER = re.compile(rf"\b{_SEASON_WORD}\s*#?\s*(\d+)\b", re.IGNORECASE)
_NUMBER_SEASON = re.compile(rf"\b(\d+)\s*\b{_SEASON_WORD}\b", re.IGNORECASE)
_DIGITS = re.compile(r"\b(\d+)\b")
_ID_DIGEST_LENGTH = 18


@dataclass(slots=True)
class NetflixWatch:
    """One distinct source title/date observation after duplicate removal."""

    row: int
    source_title: str
    watched_at: str
    show_title: str | None = None
    season_label: str | None = None
    season_number: int | None = None
    episode_title: str | None = None
    source_rows: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ParsedNetflixHistory:
    rows: list[NetflixWatch] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int = 0
    duplicate_rows: int = 0
    excluded_rows: int = 0
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "errors": [dict(error) for error in self.errors],
            "total_rows": self.total_rows,
            "duplicate_rows": self.duplicate_rows,
            "excluded_rows": self.excluded_rows,
            "language": self.language,
        }


def _norm_text(value: str | None) -> str:
    """Case/diacritic/punctuation-insensitive key for titles and headers."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _header_key(value: str | None) -> str:
    return _norm_text(value)


def _parse_date(raw: str, language: str | None) -> date:
    value = raw.strip()
    if not value:
        raise ValueError("Missing viewing date")

    # ISO is unambiguous and is accepted regardless of the selected language.
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass

    lang = (language or "").lower().replace("_", "-")
    german = lang.startswith(("de", "fr", "es", "it", "nl", "da", "fi", "no", "sv", "pt"))
    # Dotted dates are day-first.  For slash dates we follow the export locale,
    # while dates with an unambiguous first component (>12) are day-first too.
    if "." in value:
        formats = ("%d.%m.%Y", "%d.%m.%y")
    elif "/" in value:
        pieces = value.split("/")
        if len(pieces) == 3 and pieces[0].isdigit() and int(pieces[0]) > 12:
            formats = ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y")
        elif german:
            formats = ("%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y", "%m/%d/%y")
        else:
            formats = ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y")
    else:
        formats = (
            "%d-%m-%Y", "%d-%m-%y", "%m-%d-%Y", "%m-%d-%y",
            "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
        )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid viewing date: {raw.strip()}")


def _split_netflix_title(title: str) -> dict[str, Any]:
    """Return possible show/episode parts without losing colon-bearing names.

    Netflix's standard rows look like ``Show: Season 2: Episode``.  Some rows
    use a season's custom name instead (``Show: Stranger Things 5: Chapter 1``)
    and some episode titles themselves contain colons.  Keeping the full
    trailing text as the episode title handles those cases; the full original
    title is always retained as a possible movie title as well.
    """
    parts = [part.strip() for part in title.split(":")]
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return {
            "show_title": None,
            "season_label": None,
            "season_number": None,
            "episode_title": None,
        }

    show_title = parts[0]
    if len(parts) == 2:
        tail = parts[1]
        season_number = _extract_season_number(tail, show_title)
        if season_number is not None and _SEASON_NUMBER.search(tail):
            return {
                "show_title": show_title,
                "season_label": tail,
                "season_number": season_number,
                "episode_title": None,
            }
        return {
            "show_title": show_title,
            "season_label": None,
            "season_number": None,
            "episode_title": tail,
        }

    season_label = parts[1]
    season_number = _extract_season_number(season_label, show_title)
    episode_title = ": ".join(parts[2:])
    return {
        "show_title": show_title,
        "season_label": season_label,
        "season_number": season_number,
        "episode_title": episode_title,
    }


def _extract_season_number(label: str | None, show_title: str | None = None) -> int | None:
    if not label:
        return None
    match = _SEASON_NUMBER.search(label) or _NUMBER_SEASON.search(label)
    if match:
        # The capture group differs by expression but is always the number.
        return int(match.group(1))
    # Netflix custom season labels often omit the word "Season" (e.g.
    # "Stranger Things 5"). Do not interpret any trailing number as a season:
    # labels such as "Chapter 5" can be episode or arc names. Accept a plain
    # number, a known season-like prefix, or the series title followed by a
    # number; other custom labels are resolved from season metadata.
    if label.strip().isdigit():
        return int(label.strip())
    match = re.search(r"\b(?:part|volume|vol|arc|cour|book|chapter)\s*#?\s*(\d+)\s*$", label, re.IGNORECASE)
    if match:
        return int(match.group(1))
    if show_title:
        prefix = re.escape(show_title.strip())
        match = re.search(rf"^{prefix}\s+(\d+)\s*$", label, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_netflix_csv(content: bytes | str, language: str | None = None) -> ParsedNetflixHistory:
    """Parse an English or localized Netflix viewing-history CSV.

    Duplicate title/date rows are collapsed, while distinct watch dates are
    preserved for repeat-viewing history. Recoverable row errors are returned
    with source line numbers. Structural/encoding errors raise ``ValueError``.
    """
    if isinstance(content, str):
        encoded_size = len(content.encode("utf-8"))
        text = content.lstrip("\ufeff")
    else:
        encoded_size = len(content)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("This doesn't look like a valid Netflix CSV (not UTF-8 text).") from exc
    if encoded_size > MAX_TOTAL_SIZE:
        raise ValueError("Netflix viewing-history CSV is too large to import.")

    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        columns = reader.fieldnames or []
    except csv.Error as exc:
        raise ValueError(f"This Netflix CSV is malformed and couldn't be read: {exc}") from exc

    normalized = {_header_key(column): column for column in columns if column}
    title_col = next((normalized[k] for k in _HEADER_ALIASES["title"] if k in normalized), None)
    date_col = next((normalized[k] for k in _HEADER_ALIASES["date"] if k in normalized), None)
    if title_col is None or date_col is None:
        raise ValueError("This doesn't look like a Netflix viewing-history export; expected Title and Date columns.")

    inferred_language = language
    if inferred_language is None:
        date_header = _header_key(date_col)
        title_header = _header_key(title_col)
        locale_by_header = {
            "titel": "de-DE", "datum": "de-DE",
            "titre": "fr-FR", "nom": "fr-FR",
            "titulo": "es-ES", "nombre": "es-ES", "fecha": "es-ES",
            "titolo": "it-IT",
        }
        inferred_language = locale_by_header.get(title_header) or locale_by_header.get(date_header)
    parsed = ParsedNetflixHistory(language=inferred_language)
    seen: dict[tuple[str, str], NetflixWatch] = {}
    try:
        for row_number, raw_row in enumerate(reader, start=2):
            raw_title = (raw_row.get(title_col) or "").strip()
            raw_date = (raw_row.get(date_col) or "").strip()
            if not raw_title and not raw_date:
                continue
            parsed.total_rows += 1
            if None in raw_row:
                parsed.errors.append({"row": row_number, "message": "Unexpected extra CSV field(s)"})
                continue
            if not raw_title:
                parsed.errors.append({"row": row_number, "message": "Missing title"})
                continue
            if re.fullmatch(r":\s*Episode\s+\d+", raw_title, re.IGNORECASE):
                parsed.excluded_rows += 1
                continue
            try:
                watched_at = _parse_date(raw_date, inferred_language).isoformat()
            except ValueError as exc:
                parsed.errors.append({"row": row_number, "message": str(exc)})
                continue

            key = (_norm_text(raw_title), watched_at)
            existing = seen.get(key)
            if existing is not None:
                existing.source_rows.append(row_number)
                parsed.duplicate_rows += 1
                continue

            title_parts = _split_netflix_title(raw_title)
            watch = NetflixWatch(
                row=row_number,
                source_title=raw_title,
                watched_at=watched_at,
                source_rows=[row_number],
                **title_parts,
            )
            seen[key] = watch
            parsed.rows.append(watch)
    except csv.Error as exc:
        raise ValueError(f"This Netflix CSV is malformed and couldn't be read: {exc}") from exc
    return parsed


def _stable_id(kind: str, normalized_title: str) -> str:
    digest = hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()[:_ID_DIGEST_LENGTH]
    return f"{kind}:{digest}"


def _result_list(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        rows = response.get("results") or response.get("data") or []
        return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
    return []


def _media_candidate(raw: dict[str, Any], media_type: str, query: str) -> dict[str, Any]:
    title = raw.get("title") or raw.get("name") or ""
    original_title = raw.get("original_title") or raw.get("original_name") or ""
    aliases = {_norm_text(value) for value in (title, original_title) if value}
    query_key = _norm_text(query)
    exact = query_key in aliases
    similarity = max((SequenceMatcher(None, query_key, alias).ratio() for alias in aliases), default=0.0)
    date_value = raw.get("release_date") or raw.get("first_air_date") or ""
    year = int(date_value[:4]) if isinstance(date_value, str) and date_value[:4].isdigit() else None
    return {
        "tmdb_id": raw.get("id"),
        "title": title,
        "year": year,
        "poster_path": raw.get("poster_path"),
        "media_type": media_type,
        "genre_ids": raw.get("genre_ids") or [],
        "original_language": raw.get("original_language"),
        "origin_country": raw.get("origin_country") or [],
        "_exact": exact,
        "_similarity": similarity,
        "_original_title": original_title,
        "_vote_count": max(0, int(raw.get("vote_count") or 0)),
        "_popularity": max(0.0, float(raw.get("popularity") or 0)),
        "_release_date": date_value,
    }


def is_anime_candidate(*records: dict[str, Any] | None) -> bool:
    """Recognize Japanese animation from TMDB search or detail metadata."""
    animated = False
    japanese = False
    for record in records:
        if not isinstance(record, dict):
            continue
        genre_ids = record.get("genre_ids") or []
        genres = record.get("genres") or []
        animated |= 16 in genre_ids or any(
            isinstance(genre, dict) and (genre.get("id") == 16 or str(genre.get("name", "")).casefold() == "animation")
            for genre in genres
        )
        japanese |= record.get("original_language") == "ja" or "JP" in (record.get("origin_country") or [])
    return animated and japanese


def _dedupe_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[Any, dict[str, Any]] = {}
    for row in rows:
        tmdb_id = row.get("tmdb_id")
        if tmdb_id is None:
            continue
        prior = by_id.get(tmdb_id)
        if prior is None or (row.get("_exact"), row.get("_similarity", 0)) > (
            prior.get("_exact"), prior.get("_similarity", 0)
        ):
            by_id[tmdb_id] = row
    return sorted(by_id.values(), key=lambda item: (not item.get("_exact"), -item.get("_similarity", 0), str(item.get("title", ""))))


def _dominant_exact_movie(candidates: list[dict[str, Any]], watched_dates: list[str]) -> dict[str, Any] | None:
    """Choose a same-title film only when audience evidence has one clear leader.

    Netflix does not export a release year. This deliberately leaves close
    remakes and obscure titles for review instead of trusting search rank.
    """
    first_watch = min(watched_dates, default="")
    eligible = [candidate for candidate in candidates if candidate.get("_exact") and (
        not candidate.get("_release_date") or not first_watch or candidate["_release_date"][:10] <= first_watch
    )]
    if len(eligible) < 2:
        return None
    ranked = sorted(eligible, key=lambda candidate: candidate.get("_vote_count", 0), reverse=True)
    leader, runner_up = ranked[:2]
    if not leader.get("_release_date"):
        return None
    votes = leader.get("_vote_count", 0)
    other_votes = sum(candidate.get("_vote_count", 0) for candidate in ranked[1:])
    if votes < 1000 or votes < 4 * max(1, other_votes):
        return None
    if leader.get("_popularity", 0) < 1.5 * max(1, runner_up.get("_popularity", 0)):
        return None
    return leader


def _visible_candidate(candidate: dict[str, Any], confidence: str, evidence: str) -> dict[str, Any]:
    return {
        "tmdb_id": candidate.get("tmdb_id"),
        "title": candidate.get("title"),
        "year": candidate.get("year"),
        "poster_path": candidate.get("poster_path"),
        "media_type": candidate.get("media_type"),
        "confidence": confidence,
        "evidence": evidence,
    }


def _season_number_from_metadata(season: dict[str, Any]) -> int | None:
    value = season.get("season_number")
    if value is None:
        value = season.get("season_number", season.get("number"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _episodes_from_season(data: dict[str, Any], season_number: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in data.get("episodes") or []:
        if not isinstance(raw, dict):
            continue
        try:
            ep_season = int(raw.get("season_number", raw.get("season_number")))
            ep_number = int(raw.get("episode_number", raw.get("episode_number")))
        except (TypeError, ValueError):
            # TMDB season endpoints sometimes omit season_number on children.
            # Their episode_number is still canonical within the requested
            # season, so use it when present.
            ep_season = season_number
            try:
                ep_number = int(raw.get("episode_number", raw.get("episode_number")))
            except (TypeError, ValueError):
                continue
        result.append({
            "season_number": ep_season,
            "episode_number": ep_number,
            "title": raw.get("name") or "",
            "alternate_titles": [value for value in [raw.get("original_name")] if value and value != raw.get("name")],
            "air_date": raw.get("air_date"),
            "tmdb_episode_id": raw.get("id"),
        })
    return result


def _episode_released_by(episode: dict[str, Any], watched_at: str) -> bool:
    raw_date = episode.get("air_date") or episode.get("release_date")
    if not raw_date:
        return True
    try:
        air_date = date.fromisoformat(str(raw_date)[:10])
        watched_date = date.fromisoformat(watched_at)
    except ValueError:
        # A malformed catalogue date must not make an otherwise exact title
        # impossible to review; the caller still receives the candidate.
        return True
    return air_date <= date.today() and air_date <= watched_date


def resolve_netflix_episodes(
    episodes: list[dict[str, Any]], catalog_episodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve unknown episode positions from exact anchors and CSV watch order.

    The input may contain one record per viewing or already-merged records from
    an older draft. Repeated source titles share a position but keep their dates.
    """
    catalog = {
        (int(ep["season_number"]), int(ep["episode_number"])): ep
        for ep in catalog_episodes
        if ep.get("season_number") and ep.get("episode_number")
    }
    positions = sorted(catalog)
    events: list[tuple[str, int, int]] = []
    for index, episode in enumerate(episodes):
        dates = sorted(set(episode.get("watched_dates") or episode.get("dates") or []))
        source_rows = episode.get("source_rows")
        row_numbers = source_rows if isinstance(source_rows, list) else episode.get("source_row_numbers") or []
        ordered_rows = sorted((int(row) for row in row_numbers), reverse=True)
        for date_index, watched_at in enumerate(dates or [""]):
            # Netflix's export is newest first. A larger CSV line is older
            # when several viewings share a calendar date.
            row_number = ordered_rows[min(date_index, len(ordered_rows) - 1)] if ordered_rows else 0
            events.append((watched_at, -row_number, index))
    events.sort()

    def position_of(index: int) -> tuple[int, int] | None:
        episode = episodes[index]
        if episode.get("matched") and episode.get("season_number") and episode.get("episode_number"):
            return int(episode["season_number"]), int(episode["episode_number"])
        return None

    # A season label that was resolved by an exact title is stronger than its
    # Netflix number (Netflix can split one aired season into several parts).
    label_seasons: dict[str, set[int]] = {}
    for episode in episodes:
        if episode.get("matched") and episode.get("season_number") and episode.get("season_label"):
            label_seasons.setdefault(_norm_text(episode["season_label"]), set()).add(int(episode["season_number"]))

    assignments: dict[tuple[str, str], tuple[int, int] | None] = {}
    unresolved_states: dict[tuple[str, str], str] = {}
    covered_by: dict[tuple[str, str], tuple[int, int]] = {}
    groups: list[list[int]] = []
    group: list[int] = []
    for event_index, (_date, _row, episode_index) in enumerate(events):
        if position_of(episode_index) is not None:
            groups.append(group)
            group = []
        else:
            group.append(event_index)
    groups.append(group)
    anchors = [position_of(index) for _date, _row, index in events if position_of(index) is not None]
    reserved_positions = {position for position in anchors if position is not None}

    for group_index, run in enumerate(groups):
        if not run:
            continue
        left = anchors[group_index - 1] if group_index else None
        right = anchors[group_index] if group_index < len(anchors) else None
        unique: list[tuple[tuple[str, str], int, str]] = []
        seen_in_run: set[tuple[str, str]] = set()
        for event_index in run:
            watched_at, _row, index = events[event_index]
            episode = episodes[index]
            key = (_norm_text(episode.get("season_label")), _norm_text(episode.get("source_episode_title") or episode.get("source_title") or episode.get("title")))
            if key not in seen_in_run and key not in assignments:
                unique.append((key, index, watched_at))
                seen_in_run.add(key)
        available = [
            position for position in positions
            if position not in reserved_positions and (left is None or position > left) and (right is None or position < right)
        ]
        if left is None and right is None and unique:
            first = episodes[unique[0][1]]
            label = _norm_text(first.get("season_label"))
            mapped = label_seasons.get(label, set())
            start_season = next(iter(mapped)) if len(mapped) == 1 else first.get("season_number") or 1
            available = [position for position in available if position[0] >= int(start_season)]
        if right is not None and left is None and unique:
            # Initial unknowns sit immediately before the first known anchor.
            available = available[-len(unique):]
        used: set[tuple[int, int]] = set()
        last_assigned = left
        for key, index, watched_at in unique:
            episode = episodes[index]
            label = _norm_text(episode.get("season_label"))
            mapped = label_seasons.get(label, set())
            hinted_season = next(iter(mapped)) if len(mapped) == 1 else episode.get("season_number")
            choices = [
                position for position in available
                if position not in used and (last_assigned is None or position > last_assigned)
                and (hinted_season is None or position[0] >= int(hinted_season))
                and _episode_released_by(catalog[position], watched_at)
            ]
            chosen = choices[0] if choices else None
            assignments[key] = chosen
            unresolved_states[key] = "guessed" if chosen is not None else "covered" if right is not None else "discarded"
            if chosen is None and right is not None:
                covered_by[key] = right
            if chosen is not None:
                used.add(chosen)
                reserved_positions.add(chosen)
                last_assigned = chosen

    for episode in episodes:
        if episode.get("matched"):
            episode.setdefault("resolution", "exact")
            continue
        key = (_norm_text(episode.get("season_label")), _norm_text(episode.get("source_episode_title") or episode.get("source_title") or episode.get("title")))
        position = assignments.get(key)
        if position is not None:
            catalog_episode = catalog[position]
            episode.update({
                "season_number": position[0], "episode_number": position[1],
                "title": catalog_episode.get("title"),
                "tmdb_episode_id": catalog_episode.get("tmdb_episode_id"),
                "air_date": catalog_episode.get("air_date") or catalog_episode.get("release_date"),
                "matched": True, "confidence": "medium", "resolution": "guessed",
                "reason": "Position inferred from CSV viewing order and nearby exact episode matches.",
            })
        else:
            # Bounded runs are already covered by a later exact episode for
            # cumulative progress. Unbounded rows without catalogue space are
            # discarded rather than assigned to a nonexistent episode.
            covered = unresolved_states.get(key) == "covered"
            episode["resolution"] = "covered" if covered else "discarded"
            if covered:
                episode["covered_by"] = list(covered_by[key])
            episode["matched"] = False
            if not episode.get("reason") or episode["reason"] == "No unique exact episode title match was found.":
                episode["reason"] = "Covered by confirmed progress." if covered else "No released catalogue position could be assigned."
    return episodes


def _candidate_seasons(
    show_details: dict[str, Any],
    observations: list[NetflixWatch],
) -> dict[int, set[int]]:
    """Plan season requests per input row while fetching a season at most once."""
    metadata = show_details.get("seasons") or []
    seasons: dict[int, dict[str, Any]] = {}
    for raw in metadata:
        if not isinstance(raw, dict):
            continue
        number = _season_number_from_metadata(raw)
        if number is not None:
            seasons[number] = raw

    plan: dict[int, set[int]] = {}
    ordered = sorted(seasons)
    for index, row in enumerate(observations):
        if row.season_number is not None:
            if row.season_number in seasons or not seasons:
                plan[index] = {row.season_number}
            else:
                # Streaming services sometimes split one TMDB aired season
                # into several labelled parts. Search existing seasons by
                # exact episode title, then require a unique match below.
                plan[index] = {number for number in ordered if number > 0}
            continue
        if row.season_label:
            label = _norm_text(row.season_label)
            named = [
                number for number, info in seasons.items()
                if label and (
                    label == _norm_text(str(info.get("name") or ""))
                    or label in _norm_text(str(info.get("name") or ""))
                    or _norm_text(str(info.get("name") or "")) in label
                )
            ]
            numeric = _extract_season_number(row.season_label, row.show_title)
            if numeric is not None and numeric in seasons:
                plan[index] = {numeric}
            elif len(named) == 1:
                plan[index] = {named[0]}
            else:
                # Unknown custom labels are checked against each catalogued
                # season. The per-show/season cache below shares those calls.
                plan[index] = {number for number in ordered if number >= 0}
        else:
            plan[index] = {number for number in ordered if number > 0}
    return plan


def _episode_title_keys(row: NetflixWatch) -> set[str]:
    keys = {_norm_text(row.episode_title)}
    if row.show_title and row.source_title.startswith(row.show_title):
        # An episode itself may contain a colon: "Chapter Four: The Body".
        # The parser cannot know whether its first part was a season label.
        keys.add(_norm_text(row.source_title[len(row.show_title):].lstrip(": ")))
    return keys - {""}


async def _await_call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    value = fn(*args, **kwargs)
    if isinstance(value, Awaitable):
        return await value
    return value


async def prepare_netflix_import(
    history: ParsedNetflixHistory | dict[str, Any] | bytes | str,
    *,
    api_key: str | None = None,
    language: str | None = None,
    search_movies_fn: Callable[..., Any] | None = None,
    search_shows_fn: Callable[..., Any] | None = None,
    show_details_fn: Callable[..., Any] | None = None,
    season_fn: Callable[..., Any] | None = None,
    max_concurrency: int = 4,
    progress_fn: Callable[[int, int, str], Any] | None = None,
) -> dict[str, Any]:
    """Prepare a reviewable, JSON-serializable matching draft.

    Calls TMDB's search and season endpoints by default. Injected callbacks
    make the matching path easy to exercise with deterministic fixtures. Each
    distinct title search and each `(show, season, language)` request is
    cached for this invocation; network concurrency is bounded by the
    semaphore. No rows are written to AnyList or the database.
    """
    if isinstance(history, (bytes, str)):
        history = parse_netflix_csv(history, language=language)
    elif isinstance(history, dict):
        raw_rows = history.get("rows") or []
        rows: list[NetflixWatch] = []
        for raw in raw_rows:
            if isinstance(raw, NetflixWatch):
                rows.append(raw)
            elif isinstance(raw, dict):
                rows.append(NetflixWatch(**{key: raw.get(key) for key in NetflixWatch.__dataclass_fields__ if key in raw}))
        history = ParsedNetflixHistory(
            rows=rows,
            errors=list(history.get("errors") or []),
            total_rows=int(history.get("total_rows") or len(rows)),
            duplicate_rows=int(history.get("duplicate_rows") or 0),
            excluded_rows=int(history.get("excluded_rows") or 0),
            language=history.get("language"),
        )

    from core import tmdb

    search_movies_fn = search_movies_fn or tmdb.search_movies
    search_shows_fn = search_shows_fn or tmdb.search_shows
    show_details_fn = show_details_fn or tmdb.get_show_light
    season_fn = season_fn or tmdb.get_season
    semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
    requested_language = language or history.language or "en-US"
    search_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    search_tasks: dict[tuple[str, str], asyncio.Task[list[dict[str, Any]]]] = {}
    details_cache: dict[int, dict[str, Any]] = {}
    details_tasks: dict[int, asyncio.Task[dict[str, Any]]] = {}
    season_cache: dict[tuple[int, int, str], dict[str, Any]] = {}
    season_tasks: dict[tuple[int, int, str], asyncio.Task[dict[str, Any]]] = {}
    progress_lock = asyncio.Lock()
    search_progress = 0
    search_total = 0
    evaluation_progress = 0
    evaluation_total = 0

    async def report_progress(current: int, total: int, message: str) -> None:
        if progress_fn is not None:
            await _await_call(progress_fn, current, total, message)

    async def search(kind: str, query: str) -> list[dict[str, Any]]:
        key = (kind, _norm_text(query))
        if key not in search_tasks:
            search_tasks[key] = asyncio.create_task(_search_uncached(kind, query, key))
        return await search_tasks[key]

    async def _search_uncached(kind: str, query: str, key: tuple[str, str]) -> list[dict[str, Any]]:
        nonlocal search_progress
        if key not in search_cache:
            fn = search_movies_fn if kind == "movie" else search_shows_fn
            async with semaphore:
                raw = await _await_call(fn, query, api_key=api_key, language=requested_language)
            candidates = [_media_candidate(result, kind, query) for result in _result_list(raw)]
            search_cache[key] = _dedupe_candidates(candidates)
            async with progress_lock:
                search_progress += 1
                await report_progress(search_progress, search_total, f"Searching distinct {kind} titles")
        return search_cache[key]

    async def show_details(tmdb_id: int) -> dict[str, Any]:
        if tmdb_id not in details_tasks:
            details_tasks[tmdb_id] = asyncio.create_task(_show_details_uncached(tmdb_id))
        return await details_tasks[tmdb_id]

    async def _show_details_uncached(tmdb_id: int) -> dict[str, Any]:
        if tmdb_id not in details_cache:
            async with semaphore:
                raw = await _await_call(show_details_fn, tmdb_id, api_key=api_key, language=requested_language)
            details_cache[tmdb_id] = raw if isinstance(raw, dict) else {}
        return details_cache[tmdb_id]

    async def season_data(tmdb_id: int, season_number: int, lang: str | None = None) -> dict[str, Any]:
        lang_key = lang or requested_language
        key = (tmdb_id, season_number, lang_key)
        if key not in season_tasks:
            season_tasks[key] = asyncio.create_task(_season_uncached(tmdb_id, season_number, lang_key, key))
        return await season_tasks[key]

    async def _season_uncached(
        tmdb_id: int,
        season_number: int,
        lang_key: str,
        key: tuple[int, int, str],
    ) -> dict[str, Any]:
        if key not in season_cache:
            try:
                async with semaphore:
                    raw = await _await_call(season_fn, tmdb_id, season_number, api_key=api_key, language=lang_key)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                # Netflix's season labels do not always match TMDB's aired
                # order. A missing season is evidence for review, not a
                # reason to discard the rest of the viewing history.
                raw = {"_netflix_season_missing": True, "episodes": []}
            season_cache[key] = raw if isinstance(raw, dict) else {}
        return season_cache[key]

    # Search both the first title segment and a possible colon-bearing show
    # title. TMDB identity resolves the split: "Star Wars: The Clone Wars"
    # must not be grouped under an unrelated show named "Star Wars".
    rows = [replace(row) for row in history.rows]
    show_queries: dict[str, str] = {}
    movie_queries: set[str] = set()
    possible_long_titles: dict[int, str] = {}
    for index, row in enumerate(rows):
        if row.show_title:
            show_queries.setdefault(_norm_text(row.show_title), row.show_title)
            parts = [part.strip() for part in row.source_title.split(":") if part.strip()]
            if len(parts) >= 3 and _extract_season_number(parts[1], parts[0]) is None:
                long_title = ": ".join(parts[:2])
                possible_long_titles[index] = long_title
                show_queries.setdefault(_norm_text(long_title), long_title)
            if row.season_number is None and not row.season_label:
                movie_queries.add(row.source_title)
        else:
            if not row.source_title.lstrip().startswith(":"):
                movie_queries.add(row.source_title)
    show_tasks = [asyncio.create_task(search("show", query)) for query in show_queries.values()]
    movie_tasks = {query: asyncio.create_task(search("movie", query)) for query in movie_queries}
    search_total = len(show_queries) + len(movie_queries)
    if show_tasks or movie_tasks:
        await asyncio.gather(*show_tasks, *movie_tasks.values(), return_exceptions=False)

    for index, long_title in possible_long_titles.items():
        if not any(candidate.get("_exact") for candidate in search_cache.get(("show", _norm_text(long_title)), [])):
            continue
        row = rows[index]
        parts = [part.strip() for part in row.source_title.split(":") if part.strip()]
        tail = parts[2:]
        row.show_title = long_title
        row.season_label = tail[0] if len(tail) > 1 else None
        row.season_number = _extract_season_number(row.season_label, long_title)
        row.episode_title = ": ".join(tail[1:]) if len(tail) > 1 else tail[0]

    # Group by the resolved TV title, retaining viewing dates and unique
    # episode observations. Plain titles are searched as movies.
    show_rows: dict[str, list[NetflixWatch]] = {}
    movie_rows: dict[str, list[NetflixWatch]] = {}
    for row in rows:
        if row.show_title and (row.episode_title or row.season_label):
            show_rows.setdefault(_norm_text(row.show_title), []).append(row)
        else:
            movie_rows.setdefault(_norm_text(row.source_title), []).append(row)

    def _planned_candidate_count(candidate_rows: list[dict[str, Any]]) -> int:
        exact = sum(1 for candidate in candidate_rows if candidate.get("_exact"))
        if exact:
            return exact
        plausible = sum(1 for candidate in candidate_rows if candidate.get("_similarity", 0) >= 0.60)
        return min(3, plausible or len(candidate_rows))

    evaluation_total = sum(
        _planned_candidate_count(search_cache.get(("show", key), []))
        for key in show_rows
    )

    shows: list[dict[str, Any]] = []
    unresolved_show_titles: set[str] = set()
    for normalized_show, observations in show_rows.items():
        source_show = observations[0].show_title or observations[0].source_title
        candidates = search_cache.get(("show", normalized_show), [])
        if not candidates:
            unresolved_show_titles.add(normalized_show)
            continue

        # Work through exact titles first. Similar titles are suggestions only.
        exact_candidates = [candidate for candidate in candidates if candidate.get("_exact")]
        ordered_candidates = exact_candidates or [candidate for candidate in candidates if candidate.get("_similarity", 0) >= 0.60][:3] or candidates[:3]
        evaluated: list[dict[str, Any]] = []

        async def evaluate_show(candidate: dict[str, Any]) -> dict[str, Any]:
            nonlocal evaluation_progress
            tmdb_id = int(candidate["tmdb_id"])
            details = await show_details(tmdb_id)
            season_plan = _candidate_seasons(details, observations)
            required_seasons = sorted(set().union(*season_plan.values())) if season_plan else []
            highest_observed_season = max((number for number in required_seasons if number > 0), default=0)
            if highest_observed_season:
                # Partial progress is cumulative. Fetch prior seasons too so
                # the API can preview filling the gap from season 1 to the
                # latest matched episode without making per-episode requests.
                regular_seasons = {
                    number for raw in (details.get("seasons") or [])
                    if isinstance(raw, dict)
                    for number in [_season_number_from_metadata(raw)]
                    if number is not None and 0 < number <= highest_observed_season
                }
                required_seasons = sorted(set(required_seasons) | regular_seasons)
            season_results = await asyncio.gather(*(season_data(tmdb_id, number) for number in required_seasons))
            missing_seasons = {
                number for number, result in zip(required_seasons, season_results)
                if result.get("_netflix_season_missing")
            }
            available_seasons = set(required_seasons) - missing_seasons
            for possible in season_plan.values():
                if possible and possible <= missing_seasons:
                    possible.update(number for number in available_seasons if number > 0)
            episodes_by_season: dict[int, list[dict[str, Any]]] = {}
            totals: dict[int, int] = {}
            catalogue_complete: dict[int, bool] = {}
            last_aired = details.get("last_episode_to_air") or {}
            try:
                last_aired_position = (
                    int(last_aired["season_number"]), int(last_aired["episode_number"])
                ) if last_aired.get("season_number") is not None and last_aired.get("episode_number") is not None else None
            except (KeyError, TypeError, ValueError):
                last_aired_position = None
            # Details include counts for seasons that had no watched episode
            # in the export. Keep those seasons in the progress table with
            # zero represented episodes so absence is visible to review.
            for metadata_season in details.get("seasons") or []:
                if not isinstance(metadata_season, dict):
                    continue
                number = _season_number_from_metadata(metadata_season)
                if number is None or number <= 0:
                    continue
                if last_aired_position and number > last_aired_position[0]:
                    continue
                premiere = metadata_season.get("air_date")
                if premiere:
                    try:
                        if date.fromisoformat(str(premiere)[:10]) > date.today():
                            continue
                    except ValueError:
                        pass
                try:
                    episode_count = max(0, int(metadata_season.get("episode_count") or 0))
                except (TypeError, ValueError):
                    episode_count = 0
                if last_aired_position and number == last_aired_position[0]:
                    episode_count = min(episode_count, last_aired_position[1]) if episode_count else last_aired_position[1]
                if episode_count <= 0:
                    continue
                totals[number] = episode_count
                # A season endpoint is needed before completion can be inferred.
                catalogue_complete[number] = False
            for season_number, raw_season in zip(required_seasons, season_results):
                eps = _episodes_from_season(raw_season, season_number)
                episodes_by_season[season_number] = eps
                try:
                    fetched_count = int(raw_season.get("episode_count") or len(eps))
                except (TypeError, ValueError):
                    fetched_count = len(eps)
                declared = max(totals.get(season_number, 0), fetched_count)
                today_text = date.today().isoformat()
                if any(episode.get("air_date") for episode in eps):
                    known_released = [
                        episode for episode in eps
                        if episode.get("air_date") and str(episode.get("air_date"))[:10] <= today_text
                    ]
                elif last_aired_position:
                    known_released = [
                        episode for episode in eps
                        if (episode.get("season_number"), episode.get("episode_number")) <= last_aired_position
                    ]
                else:
                    # No episode dates or latest-aired position means TMDB
                    # cannot prove whether these entries have aired yet.
                    known_released = []
                if eps:
                    totals[season_number] = len(known_released)
                catalogue_complete[season_number] = bool(eps) and declared == len(eps) and all(episode.get("air_date") for episode in eps)

            # A localized season response is usually enough. For any observed
            # title that did not match there, try English once per affected
            # season and keep it as an alias on the same canonical episode.
            if not requested_language.lower().startswith("en"):
                fallback_seasons: set[int] = set()
                for index, observation in enumerate(observations):
                    episode_keys = _episode_title_keys(observation)
                    possible = season_plan.get(index, set())
                    has_localized_match = any(
                        episode_keys & {
                            _norm_text(episode.get("title")),
                            *(_norm_text(title) for title in episode.get("alternate_titles") or []),
                        }
                        and _episode_released_by(episode, observation.watched_at)
                        for season_number in possible
                        for episode in episodes_by_season.get(season_number, [])
                    )
                    if not has_localized_match:
                        fallback_seasons.update(possible)
                if fallback_seasons:
                    fallback_seasons -= missing_seasons
                if fallback_seasons:
                    english_rows = await asyncio.gather(*(
                        season_data(tmdb_id, season_number, "en-US")
                        for season_number in sorted(fallback_seasons)
                    ))
                    for season_number, raw_english in zip(sorted(fallback_seasons), english_rows):
                        for english_episode in _episodes_from_season(raw_english, season_number):
                            same_position = next((
                                episode for episode in episodes_by_season.get(season_number, [])
                                if episode.get("episode_number") == english_episode.get("episode_number")
                                and episode.get("season_number") == english_episode.get("season_number")
                            ), None)
                            if same_position is None:
                                episodes_by_season.setdefault(season_number, []).append(english_episode)
                            else:
                                names = set(same_position.get("alternate_titles") or [])
                                english_title = english_episode.get("title")
                                if english_title and english_title != same_position.get("title"):
                                    names.add(english_title)
                                same_position["alternate_titles"] = sorted(names)

            episode_matches: list[dict[str, Any]] = []
            match_count = 0
            for index, observation in enumerate(observations):
                possible = season_plan.get(index, set())
                episode_keys = _episode_title_keys(observation)
                matches = [
                    episode
                    for season_number in possible
                    for episode in episodes_by_season.get(season_number, [])
                    if episode_keys & {
                        _norm_text(episode.get("title")),
                        *(_norm_text(title) for title in episode.get("alternate_titles") or []),
                    }
                    and _episode_released_by(episode, observation.watched_at)
                ]
                # Same title can recur (e.g. "Pilot"). Season metadata removes
                # ambiguity whenever Netflix included a season label.
                matched = len(matches) == 1
                if matched:
                    match_count += 1
                    episode = matches[0]
                    episode_matches.append({
                        **episode,
                        "source_title": observation.source_title,
                        "source_episode_title": observation.episode_title,
                        "season_label": observation.season_label,
                        "watched_dates": [observation.watched_at],
                        "source_rows": list(observation.source_rows),
                        "matched": True,
                        "confidence": "high",
                        "evidence": "exact normalized episode title",
                        "reason": "Exact episode title and season evidence matched uniquely.",
                    })
                else:
                    missing_for_row = sorted(possible & missing_seasons)
                    reason = (
                        f"TMDB has no season {', '.join(map(str, missing_for_row))} for this suggested show. Choose another show or skip this episode."
                        if missing_for_row else "No unique exact episode title match was found."
                    )
                    episode_matches.append({
                        "season_number": observation.season_number,
                        "episode_number": None,
                        "title": observation.episode_title,
                        "source_title": observation.source_title,
                        "source_episode_title": observation.episode_title,
                        "season_label": observation.season_label,
                        "watched_dates": [observation.watched_at],
                        "source_rows": list(observation.source_rows),
                        "matched": False,
                        "confidence": "low",
                        "evidence": "no unique exact episode title match",
                        "reason": reason,
                    })

            # Merge repeated episode title observations and their dates. The
            # parser only collapses duplicate title/date rows, so this stage
            # must count each episode once but keep all distinct dates.
            merged_episodes: dict[tuple[Any, ...], dict[str, Any]] = {}
            for episode in episode_matches:
                identity = (
                    episode.get("season_number"), episode.get("episode_number"),
                    ("matched" if episode.get("matched") else _norm_text(episode.get("title"))),
                    None if episode.get("matched") else episode.get("source_title"),
                )
                existing = merged_episodes.get(identity)
                if existing is None:
                    merged_episodes[identity] = episode
                else:
                    existing["watched_dates"] = sorted(set(existing["watched_dates"]) | set(episode["watched_dates"]))
                    existing["source_rows"] = sorted(set(existing["source_rows"]) | set(episode["source_rows"]))
                    existing["matched"] = bool(existing["matched"] and episode["matched"])
                    source_titles = set(existing.get("source_titles") or [existing.get("source_title")])
                    source_titles.add(episode.get("source_title"))
                    existing["source_titles"] = sorted(title for title in source_titles if title)
                    source_episode_titles = set(existing.get("source_episode_titles") or [existing.get("source_episode_title")])
                    source_episode_titles.add(episode.get("source_episode_title"))
                    existing["source_episode_titles"] = sorted(title for title in source_episode_titles if title)
            final_episodes = list(merged_episodes.values())

            # Repeated rows that point to one canonical episode produce one
            # episode record, even when Netflix has multiple watch dates.
            for episode in final_episodes:
                if episode.get("matched"):
                    all_matches = [
                        ep for eps in episodes_by_season.values() for ep in eps
                        if ep["season_number"] == episode["season_number"] and ep["episode_number"] == episode["episode_number"]
                    ]
                    if len(all_matches) != 1:
                        episode["matched"] = False
                        episode["evidence"] = "episode position is ambiguous in the catalogue"
                        episode["confidence"] = "low"
                        episode["reason"] = "The episode title maps to more than one catalogue position."

            represented: dict[int, int] = {}
            for episode in final_episodes:
                if episode.get("matched"):
                    number = int(episode["season_number"])
                    represented[number] = represented.get(number, 0) + 1
            seasons = [
                {
                    "season_number": season_number,
                    "represented": represented.get(season_number, 0),
                    "total_released": totals.get(season_number),
                    "catalogue_complete": catalogue_complete.get(season_number, False),
                }
                for season_number in sorted(set(totals) | set(represented) | missing_seasons)
                if season_number > 0
            ]
            catalog_episodes = []
            for season_number in sorted(episodes_by_season):
                if season_number <= 0:
                    continue
                for episode in episodes_by_season[season_number]:
                    release_date = episode.get("air_date")
                    if release_date:
                        try:
                            if date.fromisoformat(str(release_date)[:10]) > date.today():
                                continue
                        except ValueError:
                            if not last_aired_position or (season_number, episode.get("episode_number")) > last_aired_position:
                                continue
                    elif not last_aired_position or (season_number, episode.get("episode_number")) > last_aired_position:
                        continue
                    try:
                        if release_date:
                            date.fromisoformat(str(release_date)[:10])
                    except ValueError:
                        if not last_aired_position:
                            continue
                    catalog_episodes.append({
                        "season_number": episode.get("season_number"),
                        "episode_number": episode.get("episode_number"),
                        "title": episode.get("title"),
                        "release_date": release_date,
                        "tmdb_episode_id": episode.get("tmdb_episode_id"),
                    })
            catalog_episodes.sort(key=lambda episode: (episode["season_number"], episode["episode_number"]))
            resolve_netflix_episodes(final_episodes, catalog_episodes)
            represented = {}
            for episode in final_episodes:
                if episode.get("matched") and episode.get("season_number") and episode.get("episode_number"):
                    season_number = int(episode["season_number"])
                    represented.setdefault(season_number, set()).add(int(episode["episode_number"]))
            for season in seasons:
                season["represented"] = len(represented.get(season["season_number"], set()))
            # Guesses advance progress but cannot establish which TMDB show a
            # source title names. Rank competing show identities on exact
            # episode evidence only.
            unique_matched = sum(1 for episode in final_episodes if episode.get("resolution") == "exact")
            full_exact = candidate.get("_exact")
            every_observation_matched = bool(observations) and all(
                any(
                    episode.get("matched") and row.watched_at in episode.get("watched_dates", [])
                    and _episode_title_keys(row) & {
                        _norm_text(episode.get("title")),
                        *(_norm_text(title) for title in episode.get("alternate_titles") or []),
                    }
                    for episode in final_episodes
                )
                for row in observations
            )
            confidence = "high" if full_exact and every_observation_matched else "medium" if match_count else "low"
            reason = (
                "Exact show title and every episode matched uniquely."
                if confidence == "high" else
                "Show candidate matched; remaining episode positions are inferred where possible."
                if match_count else
                "Suggested show title; episode evidence did not produce an exact unique match."
            )
            try:
                tvdb_id = (details.get("external_ids") or {}).get("tvdb_id")
            except AttributeError:
                tvdb_id = None
            result = {
                "candidate": candidate,
                "episodes": final_episodes,
                "seasons": seasons,
                "catalog_episodes": catalog_episodes,
                "confidence": confidence,
                "reason": reason,
                "match_count": unique_matched,
                "observed_count": len(observations),
                "details": details,
                "tvdb_id": tvdb_id,
            }
            async with progress_lock:
                evaluation_progress += 1
                await report_progress(evaluation_progress, evaluation_total, f"Matching episodes for {source_show}")
            return result

        evaluated = await asyncio.gather(*(evaluate_show(candidate) for candidate in ordered_candidates))
        evaluated.sort(key=lambda result: (
            result["confidence"] == "high",
            result["match_count"],
            result["candidate"].get("_exact", False),
            result["candidate"].get("_similarity", 0.0),
        ), reverse=True)
        best = evaluated[0]
        # Even a high-confidence candidate is automatic only when its show
        # identity is unambiguous or its episode evidence uniquely distinguishes
        # it from every other exact-title candidate.
        same_title = [result for result in evaluated if result["candidate"].get("_exact")]
        competing_high = [result for result in same_title if result["confidence"] == "high"]
        if len(competing_high) > 1:
            best["confidence"] = "medium"
            best["reason"] = "Multiple shows with this title have matching episode names; choose the correct show."
        elif len(same_title) > 1 and best["confidence"] == "high" and not best["match_count"]:
            best["confidence"] = "medium"
            best["reason"] = "More than one show has this title; choose the correct show."
        elif len(same_title) == 1 and best["candidate"].get("_exact") and best["match_count"] and best["confidence"] != "high":
            # The show identity is established even if some episode positions
            # had to be inferred or discarded.
            best["confidence"] = "high"
            best["reason"] = "Exact show title and matching episode evidence; other positions were resolved automatically."

        candidate = best["candidate"]
        output = {
            "id": _stable_id("show", normalized_show),
            "kind": "show",
            "source_title": source_show,
            "dates": sorted({row.watched_at for row in observations}),
            "source_rows": sum(len(row.source_rows) for row in observations),
            "tmdb_id": candidate.get("tmdb_id"),
            "title": candidate.get("title"),
            "year": candidate.get("year"),
            "poster_path": candidate.get("poster_path"),
            "media_type": "show",
            "is_anime": is_anime_candidate(candidate, best.get("details")),
            "confidence": best["confidence"],
            "reason": best["reason"],
            "status": "matched" if best["confidence"] == "high" else "review",
            "episode_order": "tmdb:aired",
            "requires_existing_order_validation": True,
            "tvdb_id": best.get("tvdb_id"),
            "episodes": best["episodes"],
            "seasons": best["seasons"],
                "catalog_episodes": best["catalog_episodes"],
            "suggestions": [
                _visible_candidate(
                    result["candidate"], result["confidence"], result["reason"],
                )
                for result in evaluated[:5]
            ],
        }
        shows.append(output)

    movies: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    # Colon-form rows that did not make a confidently identified show are
    # considered for whole-title movie search, but cannot become a movie if
    # Netflix explicitly supplied a season marker.
    for normalized_title, observations in movie_rows.items():
        source_title = observations[0].source_title
        if source_title.lstrip().startswith(":"):
            # Some Netflix exports contain anonymous ": Episode N" rows.
            # A film search produces unrelated namesakes; keep the rows
            # available for manual show/episode remapping instead.
            unmatched.append({
                "id": _stable_id("show", normalized_title),
                "kind": "show",
                "source_title": source_title,
                "dates": sorted({row.watched_at for row in observations}),
                "source_rows": sum(len(row.source_rows) for row in observations),
                "tmdb_id": None,
                "title": None,
                "media_type": None,
                "confidence": "low",
                "status": "review",
                "reason": "Netflix omitted this episode's show title. Choose the show and episode manually, or skip it.",
                "episodes": [{
                    "season_number": None,
                    "episode_number": None,
                    "title": row.source_title.lstrip(": ").strip(),
                    "source_title": row.source_title,
                    "source_episode_title": row.source_title.lstrip(": ").strip(),
                    "watched_dates": [row.watched_at],
                    "source_rows": list(row.source_rows),
                    "matched": False,
                    "confidence": "low",
                    "reason": "The show title is missing from this Netflix row.",
                } for row in observations],
                "seasons": [],
                "suggestions": [],
            })
            continue
        candidates = search_cache.get(("movie", normalized_title), [])
        exact = [candidate for candidate in candidates if candidate.get("_exact")]
        dominant = _dominant_exact_movie(exact, [row.watched_at for row in observations])
        candidate = exact[0] if len(exact) == 1 else dominant or (candidates[0] if candidates else None)
        confidence = "high" if candidate and (len(exact) == 1 or dominant is not None) else "medium" if candidate and candidate.get("_similarity", 0) >= 0.60 else "low"
        reason = (
            "Exact movie title match." if len(exact) == 1 else
            "Exact title with a clearly dominant TMDB audience match." if dominant else
            "Possible movie match; confirm the title." if candidate else "No matching movie was found."
        )
        item = {
            "id": _stable_id("movie", normalized_title),
            "kind": "movie",
            "source_title": source_title,
            "dates": sorted({row.watched_at for row in observations}),
            "source_rows": sum(len(row.source_rows) for row in observations),
            "tmdb_id": candidate.get("tmdb_id") if candidate else None,
            "title": candidate.get("title") if candidate else None,
            "year": candidate.get("year") if candidate else None,
            "poster_path": candidate.get("poster_path") if candidate else None,
            "media_type": "movie" if candidate else None,
            "is_anime": is_anime_candidate(candidate),
            "confidence": confidence,
            "reason": reason,
            "status": "matched" if confidence == "high" else "review",
            "suggestions": [
                _visible_candidate(row, "high" if row.get("_exact") else "medium", "Movie title search candidate")
                for row in candidates[:5]
            ],
        }
        (movies if candidate else unmatched).append(item)

    # Weak colon-shaped rows that have no unique episode evidence could also
    # be movies (e.g. a film with a colon in its title). Promote only an exact
    # unique movie title, and only if the show matcher did not get exact episode
    # evidence. The source observation is left intact for the review queue.
    promoted_source_rows: set[int] = set()
    for normalized_show, observations in show_rows.items():
        if any(row.season_label for row in observations):
            continue
        show_item = next((item for item in shows if item["id"] == _stable_id("show", normalized_show)), None)
        if show_item and any(ep.get("resolution") == "exact" for ep in show_item.get("episodes", [])):
            continue
        for observation in observations:
            query_key = _norm_text(observation.source_title)
            candidates = search_cache.get(("movie", query_key), [])
            exact = [candidate for candidate in candidates if candidate.get("_exact")]
            dominant = _dominant_exact_movie(exact, [observation.watched_at])
            if len(exact) == 1 or dominant:
                candidate = exact[0] if len(exact) == 1 else dominant
                item = {
                    "id": _stable_id("movie", query_key),
                    "kind": "movie",
                    "source_title": observation.source_title,
                    "dates": [observation.watched_at],
                    "source_rows": len(observation.source_rows),
                    "tmdb_id": candidate.get("tmdb_id"),
                    "title": candidate.get("title"),
                    "year": candidate.get("year"),
                    "poster_path": candidate.get("poster_path"),
                    "media_type": "movie",
                    "is_anime": is_anime_candidate(candidate),
                    "confidence": "high",
                    "reason": "Exact movie title match; the possible episode candidate did not match.",
                    "status": "matched",
                    "suggestions": [_visible_candidate(candidate, "high", "Exact movie title match")],
                }
                movies.append(item)
                promoted_source_rows.update(observation.source_rows)
            elif show_item:
                show_item["confidence"] = "medium" if show_item["tmdb_id"] else "low"
                show_item["status"] = "review"
                show_item["reason"] = "The colon-form entry could be a movie or an episode; confirm the correct match."
                show_item.setdefault("unresolved_source_rows", []).extend(observation.source_rows)

    def _group_is_fully_promoted(observations: list[NetflixWatch]) -> bool:
        source_row_ids = {source_row for row in observations for source_row in row.source_rows}
        return bool(source_row_ids) and source_row_ids.issubset(promoted_source_rows)

    shows = [item for item in shows if not _group_is_fully_promoted(show_rows.get(_norm_text(item["source_title"]), []))]

    # Any title that looks like a show but yielded no TMDB search results still
    # appears as a review item; it must not disappear from the summary.
    for normalized_show in sorted(unresolved_show_titles):
        observations = show_rows[normalized_show]
        if _group_is_fully_promoted(observations):
            continue
        source_title = observations[0].show_title or observations[0].source_title
        unmatched.append({
            "id": _stable_id("show", normalized_show),
            "kind": "show",
            "source_title": source_title,
            "dates": sorted({row.watched_at for row in observations}),
            "source_rows": sum(len(row.source_rows) for row in observations),
            "tmdb_id": None,
            "title": None,
            "year": None,
            "poster_path": None,
            "media_type": None,
            "confidence": "low",
            "reason": "No matching show was found.",
            "status": "review",
            "episodes": [
                {
                    "season_number": row.season_number,
                    "episode_number": None,
                    "title": row.episode_title,
                    "source_title": row.source_title,
                    "source_episode_title": row.episode_title,
                    "season_label": row.season_label,
                    "watched_dates": [row.watched_at],
                    "source_rows": list(row.source_rows),
                    "matched": False,
                    "confidence": "low",
                    "evidence": "No show metadata candidate was found.",
                    "reason": "No show metadata candidate was found.",
                }
                for row in observations
            ],
            "seasons": [],
            "suggestions": [],
        })

    # If a colon-form show suggestion had no episode evidence and no exact
    # whole-film match, its TV item remains the single review entry.
    known_ids = {item["id"] for item in [*movies, *shows, *unmatched]}
    for normalized_show, observations in show_rows.items():
        if _stable_id("show", normalized_show) not in known_ids:
            if _group_is_fully_promoted(observations):
                continue
            source_title = observations[0].show_title or observations[0].source_title
            unmatched.append({
                "id": _stable_id("show", normalized_show),
                "kind": "show",
                "source_title": source_title,
                "dates": sorted({row.watched_at for row in observations}),
                "source_rows": sum(len(row.source_rows) for row in observations),
                "tmdb_id": None,
                "title": None,
                "year": None,
                "poster_path": None,
                "media_type": None,
                "confidence": "low",
                "reason": "Could not identify the source entry as a show or movie.",
                "status": "review",
                "episodes": [],
                "seasons": [],
                "suggestions": [],
            })

    # Merge any duplicate movie records created from both the ordinary movie
    # path and the colon-title fallback, keeping each viewing date once.
    merged_movies: dict[str, dict[str, Any]] = {}
    for item in movies:
        current = merged_movies.get(item["id"])
        if current is None:
            merged_movies[item["id"]] = item
        else:
            current["dates"] = sorted(set(current["dates"]) | set(item["dates"]))
            current["source_rows"] += item["source_rows"]
    movies = list(merged_movies.values())

    return {
        "movies": movies,
        "shows": shows,
        "unmatched": unmatched,
        "errors": list(history.errors),
        "counts": {
            "source_rows": history.total_rows,
            "unique_rows": len(history.rows),
            "duplicate_rows": history.duplicate_rows,
            "excluded_rows": history.excluded_rows,
            "movies": len(movies),
            "shows": len(shows),
            "unmatched": len(unmatched),
            "episodes": sum(len(item.get("episodes") or []) for item in shows),
        },
    }


__all__ = ["NetflixWatch", "ParsedNetflixHistory", "parse_netflix_csv", "prepare_netflix_import"]
