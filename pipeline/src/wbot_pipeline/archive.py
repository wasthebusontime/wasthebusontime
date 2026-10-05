"""Reading the collector's hourly packs. Archives are opened read-only and never changed.

Layout: archive/{feed}/YYYY/MM/DD/{feed}-YYYY-MM-DDTHH.tar.zst, plus
...THH.1.tar.zst (and .2, ...) when files for an hour arrived after it was packed.
The spool (the current, unpacked hour) is never read: it is still changing.
"""

import hashlib
import re
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import zstandard

from . import timeutil

_PACK = re.compile(r"-(\d{4}-\d{2}-\d{2}T\d{2})(?:\.(\d+))?\.tar\.zst$")


def hour_start(ts: int) -> int:
    return ts - ts % 3600


def hours(start: int, end: int) -> list[int]:
    """UTC hour starts covering [start, end]."""
    return list(range(hour_start(start), hour_start(end) + 1, 3600))


def packs_for_hour(archive_dir: Path, feed: str, hour: int) -> list[Path]:
    """The pack and any later numbered packs for one UTC hour, in order."""
    h = datetime.fromtimestamp(hour, UTC)
    folder = archive_dir / feed / f"{h:%Y/%m/%d}"
    found = []
    for p in folder.glob(f"{feed}-{h:%Y-%m-%dT%H}*.tar.zst"):
        m = _PACK.search(p.name)
        if m and m.group(1) == f"{h:%Y-%m-%dT%H}":
            found.append((int(m.group(2) or 0), p))
    return [p for _, p in sorted(found)]


def spool_pending(spool_dir: Path, feed: str, hour_list: list[int]) -> bool:
    """True if any of these hours still has unpacked files in the spool."""
    for hour in hour_list:
        h = datetime.fromtimestamp(hour, UTC)
        d = spool_dir / feed / f"{h:%Y-%m-%d}" / f"{h:%H}"
        if d.is_dir() and any(d.iterdir()):
            return True
    return False


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _members(path: Path) -> Iterator[tuple[str, bytes]]:
    with open(path, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for m in tar:
                if m.isfile():
                    yield m.name, tar.extractfile(m).read()


def snapshots(packs: list[Path]) -> Iterator[tuple[int, str, bytes]]:
    """(fetch time, name, raw bytes) for every snapshot in one hour's packs, in time order.

    A single pack is streamed. When an hour has late packs, its snapshots are
    gathered and sorted first (one hour is at most a few tens of MB).
    """
    def item(name: str, data: bytes):
        return timeutil.snapshot_time(name) or 0, name, data

    if len(packs) == 1:
        for name, data in _members(packs[0]):
            yield item(name, data)
        return
    seen, items = set(), []
    for pack in packs:
        for name, data in _members(pack):
            if name not in seen:
                seen.add(name)
                items.append(item(name, data))
    items.sort(key=lambda x: (x[0], x[1]))
    yield from items
