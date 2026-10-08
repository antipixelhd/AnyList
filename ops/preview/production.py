#!/usr/bin/python3
"""Root-owned, fixed-target production transaction; never accepts Docker options."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path('/opt/anylist')
STATE = Path('/var/lib/anylist-production')
IMAGE = 'ghcr.io/antipixelhd/anylist'
COMPOSE = ['docker', 'compose', '--project-directory', str(ROOT), '--env-file',
           str(ROOT / '.env.production'), '-f', str(ROOT / 'compose.yaml'),
           '-f', str(ROOT / 'compose.override.yaml')]


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return run(args, stdout=subprocess.PIPE, text=True).stdout.strip()


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(value)
    os.chmod(temporary, 0o600)
    with temporary.open('rb') as handle:
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(str(path.parent), os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def select(image):
    atomic(ROOT / 'compose.override.yaml', 'services:\n  app:\n    image: ' + image + '\n')


def healthy(container, timeout=180):
    deadline = time.monotonic() + timeout
    stable_since = None
    while time.monotonic() < deadline:
        status = json.loads(output(['docker', 'inspect', container]))[0]['State']
        if status['Status'] in ('exited', 'dead') or status.get('Health', {}).get('Status') == 'unhealthy':
            raise RuntimeError('AnyList container failed its health check')
        ready = status.get('Health', {}).get('Status') == 'healthy'
        if ready:
            ready = subprocess.run(['docker', 'exec', container, 'curl', '-fsS', '--max-time',
                                    '5', '-o', '/dev/null', 'http://127.0.0.1:7330/'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if ready:
            stable_since = stable_since or time.monotonic()
            if time.monotonic() - stable_since >= 15:
                return
        else:
            stable_since = None
        time.sleep(3)
    raise RuntimeError('AnyList health check timed out')


def database():
    # Database values stay inside the existing container, never enter CI output.
    return output(COMPOSE + ['ps', '-q', 'database'])


def start(image):
    select(image)
    run(COMPOSE + ['up', '-d', '--no-deps', '--pull', 'never', '--force-recreate', 'app'])
    healthy(output(COMPOSE + ['ps', '-q', 'app']))


def cleanup():
    (STATE / 'transaction.json').unlink(missing_ok=True)
    (STATE / 'database.dump').unlink(missing_ok=True)
    subprocess.run(['docker', 'image', 'rm', 'anylist-rollback:previous'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def discard(image):
    info = json.loads(output(['docker', 'image', 'inspect', image]))[0]
    tags = [tag for tag in info.get('RepoTags', []) if tag.startswith(IMAGE + ':')]
    if tags:
        subprocess.run(['docker', 'image', 'rm', *tags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['docker', 'image', 'rm', image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def recover(transaction):
    print('Restoring previous AnyList image and database', flush=True)
    run(COMPOSE + ['stop', 'app'])
    if transaction['restore_database']:
        with (STATE / 'database.dump').open('rb') as backup:
            run(['docker', 'exec', '-i', database(), 'sh', '-ec',
                 'case "$POSTGRES_DB" in postgres|template0|template1|"") exit 1;; esac; '
                 'dropdb --force --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB"; '
                 'createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"; '
                 'exec pg_restore --exit-on-error --no-owner '
                 '-U "$POSTGRES_USER" -d "$POSTGRES_DB"'], stdin=backup)
    start(transaction['previous'])
    cleanup()
    print('Rollback healthy', flush=True)


def deploy(sha):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Expected a main commit SHA')
    pending = STATE / 'transaction.json'
    if pending.exists():
        recover(json.loads(pending.read_text()))
    head = output(['git', 'ls-remote', 'https://github.com/antipixelhd/AnyList.git', 'refs/heads/main']).split()[0]
    if head != sha:
        print('Superseded main commit; skipping deployment', flush=True)
        return
    candidate = IMAGE + ':sha-' + sha
    run(['docker', 'pull', candidate])
    info = json.loads(output(['docker', 'image', 'inspect', candidate]))[0]
    if 'APP_VERSION=' + sha not in info['Config'].get('Env', []):
        raise RuntimeError('Published image does not match the requested commit')
    digest = next(item for item in info['RepoDigests'] if item.startswith(IMAGE + '@sha256:'))
    old = json.loads(output(['docker', 'inspect', 'anylist-app-1']))[0]['Image']
    if old == info['Id']:
        healthy('anylist-app-1')
        select(digest)
        print('Requested image is already healthy', flush=True)
        return
    run(['docker', 'image', 'tag', old, 'anylist-rollback:previous'])
    transaction = {'previous': old, 'restore_database': False}
    atomic(pending, json.dumps(transaction))
    try:
        # Stop writes before the snapshot so rollback does not lose pre-deploy writes.
        run(COMPOSE + ['stop', 'app'])
        with (STATE / 'database.dump').open('wb') as backup:
            os.chmod(backup.name, 0o600)
            run(['docker', 'exec', database(), 'sh', '-ec',
                 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], stdout=backup)
            backup.flush()
            os.fsync(backup.fileno())
        transaction['restore_database'] = True
        atomic(pending, json.dumps(transaction))
        start(digest)
    except Exception:
        recover(transaction)
        raise
    cleanup()
    # Never prune shared VPS images or force removal of an image used elsewhere.
    discard(old)
    print('AnyList deployed and healthy; previous image discarded if unused', flush=True)


def main():
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / 'deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (ROOT / 'compose.override.yaml').exists():
            select(IMAGE + ':latest')
        if sys.argv[1:] == ['--recover']:
            pending = STATE / 'transaction.json'
            if pending.exists():
                recover(json.loads(pending.read_text()))
        elif len(sys.argv) == 2:
            deploy(sys.argv[1])
        else:
            raise ValueError('Expected one commit SHA')


if __name__ == '__main__':
    main()
