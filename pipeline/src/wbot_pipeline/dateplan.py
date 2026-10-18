"""The date plan: which service dates to process tonight, and which must wait. Touches no files;
the caller reads the day manifests, the archive and the spool, and passes what they say.

A date is processed when:
  new            it has no manifest yet
  old_version    its outputs were made by another events version, methodology version or
                 static version
  no_static, no_service
                 the last run found no static version, or no service; it is tried again
  packs_changed  the packs for its hours aren't the ones it was made from (late files,
                 a restore)
  recent         it is one of the last trailing_days dates, which are redone every night
  rebuild        every date is, on request
  requested      it was named on the command line
A date to process waits (hours_missing) until every hour it reads has ended, the collector
has had READY_AFTER_S to pack it, and none of those hours still has files in the spool.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from .config import EVENTS_VERSION

# Wait this long after the last hour ends before calling a date complete
# (the collector packs each hour two minutes after it ends).
READY_AFTER_S = 600


@dataclass(frozen=True)
class Day:
    """What the files say about one service date."""
    date: date
    manifest: dict | None     # its day manifest, None if it was never processed
    static_stem: str | None   # the static version that applies to it now
    packs: list[dict]         # packs now in the archive for the manifest's hours, as {path, size}


@dataclass(frozen=True)
class Entry:
    date: date
    process: bool             # False: wait for a later run
    reason: str


def stale_reason(day: Day, methodology_version: str) -> str | None:
    """Why a date's outputs are out of date, or None if they are current."""
    m = day.manifest
    if m is None:
        return "new"
    if (m.get("events_version") != EVENTS_VERSION or m.get("methodology_version") != methodology_version
            or day.static_stem != m.get("static", {}).get("stem")):
        return "old_version"
    if m["status"] != "ok":
        return m["status"]
    if day.packs != [{"path": p["path"], "size": p["size"]} for p in m["packs"]]:
        return "packs_changed"
    return None


def dates(days: list[Day], *, trailing_days: int, methodology_version: str,
          rebuild: bool = False) -> list[tuple[date, str]]:
    """(date, reason) for every date to process, of days (the candidate dates, in order)."""
    recent = {d.date for d in days[-trailing_days:]} if trailing_days else set()
    todo = []
    for day in days:
        reason = "rebuild" if rebuild else stale_reason(day, methodology_version)
        if reason is None and day.date in recent:
            reason = "recent"
        if reason:
            todo.append((day.date, reason))
    return todo


def entry(d: date, reason: str, hours: list[int] | None, unpacked: Collection[int], now: int) -> Entry:
    """Process d now, or wait. hours: the UTC hour starts it reads (None if it reads none: no
    static version or no service). unpacked: hours with files still in the spool."""
    if hours and (now < hours[-1] + 3600 + READY_AFTER_S or any(h in unpacked for h in hours)):
        return Entry(d, False, "hours_missing")
    return Entry(d, True, reason)
