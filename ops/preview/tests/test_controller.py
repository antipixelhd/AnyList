import contextlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("controller", Path(__file__).parents[1] / "controller.py")
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
SLOTS = c.load_slots(Path(__file__).parents[1] / "slots.json")
SHA = "a" * 40
OLD = "b" * 40


class FakePreview(c.Preview):
    def __init__(self, branch, state):
        self.branch, self.slots, self.slot = branch, SLOTS, SLOTS[branch]
        self.state, self.checkout = state, state / "checkout"
        self.events = []
        self.remote, self.compatible, self.non_ff = SHA, True, False
        self.changed = False
        self.fail_migration = False

    def fetch(self):
        self.events.append("fetch")
        return self.remote

    def git(self, *args, **kwargs):
        self.events.append(args[0])
        if args[0] == "merge-base":
            return int(self.non_ff)
        if args[0] == "cat-file":
            return 0
        if args[0] == "diff":
            return "backend/migrations/versions/new.py" if self.changed else ""
        return ""

    def services(self, action):
        self.events.append(action)

    def dependencies(self, force=False):
        self.events.append("dependencies-force" if force else "dependencies")

    def migration_state(self):
        return {"compatible": self.compatible, "pending": self.changed,
                "current": ["old"], "heads": ["new"]}

    def snapshot(self, permanent=False):
        self.events.append("backup" if permanent else "snapshot")

    def migrate(self):
        self.events.append("migrate")
        if self.fail_migration:
            raise RuntimeError("Migration failed")

    def reset_database(self):
        self.events.append("db-reset")

    def restart(self):
        self.events.append("restart")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.lock = patch.object(c, "lock", lambda name: contextlib.nullcontext())
        self.lock.start()
        self.addCleanup(self.lock.stop)

    def preview(self, branch="development-1"):
        p = FakePreview(branch, self.state)
        (self.state / "deployed.sha").write_text(OLD)
        return p

    def test_mapping_and_unknown_input(self):
        self.assertEqual(SLOTS["beta"]["frontend_port"], 8001)
        self.assertEqual(SLOTS["development-2"]["backend_port"], 8103)
        for branch in ["main", "../beta", "development-3", "beta;id"]:
            with self.assertRaises(c.Refused):
                c.validate(SLOTS, branch, "status")

    def test_manual_validation_and_beta_protection(self):
        for op in c.OPERATIONS - {"reset-db-from-beta"}:
            c.validate(SLOTS, "beta", op)
        for op in ["wipe", "downgrade", "shell", "reset-db-from-beta"]:
            with self.assertRaises(c.Refused):
                c.validate(SLOTS, "beta", op)
        with self.assertRaises(c.Refused):
            c.validate(SLOTS, "beta", "deploy", "HEAD")
        with self.assertRaises(c.Refused):
            c.validate(SLOTS, "beta", "deploy", SHA, "yes")
        # Guard exists at the destructive primitive too, not just CLI validation.
        p = self.preview("beta")
        with self.assertRaises(c.Refused):
            c.Preview.reset_database(p)
        self.assertEqual(p.events, [])

    def test_normal_development_preserves_database(self):
        p = self.preview()
        self.assertEqual(p.deploy(SHA), "deployed")
        self.assertNotIn("db-reset", p.events)
        self.assertIn("migrate", p.events)
        self.assertEqual((self.state / "deployed.sha").read_text().strip(), SHA)

    def test_forced_nonfastforward_and_incompatible_development_reset(self):
        for mode in ["forced", "non_ff", "incompatible", "recreate"]:
            with self.subTest(mode=mode):
                p = self.preview()
                p.non_ff = mode == "non_ff"
                p.compatible = mode != "incompatible"
                p.deploy(SHA, forced=mode == "forced", recreate=mode == "recreate")
                self.assertIn("db-reset", p.events)
                self.assertLess(p.events.index("db-reset"), p.events.index("restart"))

    def test_beta_never_resets_even_on_force_or_recreation(self):
        p = self.preview("beta")
        p.changed = True
        p.deploy(SHA, forced=True, recreate=True)
        self.assertNotIn("db-reset", p.events)
        self.assertLess(p.events.index("backup"), p.events.index("migrate"))

    def test_beta_incompatibility_and_migration_failure_fail_safely(self):
        for compatible in [False, True]:
            p = self.preview("beta")
            p.compatible = compatible
            p.fail_migration = True
            with self.assertRaises((c.Refused, RuntimeError)):
                p.deploy(SHA)
            self.assertNotIn("db-reset", p.events)
            self.assertNotIn("restart", p.events)
            self.assertEqual((self.state / "deployed.sha").read_text(), OLD)

    def test_stale_sha_does_not_touch_runtime_or_database(self):
        p = self.preview()
        p.remote = "c" * 40
        self.assertEqual(p.deploy(SHA), "superseded")
        self.assertEqual(p.events, ["fetch"])

    def test_dependency_hash_ignores_source_but_tracks_manifests(self):
        for component, paths in c.DEPENDENCIES.items():
            for path in paths:
                target = self.state / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("initial")
            first = c.dependency_digest(self.state, component)
            (self.state / component / "source.txt").write_text("source edit")
            self.assertEqual(c.dependency_digest(self.state, component), first)
            (self.state / paths[-1]).write_text("lock changed")
            self.assertNotEqual(c.dependency_digest(self.state, component), first)

    def test_configuration_rejects_destructive_mapping_and_port_collision(self):
        for field, value in [("persistent", False), ("database", "production"), ("frontend_port", 8101)]:
            slots = json.loads(json.dumps(SLOTS))
            slots["beta"][field] = value
            path = self.state / "slots.json"
            path.write_text(json.dumps(slots))
            with self.assertRaises(c.Refused):
                c.load_slots(path)

    def test_clone_stops_only_target_and_restores_target_owner_then_cleans_snapshot(self):
        p = self.preview()
        p.role = SLOTS[p.branch]["database"]
        snapshot = self.state / "snapshot.dump"
        snapshot.write_bytes(b"dump")
        p.snapshot = lambda: snapshot
        commands = []
        calls = []
        def command(args, **kwargs):
            commands.append(args)
            calls.append(kwargs)
        with patch.object(c, "run", command):
            c.Preview.reset_database(p)
        self.assertEqual(p.events, ["stop", "migrate"])
        self.assertEqual([args[4] for args in commands], ["dropdb", "createdb", "psql", "pg_restore"])
        self.assertTrue(all(p.role in args for args in commands if args[4] != "psql"))
        self.assertEqual(calls[2]["input"], f"REVOKE ALL ON DATABASE {p.role} FROM PUBLIC;\n")
        self.assertFalse(any(SLOTS["beta"]["database"] in args for args in commands))
        self.assertFalse(snapshot.exists())

    def test_permanent_snapshot_retains_recent_backups_and_keeps_new_dump(self):
        p = self.preview("beta")
        folder = self.state / "backups"
        folder.mkdir()
        for index in range(3):
            previous = folder / f"beta-old-{index}.dump"
            previous.write_bytes(b"old")
            os.utime(previous, (index + 1, index + 1))
        def dump(args, **kwargs):
            kwargs["stdout"].write(b"consistent snapshot")
        with patch.object(c, "STATE", self.state), patch.object(c, "BACKUP_KEEP", 2), \
             patch.object(c.subprocess, "run", dump):
            path = c.Preview.snapshot(p, permanent=True)
        self.assertEqual(path.read_bytes(), b"consistent snapshot")
        self.assertEqual(len(list(folder.glob("beta-*.dump"))), 2)
        self.assertTrue((folder / "beta-old-2.dump").exists())

    def test_failed_restore_cleans_snapshot_and_never_runs_migrations(self):
        p = self.preview()
        p.role = SLOTS[p.branch]["database"]
        snapshot = self.state / "snapshot.dump"
        snapshot.write_bytes(b"dump")
        p.snapshot = lambda: snapshot
        def command(args, **kwargs):
            if "pg_restore" in args:
                raise RuntimeError("restore failed")
        with patch.object(c, "run", command), self.assertRaises(RuntimeError):
            c.Preview.reset_database(p)
        self.assertEqual(p.events, ["stop"])
        self.assertFalse(snapshot.exists())

    def test_multiple_heads_are_upgraded_without_manufacturing_merge_revision(self):
        p = self.preview()
        p.home = self.state
        p.migration_state = lambda: {"compatible": True, "current": ["base"], "heads": ["a", "b"]}
        commands = []
        p.app = lambda args, **kwargs: commands.append(args)
        c.Preview.migrate(p)
        self.assertEqual(commands[0][-3:], ["alembic", "upgrade", "heads"])

    def test_dependency_sync_only_changes_affected_component_or_broken_environment(self):
        p = self.preview()
        p.home = self.state
        for paths in c.DEPENDENCIES.values():
            for name in paths:
                target = p.checkout / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("manifest")
        commands = []
        p.app = lambda args, **kwargs: commands.append(args) or 0
        c.Preview.dependencies(p)
        self.assertEqual(sum("sync" in args or "ci" in args for args in commands), 2)
        commands.clear()
        c.Preview.dependencies(p)
        self.assertFalse(any("sync" in args or "ci" in args for args in commands))
        (p.checkout / "frontend/package-lock.json").write_text("changed")
        commands.clear()
        c.Preview.dependencies(p)
        self.assertIn(["npm", "ci"], commands)
        self.assertFalse(any("sync" in args for args in commands))

    def test_additive_configuration_sync_rejects_beta_changes(self):
        p = self.preview("beta")
        install = self.state / "installed"
        install.mkdir()
        slots = json.loads(json.dumps(SLOTS))
        slots["development-3"] = {"frontend_port": 8004, "backend_port": 8104,
                                  "database": "anylist_preview_development_3", "persistent": False}
        p.git = lambda *args: json.dumps(slots)
        with patch.object(c, "STATE", self.state), patch.object(c, "INSTALL", install):
            p.sync_config()
            self.assertIn("development-3", c.load_slots(install / "slots.json"))
            slots["beta"]["frontend_port"] = 9001
            with self.assertRaises(c.Refused):
                p.sync_config()
            self.assertEqual(c.load_slots(install / "slots.json")["beta"]["frontend_port"], 8001)


if __name__ == "__main__":
    unittest.main()

