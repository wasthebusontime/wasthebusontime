"""Writes the synthetic sample stats (schema 1) that the site is built and tested with.

    cd pipeline && uv run python tests/make_sample.py [--out ../site/sample-stats]

Everything here is invented: the network, the schedule and the delays. Nothing is
derived from Intercity Transit's feeds. The output is deterministic (fixed seed), and
test_sample.py checks that the committed site/sample-stats/ matches a fresh run.

It simulates departures from a simple delay model and writes what the pipeline's day
step would have written for them: stop event and trip Parquet files and day manifests,
plus a static GTFS (names, stop positions, route lines) and a fetch log with the gaps.
The pipeline's own site files step (sitefiles.build) then writes the sample, so its
shapes and numbers are exactly what the pipeline makes of these departures.
"""

import argparse
import json
import random
import tempfile
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from synth import Trip, fetch, write_gtfs
from synth import write_log as append_log

from wbot_pipeline import alerts, events, gaps, sitefiles, static, timeutil
from wbot_pipeline.config import EVENTS_VERSION, POLL_S, load_settings

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "site" / "sample-stats"
SEED = 20261004
START = date(2026, 10, 4)
END = date(2026, 11, 2)
GENERATED_AT = "2026-11-03T12:05:00Z"
MINUTES_BETWEEN_STOPS = 3
# Share of departures that go unobserved on these days (normally 2%).
LOW_COMPLETENESS = {date(2026, 10, 22): 0.62}
# Collection gaps. The fetch log shows each one's cause: timeouts (unknown), no fetches
# at all (collector) or HTTP errors (feed).
DATA_LOSS = [
    {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T01:20:00Z", "cause": "unknown", "note": "sample gap"},
    {"start": "2026-10-17T20:10:00Z", "end": "2026-10-17T20:45:00Z", "cause": "collector", "note": "sample outage"},
    {"start": "2026-10-28T15:30:00Z", "end": "2026-10-28T16:05:00Z", "cause": "feed", "note": "sample feed gap"},
]

# A posted detour, as the agency posts it: an alert on one route, listed in the feed from
# start to end (written to the alert files of the dates it overlaps).
DETOUR = {"alert_id": "sample-1", "route_id": "R902", "start": "2026-10-20T16:00:00Z", "end": "2026-10-21T01:00:00Z",
          "header": "Route 902 detour: Sample Ave closed at 4th Ave",
          "description": "Buses use Placeholder Rd between 3rd and 5th Ave. Stops on Sample Ave at 4th Ave are closed."}

TC = "Sample Transit Center"
EW = ["Example St", "Sample Ave", "Placeholder Rd", "Fictional Blvd"]


def x(street, n):
    return f"{street} & {n}{'st' if n == 1 else 'nd' if n == 2 else 'rd' if n == 3 else 'th'} Ave"


# slug, long name, stops for direction 0 (direction 1 is the reverse), delay bias in
# seconds, service per day type as (first departure, last departure, minutes between).
ROUTES = [
    ("901", "Example St Crosstown", [TC] + [x("Example St", n) for n in range(1, 9)] + ["Example Park & Ride"], 40,
     {"weekday": (6 * 60 + 10, 23 * 60 + 40, 30), "saturday": (8 * 60, 21 * 60, 60), "sunday": (9 * 60, 19 * 60, 60)}),
    ("902", "Sample Ave", [TC] + [x("Sample Ave", n) for n in range(1, 8)] + ["Example Hospital"], 90,
     {"weekday": (6 * 60 + 15, 21 * 60, 30), "saturday": (8 * 60, 20 * 60, 60), "sunday": (9 * 60, 18 * 60, 60)}),
    ("903", "Placeholder Rd", [x("Placeholder Rd", n) for n in range(8, 0, -1)] + [TC], 20,
     {"weekday": (6 * 60, 21 * 60, 30), "saturday": (8 * 60, 20 * 60, 60), "sunday": (9 * 60, 18 * 60, 60)}),
    ("904", "3rd Ave North-South", [x(s, 3) for s in EW] + [TC], 60,
     {"weekday": (6 * 60 + 10, 20 * 60, 30), "saturday": (8 * 60, 19 * 60, 60), "sunday": (10 * 60, 17 * 60, 60)}),
    ("905", "Fictional Blvd", [x("Fictional Blvd", n) for n in range(1, 7)] + [TC], 150,
     {"weekday": (6 * 60, 20 * 60, 30), "saturday": (9 * 60, 18 * 60, 60)}),
    ("906", "Saturday Shuttle", [TC, "Example Park & Ride", "Sample Fairgrounds"], 10,
     {"saturday": (10 * 60, 13 * 60, 180)}),
]
SERVICE_IDS = {"weekday": "WK", "saturday": "SAT", "sunday": "SUN"}

# Carry-overs (the previous trip on the block reached the first stop after this trip was due):
# a chance per trip by route, and on route 902 only on weekday PM peaks. A trip that starts
# after one leaves 5 to 12 minutes late. The sample has no blocks otherwise: block and
# previous trip stay blank. With these, the sample has general patterns (905), a specific one
# (902, PM peak), routes with none (901, 903, 904) and one with too little data (906).
CARRY_OVER = {"901": 0.005, "902": 0.0, "903": 0.0, "904": 0.005, "905": 0.12, "906": 0.5}
CARRY_OVER_PM_PEAK = {"902": 0.3}


# Invented coordinates: the grid streets above laid out as a regular grid inside the
# tile area, with the named places off the grid. They match no real street.
STREET_LAT = {"Example St": 47.050, "Sample Ave": 47.042, "Placeholder Rd": 47.034, "Fictional Blvd": 47.026}
PLACES = {
    TC: (47.038, -122.940),
    "Example Park & Ride": (47.054, -122.868),
    "Example Hospital": (47.046, -122.872),
    "Sample Fairgrounds": (47.064, -122.856),
}


def coordinates(name: str) -> tuple[float, float]:
    if name in PLACES:
        return PLACES[name]
    street, avenue = name.split(" & ")
    n = int("".join(c for c in avenue if c.isdigit()))
    return STREET_LAT[street], round(-122.930 + n * 0.006, 5)


def stop_codes() -> dict[str, str]:
    names = sorted({name for route in ROUTES for name in route[2]})
    return {name: f"E{101 + i}" for i, name in enumerate(names)}


LOSS_WINDOWS = [(timeutil.parse_iso_utc(w["start"]), timeutil.parse_iso_utc(w["end"])) for w in DATA_LOSS]


def lost(ts: int) -> bool:
    return any(start <= ts < end for start, end in LOSS_WINDOWS)


# The network, as a static GTFS.


def network(codes: dict[str, str]) -> list[tuple[Trip, int]]:
    """Every scheduled trip with its route's delay bias, in the order the simulation draws them."""
    trips = []
    for slug, _, stops, bias, service in ROUTES:
        for daytype, (first, last, every) in service.items():
            for direction in (0, 1):
                pattern = stops if direction == 0 else stops[::-1]
                timepoints = {i for i in range(len(pattern)) if i == 0 or i == len(pattern) - 1 or i % 3 == 0}
                for start in range(first + direction * 10, last + 1, every):
                    trip = Trip(f"{slug}-{SERVICE_IDS[daytype]}-{direction}-{start}", f"R{slug}", direction,
                                SERVICE_IDS[daytype], start, [f"S{codes[n]}" for n in pattern], timepoints,
                                headsign=pattern[-1], shape_id=f"SH{slug}-{direction}",
                                min_between=MINUTES_BETWEEN_STOPS)
                    trips.append((trip, bias))
    return trips


def write_static(static_dir: Path, trips: list[Trip], codes: dict[str, str]) -> None:
    stops = [(f"S{code}", code, name, *coordinates(name)) for name, code in sorted(codes.items(), key=lambda c: c[1])]
    shapes = {t.shape_id: t.stops for t in trips}
    write_gtfs(static_dir, "sample_00000000", trips, feed_version="sample", start=timeutil.yyyymmdd(START),
               end=timeutil.yyyymmdd(END), fetched_at="2026-10-01T10:30:00Z",
               routes=[(f"R{slug}", slug, long_name) for slug, long_name, *_ in ROUTES],
               stops=stops, shapes=shapes)


# Departures, as the pipeline's stop events and trip facts.


def simulate_trip(rng: random.Random, d: date, t: Trip, bias: int) -> tuple[list[dict], dict]:
    """One trip on one date: its stop event rows and its trip row."""
    o = timeutil.origin(d)
    daytype = timeutil.daytype(d)
    slug = t.route_id[1:]
    n = len(t.stops)
    peak = 90 if daytype == "weekday" and 15 <= t.start_min // 60 <= 18 else 0
    drift = rng.gauss((bias + peak) * 3 / n, 25)
    tracked = rng.random() > 0.015
    drop = LOW_COMPLETENESS.get(d, 0.0)
    # Drawn from the trip's own generator, so the draws above and below don't change.
    own = random.Random(f"{d.isoformat()} {t.trip_id}")
    pm_peak = daytype == "weekday" and 15 <= t.start_min // 60 < 18
    chance = CARRY_OVER_PM_PEAK.get(slug, CARRY_OVER[slug]) if pm_peak else CARRY_OVER[slug]
    carry_over = own.random() < chance
    rows = []
    delay = 0.0
    for i, (seq, stop_id, arr, dep, timepoint) in enumerate(t.schedule()):
        observed = tracked and not lost(o + dep) and rng.random() > max(0.02, drop)
        row = {
            "service_date": d, "trip_id": t.trip_id, "route_id": t.route_id, "route_slug": slug,
            "direction_id": t.direction_id, "stop_sequence": seq, "stop_id": stop_id, "stop_code": stop_id[1:],
            "scheduled": True, "is_timepoint": timepoint, "is_first": i == 0, "is_last": i == n - 1,
            "sched_arr_ts": o + arr, "sched_dep_ts": o + dep, "service_hour": dep // 3600, "day_type": daytype,
            "delay_dep_s": None, "delay_arr_s": None, "stop_sr": "SCHEDULED" if tracked else None,
            "trip_sr": "SCHEDULED" if tracked else None, "observed": observed, "observed_arr": observed,
            # End-of-line arrivals mostly come from the next trip in the block, about 1 in 20 from GPS.
            "method": ("gps" if zlib.crc32(t.trip_id.encode()) % 20 == 0 else "block") if i == n - 1 else "tu",
        }
        rows.append(row)
        if i == n - 1:
            arrival = round(delay - rng.uniform(30, 300))
            row["delay_arr_s"] = arrival if observed else None
            break
        if i == 0:
            delay = rng.gauss(30, 50)
            if delay < 0 and rng.random() < 0.7:
                delay = rng.uniform(0, 25)
            if carry_over:
                delay = own.uniform(300, 720)
        else:
            delay = delay + drift + rng.gauss(0, 40)
            if timepoint and delay < 0 and rng.random() < 0.9:
                delay = rng.uniform(0, 25)
        actual = delay if timepoint else delay - rng.uniform(0, 100)
        skipped = not timepoint and rng.random() < 0.005
        if skipped:
            row.update(stop_sr="SKIPPED", observed=False)
        elif observed:
            row["delay_dep_s"] = round(actual)
    timed = [r for r in rows if r["is_timepoint"] and not r["is_last"]]
    trip = {
        "service_date": d, "trip_id": t.trip_id, "route_id": t.route_id, "route_slug": slug,
        "direction_id": t.direction_id, "scheduled": True, "seen": tracked, "trip_sr": "SCHEDULED" if tracked else None,
        "eligible_timepoints": len(timed), "observed_timepoints": sum(r["observed"] for r in timed),
        "skipped_stops": sum(r["stop_sr"] == "SKIPPED" for r in rows), "no_data_stops": 0,
        "start_ts": rows[0]["sched_dep_ts"], "end_ts": rows[-1]["sched_arr_ts"],
        "block_id": None, "prev_trip_id": None, "sched_layover_s": None, "prev_end_delay_s": None,
        "start_delay_s": rows[0]["delay_dep_s"] if tracked else None,
        "end_delay_s": rows[-1]["delay_arr_s"] if tracked else None,
        "carry_over": carry_over if tracked else None,
    }
    return rows, trip


EVENT_TYPES = {
    "service_date": pa.date32(), "trip_id": pa.string(), "route_id": pa.string(), "route_slug": pa.string(),
    "direction_id": pa.int32(), "stop_sequence": pa.int32(), "stop_id": pa.string(), "stop_code": pa.string(),
    "scheduled": pa.bool_(), "is_timepoint": pa.bool_(), "is_first": pa.bool_(), "is_last": pa.bool_(),
    "sched_arr_ts": pa.int64(), "sched_dep_ts": pa.int64(), "service_hour": pa.int32(), "day_type": pa.string(),
    "delay_dep_s": pa.int32(), "delay_arr_s": pa.int32(), "stop_sr": pa.string(), "trip_sr": pa.string(),
    "observed": pa.bool_(), "observed_arr": pa.bool_(), "method": pa.string(),
}
TRIP_TYPES = {
    "service_date": pa.date32(), "trip_id": pa.string(), "route_id": pa.string(), "route_slug": pa.string(),
    "direction_id": pa.int32(), "scheduled": pa.bool_(), "seen": pa.bool_(), "trip_sr": pa.string(),
    "eligible_timepoints": pa.int64(), "observed_timepoints": pa.int64(), "skipped_stops": pa.int64(),
    "no_data_stops": pa.int64(), "start_ts": pa.int64(), "end_ts": pa.int64(),
    "block_id": pa.string(), "prev_trip_id": pa.string(), "sched_layover_s": pa.int32(),
    "prev_end_delay_s": pa.int32(), "start_delay_s": pa.int32(), "end_delay_s": pa.int32(), "carry_over": pa.bool_(),
}


def write_parquet(path: Path, rows: list[dict], types: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(list(types.items()))), path)


def write_log(log_dir: Path) -> None:
    """A TripUpdates fetch every POLL_S seconds from before the first service date to after
    the last, except inside the data-loss windows, which show their cause."""
    records = []
    end = timeutil.day_end(END) + 6 * 3600
    for ts in range(timeutil.origin(START), end, POLL_S):
        fetched = datetime.fromtimestamp(ts, UTC)
        window = next((w for w, (a, b) in zip(DATA_LOSS, LOSS_WINDOWS) if a <= ts < b), None)
        if window is None:
            records.append(fetch(fetched, status=200, bytes=1000, sha256="x", header_ts=ts, data_ts=ts - 5,
                                 entities=40))
        elif window["cause"] == "feed":
            records.append(fetch(fetched, status=503, bytes=0, error="HTTP 503"))
        elif window["cause"] != "collector":
            records.append(fetch(fetched, bytes=0, error="timed out"))
    append_log(log_dir, records)


def write_day(settings, version: static.Version, d: date, rows: list[dict], trips: list[dict]) -> None:
    """The day's stop events, trip facts and manifest, with gaps found in the fetch log as the
    pipeline finds them. The notes on known gaps come from DATA_LOSS."""
    out = events.paths(settings, d)
    write_parquet(out.events, rows, EVENT_TYPES)
    write_parquet(out.trips, trips, TRIP_TYPES)
    service = gaps.merge([(t["start_ts"], t["end_ts"]) for t in trips])
    span = (service[0][0], service[-1][1])
    notes = [{"start": a, "end": b, "cause": w["cause"], "note": w["note"]} for w, (a, b) in zip(DATA_LOSS, LOSS_WINDOWS)]
    found = gaps.apply_overrides(gaps.find_gaps(gaps.read_fetches(settings.log_dir, *span), service), notes)
    lo, hi = timeutil.parse_iso_utc(DETOUR["start"]), timeutil.parse_iso_utc(DETOUR["end"])
    if lo < span[1] and span[0] < hi:
        path = alerts.path(settings, d)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist([{
            "alert_id": DETOUR["alert_id"], "effect": "DETOUR", "cause": "CONSTRUCTION", "routes": [DETOUR["route_id"]],
            "stops": [], "trips": [], "periods": [], "header": DETOUR["header"], "description": DETOUR["description"],
            "first_seen": lo, "last_seen": hi - alerts.POLL_S}], schema=alerts.SCHEMA), path)
    out.manifest.parent.mkdir(parents=True, exist_ok=True)
    out.manifest.write_text(json.dumps({
        "service_date": d.isoformat(), "events_version": EVENTS_VERSION,
        "methodology_version": settings.methodology_version, "pipeline_version": "sample", "status": "ok",
        "static": {"stem": version.stem, "feed_version": version.feed_version, "sha256": version.sha256},
        "service_span": list(span), "packs": [], "gaps": found,
        "service_s": sum(b - a for a, b in service), "lost_service_s": gaps.lost_seconds(found, service),
    }, indent=1) + "\n", encoding="utf-8", newline="\n")


def build(out: Path) -> None:
    rng = random.Random(SEED)
    codes = stop_codes()
    trips = network(codes)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        settings = load_settings({}, data_dir=work / "data", derived_dir=work / "derived", first_service_date=START,
                                 methodology_version="2", methodology_changes=(("2026-11-01", "2"),))
        write_static(settings.static_dir, [t for t, _ in trips], codes)
        (version,) = static.list_versions(settings.static_dir)
        write_log(settings.log_dir)
        d = START
        while d <= END:
            service_id = SERVICE_IDS[timeutil.daytype(d)]
            rows, trip_rows = [], []
            for t, bias in trips:
                if t.service_id == service_id:
                    r, trip = simulate_trip(rng, d, t, bias)
                    rows += r
                    trip_rows.append(trip)
            write_day(settings, version, d, rows, trip_rows)
            d += timedelta(days=1)
        sitefiles.build(settings, out, pipeline_version="sample", generated_at=GENERATED_AT, synthetic=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=SAMPLE_DIR)
    args = parser.parse_args()
    if args.out.exists() and not (args.out / "site" / "meta.json").exists():
        raise SystemExit(f"{args.out} exists and doesn't look like sample stats; refusing to replace it")
    build(args.out)
    print(f"wrote sample stats to {args.out}")


if __name__ == "__main__":
    main()
