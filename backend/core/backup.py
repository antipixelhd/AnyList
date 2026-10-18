import gzip
import hashlib
import io
import json
import re
import struct
import tempfile
from pathlib import Path

import asyncpg

from core.config import settings

_AVATAR_NAME = re.compile(r"^[1-9][0-9]*\.(?:jpg|png|webp)$")
_MAX_AVATAR_BYTES = 5 * 1024 * 1024


AVATAR_WRITE_LOCK_SQL = "SELECT pg_advisory_xact_lock(1935897199, 1)"
AVATAR_RESTORE_LOCK_SQL = "SELECT pg_advisory_lock(1935897199, 1)"


def avatar_backup_files(avatar_dir: Path, rows: list) -> dict[str, bytes]:
    """Only include files referenced by a profile in the database snapshot."""
    files = {}
    for row in rows:
        name = row["avatar_path"]
        if not name or not _AVATAR_NAME.fullmatch(name) or not name.startswith(f'{row["user_id"]}.'):
            continue
        path = avatar_dir / name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_AVATAR_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue  # An upload/removal may race with the backup.
        if len(data) <= _MAX_AVATAR_BYTES:
            files[name] = data
    return files


def write_backup_entry(output, name: str, data: bytes) -> None:
    name_bytes = name.encode("utf-8")
    output.write(struct.pack(">H", len(name_bytes)))
    output.write(name_bytes)
    output.write(struct.pack(">Q", len(data)))
    output.write(data)


def backup_avatar_manifest(files: dict[str, bytes]) -> list[dict[str, str]]:
    return [{"name": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()]


def _read_entry(raw: bytes, offset: int) -> tuple[str, bytes, int]:
    if len(raw) - offset < 2:
        raise ValueError("Invalid backup file.")
    name_len = struct.unpack_from(">H", raw, offset)[0]
    offset += 2
    if len(raw) - offset < name_len + 8:
        raise ValueError("Invalid backup file.")
    try:
        name = raw[offset:offset + name_len].decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Invalid backup file.") from None
    offset += name_len
    data_len = struct.unpack_from(">Q", raw, offset)[0]
    offset += 8
    if data_len > len(raw) - offset:
        raise ValueError("Invalid backup file.")
    return name, raw[offset:offset + data_len], offset + data_len


def parse_backup(content: bytes) -> tuple[list[str], dict[str, bytes], dict[str, bytes]]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as gz:
            raw = gz.read()
        if len(raw) < 4:
            raise ValueError
        header_len = struct.unpack_from(">I", raw)[0]
        if header_len > len(raw) - 4:
            raise ValueError
        header = json.loads(raw[4:4 + header_len])
        version = header["version"]
        tables = header["tables"]
        avatars = header.get("avatars", []) if version == 2 else []
        if version not in (1, 2) or not isinstance(tables, list) or not all(isinstance(t, str) for t in tables) or len(tables) != len(set(tables)) or not isinstance(avatars, list):
            raise ValueError
        offset = 4 + header_len
        table_data = {}
        for expected in tables:
            name, data, offset = _read_entry(raw, offset)
            if name != expected:
                raise ValueError
            table_data[name] = data
        avatar_data = {}
        for item in avatars:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not _AVATAR_NAME.fullmatch(item["name"]):
                raise ValueError
            name, data, offset = _read_entry(raw, offset)
            if name != item["name"] or name in avatar_data or len(data) > _MAX_AVATAR_BYTES or hashlib.sha256(data).hexdigest() != item.get("sha256"):
                raise ValueError
            avatar_data[name] = data
        if offset != len(raw):
            raise ValueError
        return tables, table_data, avatar_data
    except (OSError, EOFError, struct.error, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("Invalid backup file.") from None


def _topological_sort(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for child, parent in edges:
        if child in deps and parent in deps:
            deps[child].add(parent)
    result: list[str] = []
    seen: set[str] = set()

    def visit(t: str) -> None:
        if t in seen:
            return
        seen.add(t)
        for p in deps.get(t, set()):
            visit(p)
        result.append(t)

    for t in tables:
        visit(t)
    return result


async def asyncpg_conn() -> asyncpg.Connection:
    dsn = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.connect(dsn)


async def restore_backup(content: bytes) -> None:
    backup_tables, table_data, avatar_data = parse_backup(content)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".avatar-restore-", dir=settings.data_dir) as temporary:
        temporary_dir = Path(temporary)
        staged_avatars = temporary_dir / "new"
        staged_avatars.mkdir()
        for name, data in avatar_data.items():
            (staged_avatars / name).write_bytes(data)

        conn = await asyncpg_conn()
        try:
            # A session lock survives the DB commit until the avatar directory is swapped.
            await conn.execute(AVATAR_RESTORE_LOCK_SQL)
            # Backup headers are untrusted; only interpolate names from pg_tables.
            schema_rows = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            valid_tables = {r["tablename"] for r in schema_rows}
            tables = [t for t in backup_tables if t in valid_tables]
            if not tables:
                raise ValueError("Backup contains no tables from this database.")

            fk_rows = await conn.fetch(
                """
                SELECT c.relname AS child, p.relname AS parent
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_class p ON p.oid = con.confrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE con.contype = 'f' AND n.nspname = 'public'
                """
            )
            edges = [(r["child"], r["parent"]) for r in fk_rows]
            ordered = _topological_sort(tables, edges)

            async with conn.transaction():
                table_list = ", ".join(f'"{t}"' for t in ordered)
                await conn.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE")
                for table in ordered:
                    await conn.copy_to_table(table, source=io.BytesIO(table_data[table]), format="binary")

            seq_rows = await conn.fetch(
                """
                SELECT s.relname AS seq, a.attname AS col, c.relname AS tbl
                FROM pg_class s
                JOIN pg_depend d ON d.objid = s.oid
                JOIN pg_class c ON c.oid = d.refobjid
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
                JOIN pg_namespace n ON n.oid = s.relnamespace
                WHERE s.relkind = 'S' AND n.nspname = 'public'
                """
            )
            for row in seq_rows:
                await conn.execute(
                    f'SELECT setval($1, COALESCE((SELECT MAX("{row["col"]}") FROM "{row["tbl"]}"), 1), true)',
                    row["seq"],
                )
            avatars_dir = settings.data_dir / "avatars"
            previous_avatars = temporary_dir / "old"
            if avatars_dir.exists():
                avatars_dir.rename(previous_avatars)
            try:
                staged_avatars.rename(avatars_dir)
            except OSError:
                if previous_avatars.exists():
                    previous_avatars.rename(avatars_dir)
                raise
        finally:
            await conn.close()
