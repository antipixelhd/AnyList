"""Read export archives with limits on actual decompressed bytes."""

from zipfile import BadZipFile, ZipFile

MAX_ENTRY_SIZE = 100 * 1024 * 1024
MAX_TOTAL_SIZE = 500 * 1024 * 1024


class BoundedZipReader:
    def __init__(self, archive: ZipFile, *, max_entry_size: int, max_total_size: int, chunk_size: int):
        self.archive = archive
        self.max_entry_size = max_entry_size
        self.max_total_size = max_total_size
        self.chunk_size = chunk_size
        self.total_read = 0

    def read(self, name: str) -> bytes:
        chunks = []
        entry_read = 0
        try:
            with self.archive.open(name) as source:
                while chunk := source.read(self.chunk_size):
                    entry_read += len(chunk)
                    self.total_read += len(chunk)
                    if entry_read > self.max_entry_size or self.total_read > self.max_total_size:
                        raise ValueError("Export file is too large to import.")
                    chunks.append(chunk)
        except BadZipFile as exc:
            raise ValueError(f"'{name}' in the export is corrupted or inconsistent.") from exc
        return b"".join(chunks)
