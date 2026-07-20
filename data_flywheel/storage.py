"""JSONL storage — append-only record store for cases."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Iterator


class JsonlStore:
    """Append-only JSON Lines store.

    Each line is one JSON object. Suitable for badcase / goodcase collection
    where records are written often and read in batch for post-training.

    Concurrency: a per-instance lock guards `append` / `clear` so concurrent
    threads writing to the same file don't interleave bytes. Cross-process
    safety is NOT guaranteed — run only one API process per data dir.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        # Reading doesn't need the lock; append-only writes never shrink the
        # file mid-read, and we tolerate the rare partial-last-line by
        # catching JSONDecodeError below.
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Skip corrupt / partially-written trailing line.
                    continue

    def count(self) -> int:
        n = 0
        for _ in self.iter_records():
            n += 1
        return n

    def clear(self) -> None:
        with self._lock:
            self.path.write_text("", encoding="utf-8")
