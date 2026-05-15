from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def process_lock(lock_path: Path, *, blocking: bool = False) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        acquire_lock(handle, blocking=blocking)
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{lock_path.name}\n")
            handle.flush()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_lock(handle: TextIO, *, blocking: bool = False) -> None:
    try:
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(handle.fileno(), flags)
    except BlockingIOError as exc:
        raise RuntimeError("Another codex-obsidian-sync process is already running") from exc
