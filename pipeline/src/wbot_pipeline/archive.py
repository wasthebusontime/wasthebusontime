"""Reading the collector's hourly packs (paths from wbot_collector.layout). Archives are
opened read-only and never changed. An hour has a first pack and, when files for it arrived
after it was packed, later numbered ones. The spool (the current, unpacked hour) is never
read: it is still changing.
"""

import hashlib
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import zstandard
from wbot_collector import layout


def hour_start(ts: int) -> int:
    return ts - ts % 3600


def hours(start: int, end: int) -> list[int]:
    """UTC hour starts covering [start, end]."""
    return list(range(hour_start(start), hour_start(end) + 1, 3600))


def packs_for_hour(archive_dir: Path, feed: str, hour: int) -> list[Path]:
    """The pack and any later numbered packs for one UTC hour, in order."""
    return layout.packs_for_hour(archive_dir, feed, datetime.fromtimestamp(hour, UTC))


def unpacked_hours(spool_dir: Path, feed: str, hour_list: list[int]) -> set[int]:
    """The hours of hour_list that still have unpacked files in the spool."""
    out = set()
    for hour in hour_list:
        d = layout.spool_hour_dir(spool_dir, feed, datetime.fromtimestamp(hour, UTC))
        if d.is_dir() and any(d.iterdir()):
            out.add(hour)
    return out


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
        return layout.snapshot_time(name) or 0, name, data

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
