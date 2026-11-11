"""Daily journal: every message from a source, exactly as received, appended to
<root>/journal/<YYYY-MM-DD>/<account>.jsonl.gz before anything else is done with it.
Gzip members are appended per batch and synced to disk, so a crash loses at most the
batch being written and the day can be replayed exactly."""
from __future__ import annotations

import gzip
import os
from datetime import datetime
from pathlib import Path


class Journal:
    def __init__(self, root: Path, account_key: str):
        self.root, self.key = Path(root), account_key
        self.day: str | None = None
        self.handle = None

    def path(self, day: str) -> Path:
        return self.root / 'journal' / day / f'{self.key}.jsonl.gz'

    def write(self, lines: list[bytes], now: datetime) -> None:
        if not lines:
            return
        day = now.strftime('%Y-%m-%d')
        if day != self.day:
            self.close()
            target = self.path(day)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            self.handle = open(target, 'ab')
            self.day = day
        data = gzip.compress(b''.join(line if line.endswith(b'\n') else line + b'\n' for line in lines), compresslevel=5)
        self.handle.write(data)
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        if self.handle:
            self.handle.close()
            self.handle = None
            self.day = None


def read(path: Path) -> list[bytes]:
    """All lines of a journal file (for replay and verification)."""
    with gzip.open(path, 'rb') as f:
        return f.read().splitlines()
