"""Episode availability from calendar dates and an optional TVDB schedule."""

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from functools import cache
from importlib.resources import files
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_

from core.countries import country_code


WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
# TVDB uses US Eastern for the US and the capital or largest city elsewhere.
# For other countries with multiple zones, use date-only if the zone is unclear.
TVDB_COUNTRY_ZONES = {
    'AU': 'Australia/Sydney', 'BR': 'America/Sao_Paulo', 'CA': 'America/Toronto',
    'CL': 'America/Santiago', 'CN': 'Asia/Shanghai', 'ES': 'Europe/Madrid',
    'ID': 'Asia/Jakarta', 'MX': 'America/Mexico_City', 'NZ': 'Pacific/Auckland',
    'PT': 'Europe/Lisbon', 'RU': 'Europe/Moscow', 'US': 'America/New_York',
}


@cache
def single_country_zones():
    zones = defaultdict(list)
    for line in files('tzdata.zoneinfo').joinpath('zone.tab').read_text(encoding='utf-8').splitlines():
        if line and not line.startswith('#'):
            country, _, zone, *_ = line.split('\t')
            zones[country].append(zone)
    return {country: values[0] for country, values in zones.items() if len(values) == 1}


def tvdb_schedule(tvdb_id, raw):
    value = raw.get('airsTime')
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?', value):
        return None
    country = raw.get('originalCountry') or raw.get('country')
    code = country_code(country) if isinstance(country, str) else None
    zone = TVDB_COUNTRY_ZONES.get(code) or single_country_zones().get(code)
    if not zone:
        return None
    days = raw.get('airsDays')
    days = [index for index, day in enumerate(WEEKDAYS)
            if isinstance(days, dict) and days.get(day) is True]
    network = raw.get('originalNetwork')
    if (isinstance(network, dict) and network.get('name') in ('Amazon Prime Video', 'Prime Video')
            and code == 'US' and value[:5] in ('19:00', '20:00')):
        # TVDB keeps Prime's official UTC episode date and shows its Eastern clock time.
        zone, value, days = 'UTC', '00:00', []
    return {'tvdb_id': tvdb_id, 'time': value, 'zone': zone, 'weekdays': days}


@cache
def schedule_time(value, zone):
    try:
        return time.fromisoformat(value), ZoneInfo(zone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None


class EpisodeRelease:
    def __init__(self, schedule=None, *, today=None, now=None):
        self.now = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        self.today = today or date.today()
        self.today_iso = self.today.isoformat()
        self.first_timed_day = (self.now.date() - timedelta(days=1)).isoformat()
        self.last_timed_day = (self.now.date() + timedelta(days=1)).isoformat()
        schedule = schedule or {}
        self.clock = schedule_time(schedule.get('time'), schedule.get('zone')) if schedule else None
        self.weekdays = schedule.get('weekdays') or ()

    def air_at(self, air_date):
        if not self.clock or not isinstance(air_date, str) or len(air_date) != 10:
            return None
        try:
            day = date.fromisoformat(air_date)
            if self.weekdays and day.weekday() not in self.weekdays:
                return None
            wall_time, zone = self.clock
            local = datetime.combine(day, wall_time, zone)
            if local.utcoffset() == local.replace(fold=1).utcoffset():
                return local.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            return None
        return None

    def is_released(self, air_date, imported=False):
        if imported:
            return True
        if not air_date:
            return False
        if self.clock and self.first_timed_day <= air_date <= self.last_timed_day:
            aired = self.air_at(air_date)
            if aired is not None:
                return aired <= self.now
        return air_date[:10] <= self.today_iso

    def sql_clause(self, air_date, imported):
        # An IANA offset can only put an episode on the adjacent UTC calendar day.
        # Adjust the existing SQL date comparison for just those boundary dates.
        baseline = air_date <= self.today_iso
        if not self.clock:
            return or_(baseline, imported)
        utc_day = self.now.date()
        early, late = [], []
        for offset in (-1, 0, 1):
            day = utc_day + timedelta(days=offset)
            key = day.isoformat()
            released = self.is_released(key)
            if released != (day <= self.today):
                (early if released else late).append(key)
        return or_(baseline & air_date.not_in(late) if late else baseline,
                   air_date.in_(early) if early else False, imported)
