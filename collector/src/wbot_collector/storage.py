"""Atomic file writes, the fetch log, and UTC time helpers."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

TMP_PREFIX = ".tmp-"


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsync_dir(path: Path) -> None:
    # Directory fsync makes the rename durable on Linux; Windows doesn't support it.
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes) -> None:
    """Write data so that path either doesn't exist or holds all of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(TMP_PREFIX + path.name)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def append_log(log_dir: Path, record: dict, ts: datetime) -> None:
    """Append one JSON line to the fetch log for the UTC day of ts."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{ts.astimezone(UTC):%Y-%m-%d}.jsonl"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
