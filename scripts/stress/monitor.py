"""Sample host and benchmark cgroup resources from the host network namespace."""

import argparse
import json
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path


def proc_cpu():
    fields = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
    return {'total': sum(fields), 'idle': fields[3], 'iowait': fields[4], 'steal': fields[7]}


def process_cpu(cgroup, roles):
    result = Counter()
    for pid in (cgroup/'cgroup.procs').read_text().split():
        try:
            name = Path(f'/proc/{pid}/comm').read_text().strip()
            role = roles.get(pid, 'postgres' if name == 'postgres' else None)
            if role:
                stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                result[role] += int(stat[11]) + int(stat[12])
        except (ProcessLookupError, FileNotFoundError):
            pass
    return result


def key_values(path):
    return {key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())}


def health(url):
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return {'status': response.status, 'seconds': round(time.perf_counter()-start, 4)}
    except OSError as error:
        return {'error': type(error).__name__, 'seconds': round(time.perf_counter()-start, 4)}


def monitor(args):
    if not args.unit.startswith('anylist-stress-') or '/' in args.unit:
        raise SystemExit('Pass a dedicated benchmark unit.')
    cgroup = Path('/sys/fs/cgroup/system.slice') / (args.unit+'.service')
    previous = None
    bad = 0
    roles = {}
    with args.output.open('x', encoding='utf-8') as output:
        for iteration in range(args.seconds):
            started = time.monotonic()
            if not cgroup.is_dir():
                if previous:
                    break
                time.sleep(1)
                continue
            state = args.output.parent
            if not roles and (state/'pids').exists():
                roles.update(zip((state/'pids').read_text().split(), ('backend', 'frontend', 'jellyfin')))
            if (state/'load-pid').exists():
                roles[(state/'load-pid').read_text().strip()] = 'generator'
            if (state/'catalogue-pid').exists():
                roles[(state/'catalogue-pid').read_text().strip()] = 'catalogue'
            for pid in (cgroup/'cgroup.procs').read_text().split():
                try:
                    command = Path(f'/proc/{pid}/cmdline').read_bytes()
                    if b'multiprocessing.spawn' in command:
                        roles[pid] = 'backend'
                except FileNotFoundError:
                    pass
            cgroup_cpu = key_values(cgroup/'cpu.stat')
            host_cpu = proc_cpu()
            by_process = process_cpu(cgroup, roles)
            with Path('/proc/meminfo').open() as info:
                available_kb = next(int(line.split()[1]) for line in info if line.startswith('MemAvailable:'))
            sample = {'unix': time.time(), 'host_mem_available_mib': round(available_kb/1024),
                      'cgroup_mem_mib': round(int((cgroup/'memory.current').read_text())/1048576),
                      'cgroup_cpu': cgroup_cpu, 'host_cpu': host_cpu, 'process_ticks': by_process,
                      'loadavg': Path('/proc/loadavg').read_text().split()[:3],
                      'io_pressure': (cgroup/'io.pressure').read_text().strip(),
                      'cpu_pressure': (cgroup/'cpu.pressure').read_text().strip()}
            if iteration % 5 == 0:
                sample['production_probe'] = health(args.health)
                probe = sample['production_probe']
                bad = bad+1 if probe.get('status') != 200 or probe['seconds'] > 1 else 0
            if available_kb < 2 * 1024 * 1024 or bad >= 3:
                sample['aborted_for_host_safety'] = True
                print(json.dumps(sample), file=output, flush=True)
                subprocess.run(['sudo', '-n', 'systemctl', 'stop', args.unit], check=True)
                return
            print(json.dumps(sample), file=output, flush=True)
            previous = sample
            time.sleep(max(.05, 1-(time.monotonic()-started)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('unit')
    parser.add_argument('output', type=Path)
    parser.add_argument('health', help='Existing host-local endpoint for one probe every five seconds')
    parser.add_argument('--seconds', type=int, default=1500)
    monitor(parser.parse_args())
