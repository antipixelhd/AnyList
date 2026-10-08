"""Opt-in rollback integration test against a labelled disposable Postgres container."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('production', Path(__file__).parents[1] / 'production.py')
production = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production)
CONTAINER = os.environ.get('ANYLIST_DEPLOY_TEST_POSTGRES_CONTAINER', '')


@unittest.skipUnless(CONTAINER, 'Requires a disposable PostgreSQL container')
class DatabaseRollbackTests(unittest.TestCase):
    def test_restore_removes_new_migration_objects_and_restores_data(self):
        self.assertRegex(CONTAINER, r'^anylist-production-test-[a-z0-9]+$')
        info = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER]))[0]
        self.assertEqual(info['Config']['Labels'].get('anylist.test'), 'production-rollback')
        self.assertEqual(info['HostConfig']['NetworkMode'], 'none')
        self.assertIn('POSTGRES_DB=rollback_test', info['Config']['Env'])

        def sql(statement):
            return subprocess.check_output(['docker', 'exec', CONTAINER, 'psql', '-U',
                                            'postgres', '-d', 'rollback_test', '-Atc', statement], text=True).strip()

        sql('CREATE TABLE original (value text); INSERT INTO original VALUES (\'before\');')
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (state / 'database.dump').open('wb') as backup:
                subprocess.run(['docker', 'exec', CONTAINER, 'pg_dump', '-Fc', '-U', 'postgres',
                                '-d', 'rollback_test'], check=True, stdout=backup)
            sql("UPDATE original SET value = 'after'; CREATE TABLE new_migration (id int);")
            real_run = production.run

            def isolated_run(args, **kwargs):
                if args == production.COMPOSE + ['stop', 'app']:
                    return
                self.assertEqual(args[:4], ['docker', 'exec', '-i', CONTAINER])
                return real_run(args, **kwargs)

            def previous_healthy(image):
                self.assertEqual(image, 'previous-image')
                self.assertEqual(sql('SELECT value FROM original'), 'before')
                self.assertEqual(sql("SELECT to_regclass('public.new_migration') IS NULL"), 't')

            with patch.object(production, 'STATE', state), \
                 patch.object(production, 'database', return_value=CONTAINER), \
                 patch.object(production, 'run', side_effect=isolated_run), \
                 patch.object(production, 'start', side_effect=previous_healthy) as start, \
                 patch.object(production, 'cleanup'):
                production.recover({'previous': 'previous-image', 'restore_database': True})
                start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
