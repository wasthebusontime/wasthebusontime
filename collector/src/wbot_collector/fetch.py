"""The real-time poll loop: fetch each feed on schedule and spool the raw bytes."""

import logging
import math
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from . import health
from .config import (
    FEED_STALE_AFTER_S,
    FEEDS,
    HEARTBEAT_INTERVAL_S,
    HTTP_TIMEOUT_S,
    USER_AGENT,
    Feed,
    Settings,
)
from .storage import append_log, iso, sha256_hex, write_atomic

log = logging.getLogger(__name__)


def next_slot(now: float, interval_s: int, offset_s: int = 0) -> float:
    """First wall-clock time strictly after now on the feed's schedule.

    Missed slots are skipped rather than caught up, so a slow cycle never
    causes a burst of requests.
    """
    k = math.floor((now - offset_s) / interval_s) + 1
    return k * interval_s + offset_s


def summarize(content: bytes) -> dict:
    """Timestamps and entity count for the fetch log; all None if unparseable.

    header_ts is FeedHeader.timestamp. This server stamps it at request time,
    so it always advances. data_ts is the newest vehicle or trip_update
    timestamp, which shows whether the underlying data is still updating.
    """
    msg = gtfs_realtime_pb2.FeedMessage()
    try:
        msg.ParseFromString(content)
    except DecodeError:
        return {"header_ts": None, "data_ts": None, "entities": None}
    stamps = [
        e.vehicle.timestamp if e.HasField("vehicle") else e.trip_update.timestamp
        for e in msg.entity
        if e.HasField("vehicle") or e.HasField("trip_update")
    ]
    return {
        "header_ts": msg.header.timestamp if msg.header.HasField("timestamp") else None,
        "data_ts": max(stamps) if any(stamps) else None,
        "entities": len(msg.entity),
    }


def spool_path(spool_dir: Path, feed: str, ts: datetime) -> Path:
    ts = ts.astimezone(UTC)
    hour_dir = spool_dir / feed / f"{ts:%Y-%m-%d}" / f"{ts:%H}"
    path = hour_dir / f"{feed}-{ts:%Y%m%dT%H%M%SZ}.pb"
    n = 1
    while path.exists():  # Only possible after a restart within the same second.
        path = hour_dir / f"{feed}-{ts:%Y%m%dT%H%M%SZ}-{n}.pb"
        n += 1
    return path


def fetch_one(client: httpx.Client, feed: Feed, settings: Settings, now: datetime) -> dict:
    """Fetch one feed, spool the response unmodified, and log the attempt."""
    record = {
        "fetched_at": iso(now),
        "feed": feed.name,
        "url": feed.url,
        "status": None,
        "bytes": None,
        "elapsed_ms": None,
        "sha256": None,
        "header_ts": None,
        "data_ts": None,
        "entities": None,
        "path": None,
        "error": None,
    }
    t0 = time.monotonic()
    try:
        resp = client.get(feed.url)
        record["status"] = resp.status_code
        content = resp.content
        record["bytes"] = len(content)
        if resp.status_code != 200:
            record["error"] = f"HTTP {resp.status_code}"
        elif not content:
            record["error"] = "empty response"
        else:
            path = spool_path(settings.spool_dir, feed.name, now)
            write_atomic(path, content)
            record["sha256"] = sha256_hex(content)
            record.update(summarize(content))
            record["path"] = path.relative_to(settings.data_dir).as_posix()
    except httpx.HTTPError as e:
        record["error"] = f"{type(e).__name__}: {e}"
    finally:
        record["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    append_log(settings.log_dir, record, now)
    if record["error"]:
        log.warning("%s: %s", feed.name, record["error"])
    return record


class Heartbeat:
    """Pings 'collector' while TripUpdates fetches succeed, and 'feed_stale'
    while TripUpdates data is fresh.

    An empty feed counts as fresh: buses don't run overnight, and that isn't
    an outage.
    """

    def __init__(self, settings: Settings, start: float):
        self.settings = settings
        self.last_success = 0.0
        self.last_fresh = 0.0
        self.next_ping = start

    def record(self, rec: dict, now: float) -> None:
        if rec["feed"] != "tripupdates" or rec["error"]:
            return
        self.last_success = now
        if rec["entities"] == 0 or (rec["data_ts"] and now - rec["data_ts"] < FEED_STALE_AFTER_S):
            self.last_fresh = now

    def maybe_ping(self, now: float) -> None:
        # Wait for the first successful TripUpdates fetch rather than spending
        # the first slot on a cycle that only fetched Alerts.
        if now < self.next_ping or not self.last_success:
            return
        self.next_ping = now + HEARTBEAT_INTERVAL_S
        if now - self.last_success < HEARTBEAT_INTERVAL_S:
            health.ping(self.settings, "collector")
        if now - self.last_fresh < HEARTBEAT_INTERVAL_S:
            health.ping(self.settings, "feed_stale")


def run(settings: Settings, feeds: tuple[Feed, ...] = FEEDS, max_cycles: int | None = None) -> None:
    """Poll forever (or for max_cycles scheduling rounds) until SIGTERM/SIGINT."""
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    start = time.time()
    due = {f.name: next_slot(start, f.interval_s, f.offset_s) for f in feeds}
    heartbeat = Heartbeat(settings, start)
    log.info("collector started; data dir %s", settings.data_dir)

    cycles = 0
    with httpx.Client(
        timeout=HTTP_TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        while not stop.is_set():
            wait = min(due.values()) - time.time()
            if wait > 0 and stop.wait(wait):
                break
            for feed in feeds:
                if stop.is_set() or time.time() < due[feed.name]:
                    continue
                now = time.time()
                rec = fetch_one(client, feed, settings, datetime.fromtimestamp(now, UTC))
                heartbeat.record(rec, now)
                due[feed.name] = next_slot(time.time(), feed.interval_s, feed.offset_s)
            heartbeat.maybe_ping(time.time())
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
    log.info("collector stopped")
