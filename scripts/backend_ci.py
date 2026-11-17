"""Run the full unittest inventory in isolated CI jobs and verify completeness.

Local tests can still use `python -m unittest discover -s tests` unchanged.
CI uses Python 3.13 and the same discovery, fixtures, migrations and skip rules.
"""
import argparse
import json
import os
import sys
import time
import unittest
from pathlib import Path


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def partition(tests, index, count):
    if count < 1 or not 0 <= index < count:
        raise ValueError('Shard index must be in 0..count-1')
    # Interleave expensive fixture families, retaining their existing test order.
    return tests[index::count]


def run_suite(tests, index, count, manifest, runner=None):
    if not tests:
        raise ValueError('No backend tests discovered')
    selected = partition(tests, index, count)
    print(f'Backend shard {index+1}/{count}: {len(selected)} of {len(tests)} tests', flush=True)
    start = time.perf_counter()
    result = (runner or unittest.TextTestRunner(verbosity=1, durations=30)).run(unittest.TestSuite(selected))
    record = {
        'index': index, 'count': count,
        'inventory': [test.id() for test in tests],
        'selected': [test.id() for test in selected],
        'tests_run': result.testsRun, 'skipped': len(result.skipped),
        'successful': result.wasSuccessful(),
        'seconds': round(time.perf_counter()-start, 3),
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(record), encoding='utf-8')
    return record['successful']


def verify(records):
    if not records:
        raise ValueError('No backend shard reports found')
    reference = records[0]
    count, inventory = reference['count'], reference['inventory']
    if count < 1 or not inventory or len(records) != count:
        raise ValueError('Incomplete backend shard reports')
    if sorted(record['index'] for record in records) != list(range(count)):
        raise ValueError('Missing or duplicate backend shard')
    for record in records:
        if record['count'] != count or record['inventory'] != inventory:
            raise ValueError('Backend jobs discovered different test inventories')
        expected = partition(inventory, record['index'], count)
        if record['selected'] != expected or record['tests_run'] != len(expected):
            raise ValueError('Backend shard omitted, duplicated or did not finish tests')
        if record['successful'] is not True:
            raise ValueError('A backend shard failed')
    print(f'Verified all {len(inventory)} tests ran exactly once across {count} jobs; '
          f'{sum(record["skipped"] for record in records)} existing skips.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('--index', type=int, required=True)
    run.add_argument('--count', type=int, required=True)
    run.add_argument('--manifest', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'verify':
        verify([json.loads(path.read_text(encoding='utf-8')) for path in sorted(args.directory.glob('*.json'))])
        return 0
    backend = Path(__file__).resolve().parents[1] / 'backend'
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    # Discover/import every module before selecting a shard, preserving the
    # suite's shared fixture configuration and normal import failures.
    discovered = unittest.defaultTestLoader.discover('tests')
    tests = list(flatten(discovered))
    if len(tests) != discovered.countTestCases():
        raise ValueError('Backend discovery lost nested tests')
    return 0 if run_suite(tests, args.index, args.count, args.manifest) else 1


if __name__ == '__main__':
    raise SystemExit(main())
