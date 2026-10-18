"""Database backups carry referenced avatars; missing files keep a safe fallback."""
import gzip
import io
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import backup
from routers import admin, profile


def archive(version, tables, avatars=None):
    avatars = avatars or {}
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb") as compressed:
        header = {"version": version, "tables": list(tables)}
        if version == 2:
            header["avatars"] = backup.backup_avatar_manifest(avatars)
        encoded = json.dumps(header).encode()
        compressed.write(struct.pack(">I", len(encoded)))
        compressed.write(encoded)
        for name, data in tables.items():
            backup.write_backup_entry(compressed, name, data)
        for name, data in avatars.items():
            backup.write_backup_entry(compressed, name, data)
    return output.getvalue()


class FakeConnection:
    def __init__(self):
        self.copied = []
        self.closed = False
        self.avatar_locked = False

    async def fetch(self, query):
        if "pg_tables" in query:
            return [{"tablename": "user_profiles"}]
        if "avatar_path IS NOT NULL" in query:
            assert self.avatar_locked
            return [{"user_id": 7, "avatar_path": "7.webp"}]
        return []

    async def execute(self, query, *args):
        if query in (backup.AVATAR_WRITE_LOCK_SQL, backup.AVATAR_RESTORE_LOCK_SQL):
            self.avatar_locked = True

    async def copy_to_table(self, table, source, format):
        assert self.avatar_locked
        self.copied.append((table, source.read(), format))

    async def copy_from_table(self, table, output, format):
        assert self.avatar_locked
        output.write(b"profile data")

    async def close(self):
        self.closed = True

    def transaction(self):
        class Transaction:
            async def __aenter__(self):
                pass
            async def __aexit__(self, *_):
                pass
        return Transaction()


class BackupAvatarTests(unittest.IsolatedAsyncioTestCase):
    def test_old_and_new_archives_and_untrusted_file_names(self):
        tables = {"user_profiles": b"profile data"}
        self.assertEqual(backup.parse_backup(archive(1, tables)), (["user_profiles"], tables, {}))
        avatars = {"7.webp": b"webp image"}
        self.assertEqual(backup.parse_backup(archive(2, tables, avatars)), (["user_profiles"], tables, avatars))
        for name in ("../7.webp", "7.svg", "0.jpg"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Invalid backup"):
                backup.parse_backup(archive(2, tables, {name: b"image"}))

    async def test_restore_replaces_stale_avatar_files_after_database_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / "avatars"
            avatars.mkdir()
            (avatars / "7.jpg").write_bytes(b"old image")
            (avatars / "8.png").write_bytes(b"unrelated image")
            connection = FakeConnection()
            original_close = connection.close

            async def close_after_swap():
                self.assertTrue(connection.avatar_locked)
                self.assertEqual((avatars / '7.webp').read_bytes(), b'new image')
                await original_close()

            connection.close = close_after_swap
            with patch.object(backup.settings, "data_dir", root), patch.object(backup, "asyncpg_conn", AsyncMock(return_value=connection)):
                await backup.restore_backup(archive(2, {"user_profiles": b"profile data"}, {"7.webp": b"new image"}))
            self.assertEqual((avatars / "7.webp").read_bytes(), b"new image")
            self.assertEqual(sorted(path.name for path in avatars.iterdir()), ["7.webp"])
            self.assertEqual(connection.copied, [("user_profiles", b"profile data", "binary")])
            self.assertTrue(connection.closed)

    async def test_old_backup_cannot_leave_another_users_avatar_behind(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / "avatars"
            avatars.mkdir()
            (avatars / "7.jpg").write_bytes(b"old image")
            with patch.object(backup.settings, "data_dir", root), patch.object(backup, "asyncpg_conn", AsyncMock(return_value=FakeConnection())):
                await backup.restore_backup(archive(1, {"user_profiles": b"old profile data"}))
            self.assertEqual(list(avatars.iterdir()), [])

    async def test_failed_database_restore_keeps_existing_avatar_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / "avatars"
            avatars.mkdir()
            (avatars / "7.jpg").write_bytes(b"current image")
            connection = FakeConnection()
            connection.copy_to_table = AsyncMock(side_effect=RuntimeError("copy failed"))
            with patch.object(backup.settings, "data_dir", root), patch.object(backup, "asyncpg_conn", AsyncMock(return_value=connection)):
                with self.assertRaisesRegex(RuntimeError, "copy failed"):
                    await backup.restore_backup(archive(2, {"user_profiles": b"profile data"}, {"7.webp": b"new image"}))
            self.assertEqual((avatars / "7.jpg").read_bytes(), b"current image")
            self.assertEqual(sorted(path.name for path in avatars.iterdir()), ["7.jpg"])

    async def test_admin_export_includes_only_referenced_avatar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / "avatars"
            avatars.mkdir()
            (avatars / "7.webp").write_bytes(b"approved image")
            (avatars / "8.jpg").write_bytes(b"orphan")
            connection = FakeConnection()
            with patch.object(backup.settings, "data_dir", root), patch.object(admin, "asyncpg_conn", AsyncMock(return_value=connection)):
                response = await admin.backup_database(None)
            self.assertEqual(backup.parse_backup(response.body), (["user_profiles"], {"user_profiles": b"profile data"}, {"7.webp": b"approved image"}))
            self.assertTrue(connection.closed)

    async def test_avatar_delete_locks_before_removing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / 'avatars'
            avatars.mkdir()
            avatar = avatars / '7.webp'
            avatar.write_bytes(b'current image')
            row = SimpleNamespace(avatar_path='7.webp')
            calls = []

            async def execute(query):
                calls.append(str(query))
                if len(calls) == 1:
                    self.assertEqual(str(query), backup.AVATAR_WRITE_LOCK_SQL)
                    self.assertTrue(avatar.exists())
                return SimpleNamespace(scalar_one_or_none=lambda: row)

            db = SimpleNamespace(execute=execute, commit=AsyncMock())
            with patch.object(profile.settings, 'data_dir', root):
                await profile.delete_avatar(current_user=SimpleNamespace(id=7), db=db)
            self.assertFalse(avatar.exists())
            self.assertIsNone(row.avatar_path)
            db.commit.assert_awaited_once()

    async def test_avatar_upload_locks_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / 'avatars'
            avatars.mkdir()
            avatar = avatars / '7.webp'
            avatar.write_bytes(b'current image')
            row = SimpleNamespace(avatar_path='7.webp')
            calls = []

            async def execute(query):
                calls.append(str(query))
                if len(calls) == 1:
                    self.assertEqual(str(query), backup.AVATAR_WRITE_LOCK_SQL)
                    self.assertEqual(avatar.read_bytes(), b'current image')
                return SimpleNamespace(scalar_one_or_none=lambda: row)

            db = SimpleNamespace(execute=execute, commit=AsyncMock())
            file = SimpleNamespace(content_type='image/png', read=AsyncMock(return_value=b'new image'))
            with patch.object(profile.settings, 'data_dir', root):
                await profile.upload_avatar(file=file, current_user=SimpleNamespace(id=7), db=db)
            self.assertFalse(avatar.exists())
            self.assertEqual((avatars / '7.png').read_bytes(), b'new image')
            self.assertEqual(row.avatar_path, '7.png')
            db.commit.assert_awaited_once()

    async def test_missing_approved_avatar_returns_default_image(self):
        result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(avatar_path="7.png"))
        db = SimpleNamespace(execute=AsyncMock(return_value=result))
        with tempfile.TemporaryDirectory() as temporary, patch.object(profile.settings, "data_dir", Path(temporary)), patch.object(profile, "_check_profile_access", AsyncMock(return_value=(SimpleNamespace(username="Alex"), None))):
            response = await profile.get_avatar(7, db=db, current_user=None)
        self.assertEqual(response.media_type, "image/svg+xml")
        self.assertIn(b">A</text>", response.body)

    async def test_existing_avatar_is_still_served(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            avatars = root / "avatars"
            avatars.mkdir()
            (avatars / "7.png").write_bytes(b"image")
            result = SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(avatar_path="7.png"))
            db = SimpleNamespace(execute=AsyncMock(return_value=result))
            with patch.object(profile.settings, "data_dir", root), patch.object(profile, "_check_profile_access", AsyncMock(return_value=(SimpleNamespace(username="Alex"), None))):
                response = await profile.get_avatar(7, db=db, current_user=None)
            self.assertEqual(response.media_type, "image/png")
