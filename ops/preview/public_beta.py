"""Fixed-target public beta gate. Installed root-owned, outside slot checkouts."""
import contextlib
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

INSTALL = Path('/opt/anylist-preview')
CADDYFILE = Path('/opt/public-gateway/Caddyfile')
BACKUP = Path('/var/lib/anylist-preview/public-beta-before-install.caddy')
BEGIN = '# BEGIN ANYLIST PUBLIC BETA\n'
END = '# END ANYLIST PUBLIC BETA\n'
SOCKET = 'anylist-public-beta.socket'
SERVICE = 'anylist-public-beta.service'


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def block(enabled):
    template = (INSTALL / 'public-beta.caddy.in').read_text()
    mode = 'ON' if enabled else 'OFF'
    route = ('reverse_proxy unix//config/anylist-beta.sock' if enabled else
             'respond "Beta is currently unavailable." 503')
    return BEGIN + template.replace('@MODE@', mode).replace('@ROUTE@', route).rstrip() + '\n' + END


def replace_block(config, enabled):
    if config.count(BEGIN) != 1 or config.count(END) != 1:
        raise RuntimeError('Public beta gateway is not installed or has duplicate markers')
    before, rest = config.split(BEGIN)
    managed, after = rest.split(END)
    if BEGIN in managed or END in before:
        raise RuntimeError('Invalid public beta gateway markers')
    return before + block(enabled) + after


def configured_mode():
    config = CADDYFILE.read_text()
    for enabled in (False, True):
        if replace_block(config, enabled) == config:
            return 'ON' if enabled else 'OFF'
    raise RuntimeError('Public beta configuration differs from the installed template')


def validate(config):
    command('docker', 'exec', '-i', 'public-gateway', 'caddy', 'validate',
            '--config', '-', '--adapter', 'caddyfile', input=config,
            stdout=subprocess.DEVNULL)


def write_config(config):
    # Caddyfile is a single-file Docker bind mount: preserve its inode.
    with CADDYFILE.open('r+') as stream:
        original = stream.read()
        try:
            stream.seek(0)
            stream.write(config)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.seek(0)
            stream.write(original)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())
            raise


def reload_gateway():
    # The installed gateway uses `caddy run --config ...`, with admin disabled.
    # SIGUSR1 reloads that same file gracefully without opening the admin API.
    command('docker', 'kill', '--signal=USR1', 'public-gateway', stdout=subprocess.DEVNULL)


def verify_public(enabled):
    mode, status = ('ON', 200) if enabled else ('OFF', 503)
    deadline = time.monotonic() + 25
    while True:
        try:
            request = Request('https://beta.anylist.win/login?gate_check=' + str(time.time_ns()),
                              headers={'Cache-Control': 'no-cache', 'User-Agent': 'AnyList-Beta-Gate/1.0'})
            try:
                response = urlopen(request, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                if response.status == status and response.headers.get('X-AnyList-Public-Beta') == mode:
                    return
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Could not verify public beta ' + mode)
        time.sleep(1)


def disable_socket():
    # Stop the socket AND an already activated proxy, including existing streams.
    command('systemctl', 'stop', SOCKET, SERVICE)


def set_public(enabled):
    original = CADDYFILE.read_text()
    candidate = replace_block(original, enabled)
    validate(candidate)
    try:
        if not enabled:
            disable_socket()
        write_config(candidate)
        reload_gateway()
        if enabled:
            command('systemctl', 'start', SOCKET)
        verify_public(enabled)
    except BaseException:
        # A failed enable must never leave the app unintentionally public.
        # A failed OFF remains OFF rather than restoring a previously ON route.
        disable_socket()
        with contextlib.suppress(Exception):
            write_config(replace_block(original, False))
            reload_gateway()
        raise
    print('Public beta.anylist ' + ('ON' if enabled else 'OFF'), flush=True)


def install():
    """One-time administrator setup, after certificate and socket unit installation."""
    original = CADDYFILE.read_text()
    if BEGIN in original or END in original or 'beta.anylist.win' in original:
        raise RuntimeError('Beta route already exists; refusing duplicate installation')
    candidate = original.rstrip() + '\n\n' + block(False)
    validate(candidate)
    if BACKUP.exists():
        raise RuntimeError('Initial gateway backup already exists')
    BACKUP.write_text(original)
    BACKUP.chmod(0o600)
    disable_socket()
    try:
        write_config(candidate)
        reload_gateway()
    except BaseException:
        write_config(original)
        reload_gateway()
        raise
