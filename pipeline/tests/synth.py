"""A small invented transit network and simulated feeds, built in code for the tests.

Nothing here comes from Intercity Transit's feeds: routes are "901" and "902", stops
are "Example St & 1st Ave" and so on, codes start at E201. The simulator writes the
collector's layout (static/, archive/ packs, log/) so the pipeline reads it exactly as
it reads the real archive.
"""

import io
import json
import tarfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import zstandard
from google.transit import gtfs_realtime_pb2 as R

from wbot_pipeline import timeutil

STOPS = [
    # stop_id, code, name, lat, lon
    ("S1", "E201", "Sample Transit Center", 47.0400, -122.9000),
    ("S2", "E202", "Example St & 1st Ave", 47.0400, -122.8950),
    ("S3", "E203", "Example St & 2nd Ave", 47.0400, -122.8900),
    ("S4", "E204", "Example St & 3rd Ave", 47.0400, -122.8850),
    ("S5", "E205", "Example Park & Ride", 47.0400, -122.8800),
    ("S6", "E206", "Sample Ave & 4th Ave", 47.0450, -122.8800),
    ("S7", "", "Sample Ave & 5th Ave", 47.0500, -122.8800),  # no public code: falls back to stop_id
]
MIN_BETWEEN = 4  # minutes between consecutive stops


@dataclass
class Trip:
    trip_id: str
    route_id: str
    direction_id: int
    service_id: str
    start_min: int                     # minutes after the GTFS origin (may exceed 24 h)
    stops: list[str]                   # stop_ids in order
    timepoints: set[int]               # indexes that are timepoints
    hold_at: dict[int, int] = field(default_factory=dict)  # index -> extra minutes held
    headsign: str = ""
    shape_id: str = ""

    def schedule(self) -> list[tuple[int, str, int, int, bool]]:
        """(stop_sequence, stop_id, arrival s, departure s, timepoint) per stop."""
        out, t = [], self.start_min * 60
        for i, stop_id in enumerate(self.stops):
            arr = t
            dep = arr + self.hold_at.get(i, 0) * 60
            out.append((i * 10, stop_id, arr, dep, i in self.timepoints))
            t = dep + MIN_BETWEEN * 60
        return out


def default_trips() -> list[Trip]:
    trips = []
    line = ["S1", "S2", "S3", "S4", "S5"]
    for k, start in enumerate([7 * 60, 7 * 60 + 30, 8 * 60, 8 * 60 + 30]):
        trips.append(Trip(f"901-0-{k}-v1", "R901", 0, "WK", start, line, {0, 2, 4}, {2: 1},
                          "Example Park & Ride", "SH901-0"))
        trips.append(Trip(f"901-1-{k}-v1", "R901", 1, "WK", start + 15, line[::-1], {0, 2, 4}, {},
                          "Sample Transit Center", "SH901-1"))
    # A loop that passes S6 twice and starts and ends at S5.
    loop = ["S5", "S6", "S7", "S6", "S5"]
    for k, start in enumerate([7 * 60 + 10, 8 * 60 + 10]):
        trips.append(Trip(f"902-0-{k}-v1", "R902", 0, "WK", start, loop, {0, 2, 4}, {}, "Loop", "SH902"))
    # After midnight: belongs to the previous service date.
    trips.append(Trip("901-0-late-v1", "R901", 0, "WK", 24 * 60 + 50, line, {0, 2, 4}, {},
                      "Example Park & Ride", "SH901-0"))
    # Saturday only.
    trips.append(Trip("901-0-sat-v1", "R901", 0, "SAT", 9 * 60, line, {0, 2, 4}, {},
                      "Example Park & Ride", "SH901-0"))
    return trips


def hms(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def write_gtfs(static_dir: Path, stem: str, trips: list[Trip], *, feed_version: str = "test1",
               start: str = "20261001", end: str = "20261231", fetched_at: str = "2026-10-01T10:30:00Z",
               calendar_dates: list[tuple[str, str, str]] = (), with_shapes: bool = True) -> Path:
    def table(header, rows):
        buf = io.StringIO()
        buf.write(",".join(header) + "\n")
        for r in rows:
            buf.write(",".join(str(x) for x in r) + "\n")
        return buf.getvalue()

    files = {
        "agency.txt": table(["agency_id", "agency_name", "agency_url", "agency_timezone"],
                            [["A", "Sample Transit", "https://example.invalid", "America/Los_Angeles"]]),
        "routes.txt": table(["route_id", "route_short_name", "route_long_name", "route_type"],
                            [["R901", "901", "Example St Crosstown", 3], ["R902", "902", "Sample Loop", 3]]),
        "stops.txt": table(["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"],
                           [[s[0], s[1], f'"{s[2]}"', s[3], s[4]] for s in STOPS]),
        "trips.txt": table(["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"],
                           [[t.route_id, t.service_id, t.trip_id, t.headsign, t.direction_id, t.shape_id]
                            for t in trips]),
        "stop_times.txt": table(
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint"],
            [[t.trip_id, hms(a), hms(dp), sid, seq, int(tp)] for t in trips for seq, sid, a, dp, tp in t.schedule()]),
        "calendar.txt": table(
            ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
             "start_date", "end_date"],
            [["WK", 1, 1, 1, 1, 1, 0, 0, start, end], ["SAT", 0, 0, 0, 0, 0, 1, 0, start, end]]),
        "calendar_dates.txt": table(["service_id", "date", "exception_type"], list(calendar_dates)),
        "feed_info.txt": table(["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date",
                                "feed_end_date", "feed_version"],
                               [["Sample Transit", "https://example.invalid", "en", start, end, feed_version]]),
    }
    if with_shapes:
        coords = {s[0]: (s[3], s[4]) for s in STOPS}
        rows = []
        for shape_id, stops in (("SH901-0", ["S1", "S2", "S3", "S4", "S5"]), ("SH901-1", ["S5", "S4", "S3", "S2", "S1"]),
                                ("SH902", ["S5", "S6", "S7", "S6", "S5"])):
            seq = 0
            for a, b in zip(stops, stops[1:]):
                (la1, lo1), (la2, lo2) = coords[a], coords[b]
                for f in (0, 0.25, 0.5, 0.75):  # straight segments: the middle points simplify away
                    rows.append([shape_id, round(la1 + (la2 - la1) * f, 6), round(lo1 + (lo2 - lo1) * f, 6), seq])
                    seq += 1
            la, lo = coords[stops[-1]]
            rows.append([shape_id, la, lo, seq])
        files["shapes.txt"] = table(["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"], rows)
    static_dir.mkdir(parents=True, exist_ok=True)
    zip_path = static_dir / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        for name, text in files.items():
            # A byte order mark on one file, as some feeds have.
            z.writestr(name, ("﻿" if name == "stops.txt" else "") + text)
    (static_dir / f"{stem}.json").write_text(json.dumps({
        "fetched_at": fetched_at, "sha256": stem, "feed_info": {
            "feed_start_date": start, "feed_end_date": end, "feed_version": feed_version}}))
    return zip_path


@dataclass
class TripPlan:
    """How one trip behaves in the simulated feed."""
    delays: list[int] | None = None    # actual departure minus scheduled, per stop (seconds)
    vehicle: bool = True
    cancel: bool = False
    skip: set[int] = field(default_factory=set)
    no_data: set[int] = field(default_factory=set)
    frozen_ts: bool = False            # trip_update.timestamp never advances past the start


class Feed:
    """Simulates TripUpdates for one service date and writes packs and log lines."""

    def __init__(self, data_dir: Path, d: date, trips: list[Trip]):
        self.data_dir, self.d, self.trips = data_dir, d, trips
        self.origin = timeutil.origin(d)
        self.plans: dict[str, TripPlan] = {}
        self.added: list[tuple[str, list[tuple[int, str, int]]]] = []  # trip_id, [(seq, stop_id, dep_ts)]
        self.gaps: list[tuple[int, int]] = []          # no fetch attempts at all (collector down)
        self.feed_errors: list[tuple[int, int]] = []   # HTTP 503 answers
        self.corrupt: set[int] = set()                 # snapshot times whose bytes don't parse

    def active(self) -> list[Trip]:
        wd = self.d.weekday()
        return [t for t in self.trips if (t.service_id == "WK" and wd < 5) or (t.service_id == "SAT" and wd == 5)]

    def actual(self, t: Trip) -> list[tuple[int, str, int, int, int, int]]:
        plan = self.plans.get(t.trip_id, TripPlan())
        out = []
        for i, (seq, sid, a, dp, _) in enumerate(t.schedule()):
            delay = plan.delays[i] if plan.delays else 30
            dep = self.origin + dp + delay
            arr = min(self.origin + a + delay, dep) - 20
            out.append((seq, sid, arr, dep, i, delay))
        return out

    def snapshot(self, snap: int) -> bytes:
        msg = R.FeedMessage()
        msg.header.gtfs_realtime_version = "2.0"
        msg.header.timestamp = snap
        for t in self.active():
            plan = self.plans.get(t.trip_id, TripPlan())
            stops = self.actual(t)
            start, end = stops[0][3], stops[-1][3]
            if not (start - 1200 <= snap <= end + 900):
                continue
            e = msg.entity.add()
            e.id = t.trip_id
            tu = e.trip_update
            tu.trip.trip_id = t.trip_id
            tu.trip.route_id = t.route_id
            tu.trip.start_date = timeutil.yyyymmdd(self.d)
            on_road = start - 300 <= snap <= end
            if plan.cancel:
                tu.trip.schedule_relationship = R.TripDescriptor.CANCELED
                tu.timestamp = snap - 3
                continue
            if plan.vehicle and on_road:
                tu.vehicle.id = "V" + t.trip_id
            tu.timestamp = (start - 300 if plan.frozen_ts else min(snap, end) - 3) if plan.vehicle else snap - 3
            for seq, sid, arr, dep, i, _ in stops:
                if snap > dep + 885:   # passed stops drop out after about 15 minutes
                    continue
                s = tu.stop_time_update.add()
                s.stop_sequence = seq
                s.stop_id = sid
                if i in plan.no_data:
                    s.schedule_relationship = R.TripUpdate.StopTimeUpdate.NO_DATA
                    continue
                if i in plan.skip:
                    s.schedule_relationship = R.TripUpdate.StopTimeUpdate.SKIPPED
                s.arrival.time = arr
                s.departure.time = dep
        for trip_id, stops in self.added:
            if not (stops[0][2] - 600 <= snap <= stops[-1][2] + 900):
                continue
            e = msg.entity.add()
            e.id = trip_id
            tu = e.trip_update
            tu.trip.trip_id = trip_id
            tu.trip.route_id = "R901"
            tu.trip.start_date = timeutil.yyyymmdd(self.d)
            tu.trip.schedule_relationship = R.TripDescriptor.ADDED
            tu.vehicle.id = "VX"
            tu.timestamp = snap - 3
            for seq, sid, dep in stops:
                if snap <= dep + 885:
                    s = tu.stop_time_update.add()
                    s.stop_sequence, s.stop_id = seq, sid
                    s.arrival.time, s.departure.time = dep - 20, dep
        return msg.SerializeToString()

    def times(self) -> list[int]:
        scheds = [self.actual(t) for t in self.active()]
        lo = min(s[0][3] for s in scheds) - 1800
        hi = max(s[-1][2] for s in scheds) + 1800
        lo -= lo % 30
        return list(range(lo, hi, 30))

    def write(self, late_hours: set[int] = frozenset()) -> None:
        """Write archive packs and log lines. Hours in late_hours get part of their
        snapshots in a second (.1) pack, as when files arrive after packing."""
        by_hour: dict[int, list[tuple[str, bytes]]] = {}
        log_lines = []
        for snap in self.times():
            fetched = datetime.fromtimestamp(snap, UTC)
            if any(a <= snap < b for a, b in self.gaps):
                continue
            line = {"fetched_at": fetched.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "feed": "tripupdates",
                    "url": "https://example.invalid/tu", "elapsed_ms": 100, "path": None}
            if any(a <= snap < b for a, b in self.feed_errors):
                log_lines.append({**line, "status": 503, "bytes": 0, "sha256": None, "header_ts": None,
                                  "data_ts": None, "entities": None, "error": "HTTP 503"})
                continue
            data = b"\x00not a protobuf\xff" if snap in self.corrupt else self.snapshot(snap)
            name = f"tripupdates-{fetched:%Y%m%dT%H%M%S}Z.pb"
            by_hour.setdefault(snap - snap % 3600, []).append((name, data))
            parsed = snap not in self.corrupt
            msg = R.FeedMessage()
            if parsed:
                msg.ParseFromString(data)
            data_ts = max((e.trip_update.timestamp for e in msg.entity), default=None) if parsed else None
            log_lines.append({**line, "status": 200, "bytes": len(data), "sha256": "x", "header_ts": snap,
                              "data_ts": data_ts, "entities": len(msg.entity) if parsed else None, "error": None})
        for hour, items in by_hour.items():
            h = datetime.fromtimestamp(hour, UTC)
            folder = self.data_dir / "archive" / "tripupdates" / f"{h:%Y/%m/%d}"
            folder.mkdir(parents=True, exist_ok=True)
            parts = [items] if hour not in late_hours else [items[::2], items[1::2]]
            for n, part in enumerate(parts):
                suffix = f".{n}" if n else ""
                write_pack(folder / f"tripupdates-{h:%Y-%m-%dT%H}{suffix}.tar.zst", part)
        log_dir = self.data_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        for line in log_lines:
            with open(log_dir / f"{line['fetched_at'][:10]}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")


def write_pack(path: Path, items: list[tuple[str, bytes]]) -> None:
    cctx = zstandard.ZstdCompressor(level=3)
    with open(path, "wb") as raw, cctx.stream_writer(raw, closefd=False) as zw, \
            tarfile.open(fileobj=zw, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for name, data in items:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
