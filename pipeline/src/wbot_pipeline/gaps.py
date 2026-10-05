"""Gaps in collection, from the collector's fetch log (log/YYYY-MM-DD.jsonl).

A gap is a stretch between two good TripUpdates fetches that is longer than the
polling allows, while at least one trip is scheduled to be running (gaps when no bus
runs lose nothing). Good means HTTP 200 and parsed, and, while trips are running,
trip updates fresher than STALE_AFTER_S (finished trips linger in the feed with old
timestamps, so staleness means nothing between trips). Every gap over a minute
is recorded; whether it lost data depends on its length (gap_tolerance_s, applied
later), because passed stops stay in the feed about 15 minutes.

Cause, from what the log shows between the two good fetches:
  feed       the server answered with an error, or with stale data
  collector  no attempts were logged, or they failed to connect (our side)
  unknown    anything else (for example timeouts)
gap_causes.toml (next to this module) can set the cause and a note for known gaps.
"""

import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import timeutil
from .config import POLL_S, STALE_AFTER_S

MIN_GAP_S = 60
OVERRIDES = Path(__file__).with_name("gap_causes.toml")


def read_fetches(log_dir: Path, start: int, end: int, feed: str = "tripupdates") -> list[dict]:
    """Fetch log lines for one feed between start and end (unix seconds), in time order."""
    rows = []
    day = datetime.fromtimestamp(start, UTC).date() - timedelta(days=1)
    last = datetime.fromtimestamp(end, UTC).date()
    while day <= last:
        path = log_dir / f"{day.isoformat()}.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if f'"{feed}"' not in line:
                        continue
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if r.get("feed") != feed:
                        continue
                    r["ts"] = timeutil.parse_iso_utc(r["fetched_at"])
                    if start - 3600 <= r["ts"] <= end + 3600:
                        rows.append(r)
        day += timedelta(days=1)
    rows.sort(key=lambda r: r["ts"])
    return rows


def merge(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _inside(ts: int, service: list[tuple[int, int]]) -> bool:
    return any(a <= ts <= b for a, b in service)


def classify(r: dict, service: list[tuple[int, int]] = ()) -> str:
    """'good', 'feed' (server error or stale data), 'collector' (couldn't connect), 'unknown'."""
    status, error = r.get("status"), r.get("error")
    if status == 200 and not error and r.get("entities") is not None:
        data_ts = r.get("data_ts")
        if data_ts and r["ts"] - data_ts > STALE_AFTER_S and _inside(r["ts"], service):
            return "feed"
        return "good"
    if status is not None and status != 200:
        return "feed"
    if status == 200:
        return "feed"           # answered, but the bytes didn't parse
    text = (error or "").lower()
    if "timeout" in text or "timed out" in text:
        return "unknown"
    return "collector"


def find_gaps(fetches: list[dict], service: list[tuple[int, int]]) -> list[dict]:
    """Gaps that overlap the service intervals (merged, when trips are scheduled to run).
    Each runs from the first missed poll after a good fetch (or the start of service)
    to the next good fetch (or the end of service)."""
    if not service:
        return []
    start, end = service[0][0], service[-1][1]
    kinds = [(r["ts"], classify(r, service)) for r in fetches]
    good = sorted(t for t, k in kinds if k == "good")
    before = [t for t in good if t <= start]
    after = [t for t in good if t >= end]
    points = [before[-1] if before else start - POLL_S]
    points += [t for t in good if start < t < end]
    points.append(after[0] if after else end)
    gaps = []
    for a, b in zip(points, points[1:]):
        if b - a <= MIN_GAP_S or not any(a < hi and lo < b for lo, hi in service):
            continue
        between = [k for t, k in kinds if a < t < b]
        if any(c == "feed" for c in between):
            cause = "feed"
        elif all(c == "collector" for c in between):
            cause = "collector"
        else:
            cause = "unknown"
        gaps.append({"start": a + POLL_S, "end": b, "cause": cause})
    return gaps


def lost_seconds(gaps: list[dict], service: list[tuple[int, int]]) -> int:
    """Seconds of scheduled service inside gaps."""
    return sum(max(0, min(g["end"], b) - max(g["start"], a)) for g in gaps for a, b in service)


def load_overrides(path: Path = OVERRIDES) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    return [{"start": timeutil.parse_iso_utc(g["start"]), "end": timeutil.parse_iso_utc(g["end"]),
             "cause": g["cause"], "note": g.get("note", "")} for g in doc.get("gap", [])]


def apply_overrides(gaps: list[dict], overrides: list[dict]) -> list[dict]:
    out = []
    for g in gaps:
        g = dict(g)
        for o in overrides:
            if g["start"] < o["end"] and o["start"] < g["end"]:
                g["cause"] = o["cause"]
                if o["note"]:
                    g["note"] = o["note"]
        out.append(g)
    return out
