"""Platform-specific profile settings, separate from shared Nuvio watch state."""
import copy
import json


def content_key(value):
    return str(value).strip().split('|', 1)[0]


def next_up_seeds(records, *, aliases=None):
    """Collect actual episode seeds, optionally projecting one title's aliases."""
    seeds = {}
    for row in records:
        key = str(row.get('content_id') or '').strip()
        if not key or str(row.get('content_type') or '').lower() not in ('series', 'tv'):
            continue
        if aliases is not None and key not in aliases:
            continue
        coordinates = tuple(row.get(field) if row.get(field) is not None else -1
            for field in ('season', 'episode'))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in coordinates):
            raise ValueError('Invalid Next Up episode coordinates')
        for alias in aliases if aliases is not None else (key,):
            seeds.setdefault(alias, set()).add(coordinates)
    return seeds


def _string_set(value):
    if not isinstance(value, list) or any(not isinstance(key, str) for key in value):
        raise ValueError('Invalid Next Up dismissals')
    return set(value)


class TVSettings:
    platform = 'tv'
    version = 1

    @staticmethod
    def dismissals(blob):
        tracking = blob['features'].get('trakt_settings', {})
        if not isinstance(tracking, dict):
            raise ValueError('Invalid tracking settings')
        encoded = tracking.get('dismissed_next_up_keys', {'type': 'string_set', 'value': []})
        if not isinstance(encoded, dict) or encoded.get('type') != 'string_set':
            raise ValueError('Invalid Next Up dismissals')
        return _string_set(encoded.get('value'))

    @classmethod
    def merge(cls, original, hidden, visible, seeds):
        keys = cls.dismissals(original)
        # Normalize touched TV dismissals to title IDs, including legacy seeds.
        keys = {key for key in keys if content_key(key) not in hidden | visible}
        keys.update(hidden - visible)
        if keys == cls.dismissals(original):
            return original, keys
        blob = copy.deepcopy(original)
        tracking = blob['features'].setdefault('trakt_settings', {})
        encoded = tracking.get('dismissed_next_up_keys', {'type': 'string_set'})
        tracking['dismissed_next_up_keys'] = {**encoded, 'value': sorted(keys)}
        return blob, keys


class MobileSettings:
    platform = 'mobile'
    version = 4

    @staticmethod
    def payload(blob):
        encoded = blob['features'].get('continue_watching_settings_payload', '')
        if not isinstance(encoded, str):
            raise ValueError('Invalid Continue Watching payload')
        payload = json.loads(encoded) if encoded.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError('Invalid Continue Watching payload')
        return payload

    @classmethod
    def dismissals(cls, blob):
        return _string_set(cls.payload(blob).get('dismissedNextUpKeys', []))

    @classmethod
    def merge(cls, original, hidden, visible, seeds):
        payload = cls.payload(original)
        keys = cls.dismissals(original)
        keys = {key for key in keys if content_key(key) not in visible}
        for content_id in hidden - visible:
            keys.update(f'{content_id}|{season}|{episode}'
                for season, episode in seeds.get(content_id, ()))
        if keys == cls.dismissals(original):
            return original, keys
        payload['dismissedNextUpKeys'] = sorted(keys)
        blob = copy.deepcopy(original)
        blob['features']['continue_watching_settings_payload'] = json.dumps(
            payload, separators=(',', ':'), ensure_ascii=False)
        return blob, keys


SETTINGS_ADAPTERS = (TVSettings, MobileSettings)
