"""Summarize completed mixed-load stages and optional Linux host samples."""

import argparse
import json
import math
from pathlib import Path


def records(path):
    with path.open(encoding='utf-8') as source:
        return [json.loads(line) for line in source if line.strip()]


def p95(values):
    return round(sorted(values)[math.ceil(.95*len(values))-1], 2) if values else None


def pressure(sample, field):
    return int(sample[field].splitlines()[0].rsplit('total=', 1)[1])


def summary(stage, host):
    start, duration = stage['stage_start_unix'], stage['duration']
    measured = [sample for sample in stage['samples'] if 'app' in sample]
    resources = [sample for sample in host if start <= sample['unix'] <= start+duration]
    values = {key: stage[key] for key in ('users', 'duration', 'journeys_per_sec', 'rps', 'p95', 'errors',
                                         'sync_completed', 'sync_started', 'sync_end_to_end_seconds')}
    attempts = stage['request_count']
    values.update(successful_requests_per_second=round((attempts-stage['errors'])/(duration+stage['drain_seconds']), 2),
                  error_percent=round(stage['errors']*100/attempts, 2) if attempts else None,
                  game_primary_outcomes=stage.get('game_primary_outcomes', {}),
                  missing_app_samples=len(stage['samples'])-len(measured),
                  operation_p95_ms={k: round(v['p95']*1000) for k, v in stage['labels'].items()},
                  operation_errors={k: v['errors'] for k, v in stage['labels'].items() if v['errors']})
    if len(measured) >= 2:
        a, b = measured[0], measured[-1]
        interval = b['elapsed']-a['elapsed']
        providers_a = a['app']['providers']
        values.update(
            pool_peak=max(s['app']['sql'].get('peak_pool', 0) for s in measured),
            loop_lag_p95_ms=p95([lag for s in measured for lag in s['app'].get('loop_lag_ms', [])]),
            pg_lock_wait_peak=max(s['pg']['lock_wait'] for s in measured),
            pg_active_peak=max(s['pg']['active'] for s in measured),
            pg_idle_transaction_peak=max(s['pg']['idle_in_transaction'] for s in measured),
            sql_per_sec=round((b['app']['sql'].get('count', 0)-a['app']['sql'].get('count', 0))/interval, 1),
            mapper_turns=(b['mapper']['turns'] or 0)-(a['mapper']['turns'] or 0),
            mapper_processed=(b['mapper']['processed'] or 0)-(a['mapper']['processed'] or 0),
            mapper_due=b['mapper']['due'], mapper_errors=b['mapper']['errors'],
            sync_pending=b['sync']['pending'], sync_running=b['sync']['running'],
            provider_requests={k: delta for k, count in b['app']['providers'].items()
                               if (delta := count-providers_a.get(k, 0))},
        )
    if len(resources) >= 2:
        a, b = resources[0], resources[-1]
        wall = b['unix']-a['unix']
        a_cpu, b_cpu = a['cgroup_cpu'], b['cgroup_cpu']
        elapsed_ticks = b['host_cpu']['total']-a['host_cpu']['total']
        percentages = {key: (b['host_cpu'][key]-a['host_cpu'][key])*100/elapsed_ticks
                       for key in ('idle', 'iowait', 'steal')}
        periods = b_cpu['nr_periods']-a_cpu['nr_periods']
        by_process = {}
        for role in sorted(set(b['process_ticks']) & set(a['process_ticks'])):
            if b['process_ticks'][role] >= a['process_ticks'][role]:
                by_process[role] = round((b['process_ticks'][role]-a['process_ticks'][role])/(100*wall), 3)
        values.update(
            benchmark_average_cpu_cores=round((b_cpu['usage_usec']-a_cpu['usage_usec'])/(1e6*wall), 3),
            process_average_cpu_cores=by_process,
            throttle_period_percent=round((b_cpu['nr_throttled']-a_cpu['nr_throttled'])*100/max(1, periods), 2),
            host_busy_percent=round(100-sum(percentages.values()), 2),
            host_iowait_percent=round(percentages['iowait'], 2), host_steal_percent=round(percentages['steal'], 2),
            fixture_io_pressure_percent=round((pressure(b, 'io_pressure')-pressure(a, 'io_pressure'))/(wall*1e4), 2),
            benchmark_peak_mib=max(s['cgroup_mem_mib'] for s in resources),
            host_min_available_mib=min(s['host_mem_available_mib'] for s in resources),
            peer_probe_ms_p95=p95([s['production_probe']['seconds']*1000 for s in resources if 'production_probe' in s]),
        )
    return values


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stages', type=Path)
    parser.add_argument('--host', type=Path)
    args = parser.parse_args()
    host = records(args.host) if args.host else []
    print(json.dumps([summary(stage, host) for stage in records(args.stages)], indent=2))
