import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('production', Path(__file__).parents[1] / 'production.py')
production = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production)
SHA = 'a' * 40
OLD = 'sha256:' + 'b' * 64
NEW = 'sha256:' + 'c' * 64
DIGEST = production.IMAGE + '@sha256:' + 'd' * 64


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.events = []
        self.addCleanup(patch.stopall)
        patch.object(production, 'STATE', self.state).start()
        patch.object(production, 'atomic', side_effect=lambda path, value: path.write_text(value)).start()
        patch.object(production, 'output', side_effect=self.output).start()
        patch.object(production, 'run', side_effect=lambda args, **kwargs: self.events.append(args)).start()
        patch.object(production, 'start', side_effect=lambda image: self.events.append(['start', image])).start()
        patch.object(production, 'cleanup', side_effect=lambda: self.events.append(['cleanup'])).start()
        patch.object(production, 'discard', side_effect=lambda image: self.events.append(['discard', image])).start()

    def output(self, args):
        if args[0] == 'git':
            return SHA + '\trefs/heads/main'
        if args[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': NEW, 'Config': {'Env': ['APP_VERSION=' + SHA]}, 'RepoDigests': [DIGEST]}])
        if args[:2] == ['docker', 'inspect']:
            return json.dumps([{'Image': OLD}])
        return 'database-id'

    def test_success_pins_digest_and_discards_old_only_after_health(self):
        production.deploy(SHA)
        self.assertLess(next(i for i, e in enumerate(self.events) if 'pg_dump' in ' '.join(e)),
                        self.events.index(['start', DIGEST]))
        self.assertEqual(self.events[-3:], [['start', DIGEST], ['cleanup'], ['discard', OLD]])

    def test_failed_candidate_restores_database_before_starting_previous(self):
        def start(image):
            self.events.append(['start', image])
            if image == DIGEST:
                raise RuntimeError('unhealthy')
        production.start.side_effect = start
        with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
            production.deploy(SHA)
        restore = next(i for i, e in enumerate(self.events) if 'pg_restore' in ' '.join(e))
        self.assertLess(restore, self.events.index(['start', OLD]))
        self.assertNotIn(['discard', OLD], self.events)

    def test_backup_failure_restarts_old_without_restoring_incomplete_dump(self):
        def run(args, **kwargs):
            self.events.append(args)
            if 'pg_dump' in ' '.join(args):
                raise RuntimeError('backup failed')
        production.run.side_effect = run
        with self.assertRaisesRegex(RuntimeError, 'backup failed'):
            production.deploy(SHA)
        self.assertIn(['start', OLD], self.events)
        self.assertFalse(any('pg_restore' in ' '.join(e) for e in self.events))

    def test_superseded_commit_never_pulls_or_stops_container(self):
        production.deploy('e' * 40)
        self.assertEqual(self.events, [])

    def test_invalid_commit_never_reaches_docker(self):
        with self.assertRaises(ValueError):
            production.deploy('--privileged')
        self.assertEqual(self.events, [])

    def test_failed_rollback_preserves_recovery_files(self):
        (self.state / 'database.dump').write_bytes(b'backup')
        transaction = {'previous': OLD, 'restore_database': True}
        (self.state / 'transaction.json').write_text(json.dumps(transaction))
        production.start.side_effect = RuntimeError('old unhealthy')
        with self.assertRaisesRegex(RuntimeError, 'old unhealthy'):
            production.recover(transaction)
        self.assertTrue((self.state / 'transaction.json').exists())
        self.assertTrue((self.state / 'database.dump').exists())
        self.assertNotIn(['cleanup'], self.events)


class HealthTests(unittest.TestCase):
    def test_exited_or_unhealthy_candidate_fails_immediately(self):
        for state in [{'Status': 'exited'}, {'Status': 'running', 'Health': {'Status': 'unhealthy'}}]:
            with self.subTest(state=state), patch.object(production, 'output', return_value=json.dumps([{'State': state}])):
                with self.assertRaisesRegex(RuntimeError, 'failed its health'):
                    production.healthy('candidate')

    def test_backend_health_without_frontend_never_passes(self):
        state = [{'State': {'Status': 'running', 'Health': {'Status': 'healthy'}}}]
        with patch.object(production, 'output', return_value=json.dumps(state)), \
             patch.object(production.subprocess, 'run') as execute, \
             patch.object(production.time, 'monotonic', side_effect=[0, 1, 200]), \
             patch.object(production.time, 'sleep'):
            execute.return_value.returncode = 1
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                production.healthy('candidate')

    def test_both_health_checks_must_stay_good_for_fifteen_seconds(self):
        state = [{'State': {'Status': 'running', 'Health': {'Status': 'healthy'}}}]
        with patch.object(production, 'output', return_value=json.dumps(state)), \
             patch.object(production.subprocess, 'run') as execute, \
             patch.object(production.time, 'monotonic', side_effect=[0, 1, 1, 1, 17, 17]), \
             patch.object(production.time, 'sleep'):
            execute.return_value.returncode = 0
            production.healthy('candidate')
            self.assertEqual(execute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
