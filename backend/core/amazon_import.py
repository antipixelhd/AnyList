"""Amazon archive adapter. Only meaningful, non-promotional playback is evidence.

Quality-duration counters and seek endpoints are deliberately not completion
signals. Ten minutes is an admission threshold; the shared review determines
actual watched progress. Summary and session records never become duplicate watches.
"""
from __future__ import annotations
import csv
import io
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zipfile import ZipFile, BadZipFile
from core.archive_reader import BoundedZipReader
from core.netflix_import import _norm_text
from core.streaming_history import WatchObservation, ParsedHistory, MIN_WATCH_SECONDS

MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_ROWS = 100_000


def _text(value):
    return (value or "").strip().strip('"').strip()


def _seconds(value):
    try:
        number = float(_text(value))
        return number if math.isfinite(number) and number >= 0 else 0
    except (ValueError, TypeError):
        return 0


def _date(value):
    parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Missing UTC timezone")
    return parsed.astimezone(timezone.utc).date().isoformat()


def _identity(title, episode_titles=()):
    # Episode-show suffixes contain hyphens themselves; anchor the season end.
    match = re.match(r"^(.*)-(.+?)[, \u2013\ufffd-]+(?:season|staffel)\s*(\d+)(?:\s*\[.*\])?$", title, re.I)
    if match:
        episode, show, season = match.groups()
        # Watch Events supplies the standalone episode name. Use that boundary
        # when either the episode or the series name contains hyphens.
        combined = title[:match.start(3)]
        known = [position for position, char in enumerate(title)
                 if char == "-" and title[:position].casefold() in episode_titles]
        if len(known) == 1:
            boundary = known[0]
            suffix = re.sub(r"[, \u2013\ufffd-]+(?:season|staffel)\s*$", "", combined[boundary + 1:], flags=re.I)
            return _text(title[:boundary]), _text(suffix), int(season)
        return _text(episode), _text(show), int(season)
    parts = title.split("-")
    if len(parts) % 2 == 0:
        middle = len(parts) // 2
        left, right = "-".join(parts[:middle]), "-".join(parts[middle:])
        if _norm_text(left) == _norm_text(right):
            return left.strip(), None, None
    return title, None, None


def parse_amazon_zip(content: bytes, language: str | None = None) -> ParsedHistory:
    parsed = ParsedHistory(language=language)
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 2000:
                raise ValueError("The Amazon archive contains too many files.")
            reader = BoundedZipReader(archive, max_entry_size=MAX_CSV_BYTES,
                                      max_total_size=2 * MAX_CSV_BYTES, chunk_size=65536)
            tables = {}
            for filename, required in (("Viewing History.csv", {"Title", "Seconds Viewed", "Playback Start Datetime (UTC)"}),
                                       ("Watch Events.csv", {"Title Name", "Seconds Watched", "Most Recent Watch Date"})):
                matches = [n for n in archive.namelist() if n.replace("\\", "/").split("/")[-1] == filename]
                if len(matches) > 1:
                    raise ValueError(f"Multiple {filename} files; upload one Prime Video export.")
                if not matches:
                    continue
                table = csv.DictReader(io.StringIO(reader.read(matches[0]).decode("utf-8-sig")), strict=True)
                if not required <= set(table.fieldnames or []):
                    raise ValueError(f"Unrecognized Amazon {filename} columns.")
                rows = []
                for row in table:
                    if len(rows) >= MAX_ROWS:
                        raise ValueError("Too many playback rows in the Amazon export.")
                    rows.append(row)
                tables[filename] = rows
    except (BadZipFile, UnicodeDecodeError, csv.Error, RuntimeError) as exc:
        raise ValueError("This is not a readable Prime Video export ZIP.") from exc
    sessions = tables.get("Viewing History.csv")
    if sessions is None:
        raise ValueError("The ZIP must contain Prime Video Viewing History.csv.")
    if not parsed.language:
        locales = Counter(_text(row.get("Preferred Language Code")).lower() for row in sessions
                          if re.fullmatch(r"[a-zA-Z]{2}(?:-[a-zA-Z]{2})?", _text(row.get("Preferred Language Code"))))
        parsed.language = locales.most_common(1)[0][0] if locales else None
    # Summary records can corroborate meaningful viewing but only when they map
    # to exactly one detailed identity. Never guess between same-name episodes.
    episode_titles = {_text(row.get("Title Name")).casefold() for row in tables.get("Watch Events.csv", []) if _text(row.get("Title Name"))}
    identities = defaultdict(set)
    eligible_titles = set()
    for row in sessions:
        title = _text(row.get("Title"))
        episode, show, season = _identity(title, episode_titles)
        identities[_norm_text(episode)].add(title)
        if (_text(row.get("Material Type Description")).lower() in {"full", "feature"}
            and _text(row.get("Is Deleted")).lower() != "yes"):
            eligible_titles.add(title)
    observations = {}
    def add(title, watched_date, row_number, watched_seconds, completed_evidence=False):
        key = (title, watched_date)
        if key in observations:
            observations[key].completed_evidence |= completed_evidence
            observations[key].watched_seconds = max(observations[key].watched_seconds or 0, watched_seconds)
            parsed.duplicate_rows += 1
            return
        episode, show, season = _identity(title, episode_titles)
        observations[key] = WatchObservation(row=row_number, source_title=title if show else episode,
            watched_at=watched_date, show_title=show, season_number=season,
            season_label=f"Season {season}" if season else None,
            episode_title=episode if show else None, source_rows=[row_number], watched_seconds=watched_seconds, completed_evidence=completed_evidence)
    def promotional(title):
        return bool(re.search(r"\b(trailer|teaser|preview|behind the scenes)\b", title, re.I))
    for index, row in enumerate(sessions, 2):
        parsed.total_rows += 1
        title = _text(row.get("Title"))
        if (not title or promotional(title) or _text(row.get("Is Deleted")).lower() == "yes"
            or _text(row.get("Material Type Description")).lower() not in {"feature", "full"}
            or _seconds(row.get("Seconds Viewed")) < MIN_WATCH_SECONDS):
            parsed.excluded_rows += 1
            continue
        try:
            add(title, _date(row.get("Playback Start Datetime (UTC)")), index, _seconds(row.get("Seconds Viewed")))
        except (ValueError, TypeError):
            parsed.errors.append({"row": index, "message": "Invalid playback date"})
    for index, row in enumerate(tables.get("Watch Events.csv", []), len(sessions) + 2):
        parsed.total_rows += 1
        title = _text(row.get("Title Name"))
        matches = identities.get(_norm_text(title), set())
        if (len(matches) != 1 or promotional(title)
            or _text(row.get("Deleted from Watch History")).lower() == "yes"
            or _seconds(row.get("Seconds Watched")) < MIN_WATCH_SECONDS):
            parsed.excluded_rows += 1
            continue
        resolved = next(iter(matches))
        # A summary cannot override a detailed classification as promotional.
        if resolved not in eligible_titles:
            parsed.excluded_rows += 1
            continue
        try:
            add(resolved, _date(row.get("Most Recent Watch Date")), index, _seconds(row.get("Seconds Watched")),
                _text(row.get("Latest Watch Progress")).casefold() in {"completed", "complete", "finished", "100%"})
        except (ValueError, TypeError):
            parsed.errors.append({"row": index, "message": "Invalid viewing date"})
    parsed.rows = sorted(observations.values(), key=lambda r: r.watched_at, reverse=True)
    return parsed
