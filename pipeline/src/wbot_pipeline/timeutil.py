"""Service dates and GTFS times. Everything is timezone-aware (America/Los_Angeles)."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import BANDS

TZ = ZoneInfo("America/Los_Angeles")


def origin(d: date) -> int:
    """GTFS time origin for a service date: noon minus 12 h, local time (correct across DST)."""
    noon = datetime.combine(d, time(12), tzinfo=TZ)
    return int(noon.timestamp()) - 12 * 3600


def day_end(d: date) -> int:
    """Unix time of local midnight at the end of the date."""
    return int(datetime.combine(d + timedelta(days=1), time(0), tzinfo=TZ).timestamp())


def gtfs_seconds(hms: str) -> int | None:
    """'25:10:00' -> 90600. Blank -> None."""
    hms = (hms or "").strip()
    if not hms:
        return None
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def daytype(d: date) -> str:
    return {5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def band(hour: int) -> str:
    return next(key for key, lo, hi in BANDS if lo <= hour < hi)


def yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_yyyymmdd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def today_local(now: int) -> date:
    return datetime.fromtimestamp(now, TZ).date()


_SNAPSHOT_NAME = re.compile(r"(\d{8}T\d{6})Z")


def snapshot_time(name: str) -> int | None:
    """Fetch time from a collector file name such as tripupdates-20261004T032400Z.pb."""
    m = _SNAPSHOT_NAME.search(name)
    if not m:
        return None
    return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC).timestamp())
