"""Pack each completed UTC hour of spooled snapshots into a verified .tar.zst.

Spool files are deleted only after the archive has been written, re-read,
and every member's sha256 matches the original file.
"""

import hashlib
import io
import logging
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import zstandard

from . import health
from .config import Settings
from .storage import TMP_PREFIX, fsync_dir

log = logging.getLogger(__name__)

ZSTD_LEVEL = 19
# Don't pack an hour until this long after it ends, so in-flight writes finish.
GRACE = timedelta(minutes=2)


class VerifyError(Exception):
    pass


def _hour_start(date_dir: Path, hour_dir: Path) -> datetime | None:
    try:
        return datetime.strptime(f"{date_dir.name} {hour_dir.name}", "%Y-%m-%d %H").replace(tzinfo=UTC)
    except ValueError:
        return None


def archive_hashes(path: Path) -> dict[str, str]:
    """Map member name to sha256 for every file in a .tar.zst archive."""
    hashes = {}
    with open(path, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if member.isfile():
                    hashes[member.name] = hashlib.sha256(tar.extractfile(member).read()).hexdigest()
    return hashes


def _write_archive(path: Path, files: list[Path]) -> None:
    tmp = path.with_name(TMP_PREFIX + path.name)
    cctx = zstandard.ZstdCompressor(level=ZSTD_LEVEL, write_checksum=True)
    with open(tmp, "wb") as raw:
        with cctx.stream_writer(raw, closefd=False) as zw:
            with tarfile.open(fileobj=zw, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for f in files:
                    data = f.read_bytes()
                    # Only name, size, and mtime: no local user or group names.
                    info = tarfile.TarInfo(f.name)
                    info.size = len(data)
                    info.mtime = int(f.stat().st_mtime)
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
        raw.flush()
        os.fsync(raw.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def _archive_path(archive_dir: Path, feed: str, hour: datetime, n: int) -> Path:
    suffix = "" if n == 0 else f".{n}"
    return archive_dir / feed / f"{hour:%Y/%m/%d}" / f"{feed}-{hour:%Y-%m-%dT%H}{suffix}.tar.zst"


def pack_hour(settings: Settings, feed: str, hour: datetime, hour_dir: Path) -> int:
    """Pack one spool hour directory. Returns the number of files archived."""
    # Leftover temp files are interrupted writes; they never completed.
    for tmp in hour_dir.glob(TMP_PREFIX + "*"):
        tmp.unlink()
    files = sorted(p for p in hour_dir.iterdir() if p.is_file())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

    # An earlier run may have archived some of these and then been interrupted,
    # or late files may have arrived. Anything already archived intact is done;
    # the rest goes into the next numbered archive for the hour.
    n = 0
    while (existing := _archive_path(settings.archive_dir, feed, hour, n)).exists():
        for name, h in archive_hashes(existing).items():
            if hashes.get(name) == h:
                (hour_dir / name).unlink()
                del hashes[name]
        n += 1
    files = [p for p in files if p.name in hashes]

    packed = 0
    if files:
        path = _archive_path(settings.archive_dir, feed, hour, n)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_archive(path, files)
        got = archive_hashes(path)
        if got != hashes:
            path.unlink()
            raise VerifyError(f"{path}: archive contents don't match spool; spool kept")
        for p in files:
            p.unlink()
        packed = len(files)
        log.info("packed %d files into %s (%d bytes)", packed, path, path.stat().st_size)

    hour_dir.rmdir()
    return packed


def pack_completed(settings: Settings, now: datetime | None = None) -> int:
    """Pack every spool hour that ended at least GRACE ago. Returns files packed."""
    now = now or datetime.now(UTC)
    total = 0
    if not settings.spool_dir.exists():
        return 0
    for feed_dir in sorted(p for p in settings.spool_dir.iterdir() if p.is_dir()):
        for date_dir in sorted(p for p in feed_dir.iterdir() if p.is_dir()):
            for hour_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
                hour = _hour_start(date_dir, hour_dir)
                if hour is None:
                    log.warning("skipping unexpected directory %s", hour_dir)
                    continue
                if hour + timedelta(hours=1) + GRACE > now:
                    continue
                total += pack_hour(settings, feed_dir.name, hour, hour_dir)
            if not any(date_dir.iterdir()):
                date_dir.rmdir()
    return total


def run(settings: Settings) -> bool:
    try:
        n = pack_completed(settings)
    except Exception as e:
        log.exception("pack failed")
        health.ping(settings, "pack", fail=True, message=f"{type(e).__name__}: {e}")
        return False
    health.ping(settings, "pack", message=f"packed {n} files")
    return True
