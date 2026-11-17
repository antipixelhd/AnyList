"""CPU attribution survives PostgreSQL process churn and PID reuse."""
import importlib.util
import unittest
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('stress_summary', Path(__file__).resolve().parents[2]/'scripts/stress/summarize.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
spec = importlib.util.spec_from_file_location('stress_monitor', Path(__file__).resolve().parents[2]/'scripts/stress/monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class StressMetricsTests(unittest.TestCase):
    def test_reused_run_replaces_primary_process_roles_instead_of_retaining_stale_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state/'cgroup.procs').write_text('')
            (state/'pids').write_text('101 102 103')
            (state/'load-pid').write_text('104')
            first = monitor.process_roles(state, state)
            (state/'pids').write_text('201 202 203')
            (state/'load-pid').write_text('204')
            second = monitor.process_roles(state, state)
            self.assertEqual(first, {'101': 'backend', '102': 'frontend', '103': 'jellyfin', '104': 'generator'})
            self.assertEqual(second, {'201': 'backend', '202': 'frontend', '203': 'jellyfin', '204': 'generator'})

    def test_surviving_intervals_retain_work_before_exit_and_ignore_reused_pid(self):
        def process(role, ticks, start=1):
            return {'role': role, 'user_ticks': ticks, 'system_ticks': 0, 'start_ticks': start}
        resources = [
            {'unix': 0, 'clock_ticks': 100, 'process_counters': {'1': process('backend', 100),
                '2': process('postgres', 1000), '4': process('api_reader', 10)}},
            {'unix': 1, 'process_counters': {'1': process('backend', 200), '2': process('postgres', 1050),
                '3': process('postgres', 10), '4': process('api_reader', 20)}},
            {'unix': 2, 'process_counters': {'1': process('backend', 300), '2': process('postgres', 5000, 2),
                '3': process('postgres', 20), '4': process('api_reader', 30)}}]
        usage, method = module.process_usage(resources)
        self.assertEqual(method, 'surviving_pid_pairs')
        self.assertEqual(usage, {'backend': 1.0, 'postgres': .3, 'api_reader': .1})

    def test_legacy_samples_are_labelled_and_use_recorded_clock_rate(self):
        usage, method = module.process_usage([
            {'unix': 0, 'clock_ticks': 250, 'process_ticks': {'backend': 100, 'postgres': 1000}},
            {'unix': 2, 'process_ticks': {'backend': 350, 'postgres': 20}}])
        self.assertEqual(method, 'legacy_aggregate')
        self.assertEqual(usage, {'backend': .5})
