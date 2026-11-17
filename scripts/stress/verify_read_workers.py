"""Exercise real API reader failure, permissions and edits in the private lab."""
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import asyncpg
import httpx
from fixture import check_environment


async def verify(state):
    check_environment()
    fixture = json.loads((state/'fixture.json').read_text())
    actor, other = fixture['users'][-2:]
    headers = {'Authorization': f'Bearer {actor["token"]}'}
    base = f'http://127.0.0.1:{os.environ["BACKEND_PORT"]}'
    db = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg:', 'postgresql:', 1))
    try:
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=15) as client:
            metrics = (await client.get('/__stress/metrics')).json()
            readers = metrics['read_workers']
            assert readers
            work = fixture['games'][0]
            path = f'/tracking/games/works/{work}'
            before = await client.get(path, headers=headers)
            before.raise_for_status()
            entry = before.json()
            response = await client.patch(f'/tracking/games/entries/{work}', headers=headers, json={
                'status': 'playing', 'score': 8, 'notes': 'Private reader recovery fixture',
                'revision': entry.get('revision'),
            })
            response.raise_for_status()
            own = await client.get(path, headers=headers)
            own.raise_for_status()
            assert 'Private reader recovery fixture' in own.text
            foreign = await client.get(path, headers={'Authorization': f'Bearer {other["token"]}'})
            foreign.raise_for_status()
            assert 'Private reader recovery fixture' not in foreign.text
            assert (await client.get(path)).status_code in (401, 403)
            forbidden = await client.patch(readers[0]['origin']+f'/tracking/games/entries/{work}',
                                            headers=headers, json={'score': 1})
            assert forbidden.status_code == 405
            marker = await db.fetchval("""
                INSERT INTO sync_jobs (user_id, source, status, job_type)
                SELECT id, 'manual', 'running', 'reader-probe' FROM users WHERE username=$1 RETURNING id
            """, actor['username'])
            os.kill(readers[0]['pid'], signal.SIGKILL)
            previous = {reader['pid'] for reader in readers}
            async with asyncio.timeout(40):
                while True:
                    metrics = (await client.get('/__stress/metrics')).json()
                    current = {reader['pid'] for reader in metrics['read_workers']}
                    if current != previous:
                        break
                    response = await client.get(path, headers=headers)
                    response.raise_for_status()
                    await asyncio.sleep(.25)
            assert await db.fetchval('SELECT status FROM sync_jobs WHERE id=$1', marker) == 'running'
            response = await client.get(path, headers=headers)
            response.raise_for_status()
            assert 'Private reader recovery fixture' in response.text
            await db.execute("UPDATE sync_jobs SET status='completed' WHERE id=$1", marker)
            return {'reader_count': len(readers), 'reader_replaced_after_kill': True,
                    'active_sync_preserved': True, 'edit_visible_after_restart': True,
                    'other_viewer_private_notes_excluded': True, 'reader_rejected_write': True}
    finally:
        await db.close()


if __name__ == '__main__':
    print(json.dumps(asyncio.run(verify(Path(sys.argv[1])))))
