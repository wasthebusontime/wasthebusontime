"""The data directory's layout: where the collector puts each file, and what the fetch log records.

The collector writes this layout and the pipeline reads it; both take the names from here, so
each is written once. The layout is fixed (the 2026-10-03 storage decisions). Standard library
only: the pipeline imports this module without the collector's own dependencies.

  spool/{feed}/YYYY-MM-DD/HH/{feed}-YYYYMMDDTHHMMSSZ{ext}    the current hour, unpacked
                                 (-1, -2, ... before ext after a restart within the second)
  archive/{feed}/YYYY/MM/DD/{feed}-YYYY-MM-DDTHH.tar.zst     each completed hour, packed
                                 (.1, .2, ... before .tar.zst when files arrived after packing)
  log/YYYY-MM-DD.jsonl                                       the fetch log: one FetchRecord per attempt
  static/{feed_version}_{sha8}.zip and .json                 each static GTFS version and its Sidecar
  state/                                                     the collector's own state
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TypedDict

SPOOL = "spool"
ARCHIVE = "archive"
LOG = "log"
STATIC = "static"
STATE = "state"

# Feed names, as they appear in paths and in the fetch log.
TRIPUPDATES = "tripupdates"
VEHICLEPOSITIONS = "vehiclepositions"
ALERTS = "alerts"
WSDOT_TRAVELTIMES = "wsdot_traveltimes"
WSDOT_ALERTS = "wsdot_alerts"
# The fetch log's name for the static GTFS checks.
STATIC_FEED = "static"


def iso(ts: datetime) -> str:
    """A UTC time as the fetch log and sidecars write it: 2026-10-04T03:24:00.000Z."""
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Spool


def spool_hour_dir(spool_dir: Path, feed: str, hour: datetime) -> Path:
    hour = hour.astimezone(UTC)
    return spool_dir / feed / f"{hour:%Y-%m-%d}" / f"{hour:%H}"


def spool_path(spool_dir: Path, feed: str, ts: datetime, ext: str = ".pb", n: int = 0) -> Path:
    """Where a snapshot fetched at ts goes; n > 0 for a second snapshot in the same second."""
    ts = ts.astimezone(UTC)
    suffix = f"-{n}" if n else ""
    return spool_hour_dir(spool_dir, feed, ts) / f"{feed}-{ts:%Y%m%dT%H%M%SZ}{suffix}{ext}"


def spool_hour(date_dir: Path, hour_dir: Path) -> datetime | None:
    """The UTC hour a spool directory holds, or None if the names aren't a date and an hour."""
    try:
        return datetime.strptime(f"{date_dir.name} {hour_dir.name}", "%Y-%m-%d %H").replace(tzinfo=UTC)
    except ValueError:
        return None


_SNAPSHOT_NAME = re.compile(r"(\d{8}T\d{6})Z")


def snapshot_time(name: str) -> int | None:
    """Fetch time (unix seconds) from a snapshot's file name, such as tripupdates-20261004T032400Z.pb."""
    m = _SNAPSHOT_NAME.search(name)
    if not m:
        return None
    return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp())


# Archive


def pack_path(archive_dir: Path, feed: str, hour: datetime, n: int = 0) -> Path:
    """The hour's first pack (n = 0), or a later one for files that arrived after packing."""
    hour = hour.astimezone(UTC)
    suffix = f".{n}" if n else ""
    return archive_dir / feed / f"{hour:%Y/%m/%d}" / f"{feed}-{hour:%Y-%m-%dT%H}{suffix}.tar.zst"


_PACK = re.compile(r"-(\d{4}-\d{2}-\d{2}T\d{2})(?:\.(\d+))?\.tar\.zst$")


def packs_for_hour(archive_dir: Path, feed: str, hour: datetime) -> list[Path]:
    """The hour's packs that exist, first pack first."""
    hour = hour.astimezone(UTC)
    found = []
    for p in pack_path(archive_dir, feed, hour).parent.glob(f"{feed}-{hour:%Y-%m-%dT%H}*.tar.zst"):
        m = _PACK.search(p.name)
        if m and m.group(1) == f"{hour:%Y-%m-%dT%H}":
            found.append((int(m.group(2) or 0), p))
    return [p for _, p in sorted(found)]


# Fetch log


def log_path(log_dir: Path, day: date) -> Path:
    """The fetch log for one UTC day."""
    return log_dir / f"{day.isoformat()}.jsonl"


class FetchRecord(TypedDict):
    """One fetch of a real-time feed, as a line of the fetch log."""
    fetched_at: str          # iso()
    feed: str
    url: str                 # without the access code
    status: int | None       # HTTP status; None if there was no response
    bytes: int | None
    elapsed_ms: int | None
    sha256: str | None       # of the spooled snapshot
    header_ts: int | None    # FeedHeader.timestamp
    data_ts: int | None      # newest vehicle or trip update timestamp
    entities: int | None     # None if the response didn't parse
    path: str | None         # the spooled snapshot, relative to the data directory
    error: str | None


def fetch_record(fetched_at: datetime, feed: str, url: str, **fields) -> FetchRecord:
    """A fetch log record, every field not given None."""
    record = dict.fromkeys(FetchRecord.__annotations__)
    record.update(fetched_at=iso(fetched_at), feed=feed, url=url)
    unknown = fields.keys() - record.keys()
    if unknown:
        raise TypeError(f"not fetch log fields: {', '.join(sorted(unknown))}")
    record.update(fields)
    return record


class StaticFetchRecord(TypedDict):
    """One check of the static GTFS feed, as a line of the fetch log (feed STATIC_FEED)."""
    fetched_at: str
    feed: str
    url: str
    final_url: str           # after redirects
    status: int
    bytes: int
    elapsed_ms: int
    sha256: str | None
    path: str | None         # the saved zip, relative to the data directory; None if not new
    error: str | None


# Static GTFS


def static_stem(feed_version: str, sha256: str) -> str:
    """A version's file name without the extension: feed_version (made safe) and the hash's first 8."""
    version = re.sub(r"[^A-Za-z0-9._-]", "_", feed_version) or "noversion"
    return f"{version}_{sha256[:8]}"


def static_zip(static_dir: Path, stem: str) -> Path:
    return static_dir / f"{stem}.zip"


def sidecar_path(zip_path: Path) -> Path:
    return zip_path.with_suffix(".json")


def static_zips(static_dir: Path) -> list[Path]:
    """Every saved static version, by name."""
    return sorted(static_dir.glob("*.zip"))


class Sidecar(TypedDict):
    """What the collector knew when it saved a static zip."""
    fetched_at: str          # iso()
    url: str
    final_url: str
    sha256: str              # of the zip
    bytes: int
    etag: str | None
    last_modified: str | None
    feed_info: dict[str, str]  # feed_info.txt's first row, or {}
