"""JSONL storage — append-only record store for cases.

Cross-process safe via a sidecar file lock (`.lock` suffix). Uses
`msvcrt.locking` on Windows and `fcntl.flock` on POSIX.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Iterator


# --------------------------------------------------------------------------- #
# Cross-platform file lock
# --------------------------------------------------------------------------- #
if sys.platform == "win32":
    import msvcrt  # type: ignore[import-not-found]

    def _acquire_lock(f) -> None:
        # LK_LOCK blocks up to ~10s then raises; sufficient for short ops.
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _release_lock(f) -> None:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            # Already released or file closed — ignore.
            pass
else:
    import fcntl  # type: ignore[import-not-found]

    def _acquire_lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _release_lock(f) -> None:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class JsonlStore:
    """Append-only JSON Lines store.

    Each line is one JSON object. Suitable for badcase / goodcase collection
    where records are written often and read in batch for post-training.

    Concurrency:
      - Per-instance `threading.Lock` guards in-process threads.
      - A sidecar `<path>.lock` file with OS-level locking guards across
        processes (multiple API workers on the same data dir).
      - `read_modify_write` is the atomic primitive for operations that
        need to read-all → mutate → rewrite (e.g. dedup merging).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self._lock = Lock()
        # Sidecar lock file — exists for the lifetime of the store, opened
        # on demand inside `_cross_process_lock`.
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _cross_process_lock(self):
        """Acquire the cross-process file lock for the duration of a with-block."""
        # Open the lock file in binary append mode so it's created if missing.
        f = open(self._lock_path, "a+b")
        try:
            _acquire_lock(f)
            yield
        finally:
            _release_lock(f)
            f.close()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock, self._cross_process_lock():
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def iter_records(self) -> Iterator[dict[str, Any]]:
        # Reading doesn't strictly need the lock; append-only writes never
        # shrink the file mid-read, and we tolerate the rare partial-last-line
        # by catching JSONDecodeError below. For read-modify-write flows,
        # callers should use `read_modify_write` which DOES hold the lock.
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
        with self._lock, self._cross_process_lock():
            self.path.write_text("", encoding="utf-8")

    def read_modify_write(
        self,
        mutate_fn: "Any",
    ) -> list[dict[str, Any]]:
        """Atomically read all records, apply `mutate_fn`, rewrite the store.

        `mutate_fn(records: list[dict]) -> list[dict]` receives a copy of
        the current records and returns the new records to persist. The
        cross-process lock is held for the whole operation so concurrent
        writers can't interleave / lose data.

        Returns the persisted records (the return value of `mutate_fn`).
        """
        with self._lock, self._cross_process_lock():
            # Read all current records under the lock.
            records: list[dict[str, Any]] = []
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            # Apply the mutation.
            new_records = mutate_fn(records)
            # Rewrite the file atomically: write to a temp file then rename.
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                with tmp.open("w", encoding="utf-8") as f:
                    for rec in new_records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                # os.replace is atomic on both Windows and POSIX.
                tmp.replace(self.path)
            finally:
                # Clean up the temp file if the write or replace failed
                # (disk full / permission / etc.) so we don't leave
                # stale *.tmp files lying around. After a successful
                # replace, tmp no longer exists so this is a no-op.
                # See `optimization_logs/2026-07-21/second-review.md` P2-6.
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            return new_records
