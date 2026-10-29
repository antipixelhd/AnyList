"""Bounded public release selections survive process restarts; no viewer state."""
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

FRESH_SECONDS = 4 * 3600
STALE_SECONDS = 6 * 3600
MAX_BYTES = 2_000_000
MAX_RECORDS = 64


class ReleaseSnapshots:
    def __init__(self, directory):
        self.directory = directory

    def path(self, identity):
        digest = hashlib.sha256(repr(identity).encode()).hexdigest()
        return Path(self.directory()) / f'v1-{digest}.json'

    async def read(self, identity):
        def load():
            try:
                path = self.path(identity)
                if path.stat().st_size > MAX_BYTES:
                    return None
                saved = json.loads(path.read_text(encoding='utf-8'))
                age = time.time() - saved['fetched_at']
                result = saved['result']
                if not 0 <= age < STALE_SECONDS or not isinstance(result.get('rows'), list) or not isinstance(result.get('partial'), bool) or result.get('incomplete'):
                    return None
                if not all(isinstance(row, dict) and row.get('release_event') and row.get('release_date') for row in result['rows']):
                    return None
                return result, age
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None
        return await asyncio.to_thread(load)

    async def write(self, identity, result):
        def save():
            temporary = None
            try:
                encoded = json.dumps({'fetched_at': time.time(), 'result': result}, separators=(',', ':'))
                if len(encoded.encode()) > MAX_BYTES:
                    return
                path = self.path(identity)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
                temporary.write_text(encoded, encoding='utf-8')
                temporary.replace(path)
                for old in sorted(path.parent.glob('v1-*.json'), key=lambda entry: entry.stat().st_mtime, reverse=True)[MAX_RECORDS:]:
                    old.unlink(missing_ok=True)
            except OSError:
                pass  # Optional disk cache must not turn a usable selection into an error.
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
        await asyncio.to_thread(save)
