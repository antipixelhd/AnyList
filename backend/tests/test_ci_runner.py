"""CI partitioning must preserve coverage and fail closed on incomplete work."""
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('backend_ci', Path(__file__).resolve().parents[2]/'scripts/backend_ci.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def reports(inventory, count):
    return [dict(index=index, count=count, inventory=inventory,
                 selected=inventory[index::count], tests_run=len(inventory[index::count]),
                 skipped=0, successful=True) for index in range(count)]


class CiRunnerTests(unittest.TestCase):
    def test_partition_preserves_all_discovered_occurrences_and_order(self):
        cases = [unittest.FunctionTestCase(lambda: None) for _ in range(3)]
        nested = unittest.TestSuite([cases[0], unittest.TestSuite(cases[1:])])
        self.assertEqual(list(ci.flatten(nested)), cases)
        self.assertEqual(len(list(ci.flatten(nested))), nested.countTestCases())
        inventory = ['unit.a', 'pg.a', 'pg.b', 'unit.a', 'pg.c', 'unit.b', 'pg.d']
        for count in (1, 2, 4, 9):
            with self.subTest(count=count):
                records = reports(inventory, count)
                ci.verify(records)
                for index, record in enumerate(records):
                    self.assertEqual(record['selected'], ci.partition(inventory, index, count))

    def test_missing_duplicate_changed_or_unfinished_shards_never_pass(self):
        valid = reports(['a', 'b', 'c', 'd', 'e'], 3)
        mutations = (
            lambda rows: rows.pop(),
            lambda rows: rows[1].update(index=0),
            lambda rows: rows[1].update(inventory=['different']),
            lambda rows: rows[1].update(selected=['a']),
            lambda rows: rows[1].update(tests_run=0),
            lambda rows: rows[1].update(successful=False),
            lambda rows: rows[1].update(count=4),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                rows = copy.deepcopy(valid)
                mutate(rows)
                with self.assertRaises(ValueError):
                    ci.verify(rows)
        with self.assertRaises(ValueError):
            ci.verify([])
        with self.assertRaises(ValueError):
            ci.verify(reports([], 1))

    def test_invalid_partition_and_empty_discovery_are_errors(self):
        for index, count in ((0, 0), (-1, 2), (2, 2)):
            with self.assertRaises(ValueError):
                ci.partition(['a'], index, count)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            ci.run_suite([], 0, 1, Path(directory)/'report.json')

    def test_failed_tests_record_failure_and_cannot_pass_the_gate(self):
        failing = unittest.FunctionTestCase(lambda: self.fail('expected inner failure'))
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'
            self.assertFalse(ci.run_suite([failing], 0, 1, path, runner))
            with self.assertRaises(ValueError):
                ci.verify([json.loads(path.read_text(encoding='utf-8'))])

    def test_normal_skips_keep_their_existing_result_and_count(self):
        skipped = unittest.FunctionTestCase(unittest.skip('existing opt-in')(lambda: None))
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report.json'
            self.assertTrue(ci.run_suite([skipped], 0, 1, path, runner))
            record = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(record['skipped'], 1)
            ci.verify([record])
