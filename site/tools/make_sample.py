"""Writes synthetic sample stats (schema 1) for building and testing the site.

    uv run python tools/make_sample.py [--out sample-stats]

Everything here is invented: the network, the schedule and the delays. Nothing is
derived from Intercity Transit's feeds. The output is deterministic (fixed seed), and
tests check that the committed sample-stats/ matches a fresh run.

It simulates individual departures from a simple delay model and then aggregates
them the way the stats pipeline will, so every count in the output is consistent.
"""

import argparse
import csv
import json
import math
import random
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261004
START = date(2026, 10, 4)
END = date(2026, 11, 2)
GENERATED_AT = "2026-11-03T12:05:00Z"
HEADLINE = (0, 300)
ALT = (-60, 300)
HIST_START_MIN = -10
HIST_BUCKETS = 30
MIN_SAMPLE = 30
MINUTES_BETWEEN_STOPS = 3
LOW_COMPLETENESS = {date(2026, 10, 22): 0.62}
DATA_LOSS = [
    {"start": "2026-10-09T01:00:00Z", "end": "2026-10-09T01:20:00Z", "cause": "unknown", "note": "sample gap"},
    {"start": "2026-10-17T20:10:00Z", "end": "2026-10-17T20:45:00Z", "cause": "collector", "note": "sample outage"},
    {"start": "2026-10-28T15:30:00Z", "end": "2026-10-28T16:05:00Z", "cause": "feed", "note": "sample feed gap"},
]

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


# Invented coordinates: the grid streets above laid out as a regular grid inside the
# tile area, with the named places off the grid. They match no real street.
STREET_LAT = {"Example St": 47.050, "Sample Ave": 47.042, "Placeholder Rd": 47.034, "Fictional Blvd": 47.026}
PLACES = {
    TC: (47.038, -122.940),
    "Example Park & Ride": (47.054, -122.868),
    "Example Hospital": (47.046, -122.872),
    "Sample Fairgrounds": (47.064, -122.856),
}
# Time-of-day bands on the map, by service hour: (key, first hour, last hour + 1).
BANDS = [("early", 0, 6), ("am_peak", 6, 9), ("midday", 9, 15), ("pm_peak", 15, 18), ("evening", 18, 48)]


def coordinates(name: str) -> tuple[float, float]:
    if name in PLACES:
        return PLACES[name]
    street, avenue = name.split(" & ")
    n = int("".join(c for c in avenue if c.isdigit()))
    return STREET_LAT[street], round(-122.930 + n * 0.006, 5)


def band(hour: int) -> str:
    return next(key for key, lo, hi in BANDS if lo <= hour < hi)


def daytype(d: date) -> str:
    return {5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def to_utc(d: date, minutes: int) -> datetime:
    # Good enough for invented data: PDT until 2026-11-01, PST after.
    offset = 7 if d < date(2026, 11, 1) else 8
    return datetime(d.year, d.month, d.day) + timedelta(minutes=minutes, hours=offset)


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


LOSS_WINDOWS = [(parse_utc(w["start"]), parse_utc(w["end"])) for w in DATA_LOSS]


def lost(when: datetime) -> bool:
    return any(start <= when < end for start, end in LOSS_WINDOWS)


def stop_codes() -> dict[str, str]:
    names = sorted({name for route in ROUTES for name in route[2]})
    return {name: f"E{101 + i}" for i, name in enumerate(names)}


def simulate(rng: random.Random):
    """Returns (departures, arrivals, trips) as lists of dicts."""
    deps, arrs, trips = [], [], []
    d = START
    while d <= END:
        dt = daytype(d)
        for slug, _, stops, bias, service in ROUTES:
            if dt not in service:
                continue
            first, last, every = service[dt]
            for direction in (0, 1):
                pattern = stops if direction == 0 else stops[::-1]
                for start in range(first + direction * 10, last + 1, every):
                    simulate_trip(rng, d, dt, slug, direction, pattern, start, bias, deps, arrs, trips)
        d += timedelta(days=1)
    return deps, arrs, trips


def simulate_trip(rng, d, dt, slug, direction, pattern, start, bias, deps, arrs, trips):
    peak = 90 if dt == "weekday" and 15 <= start // 60 <= 18 else 0
    drift = rng.gauss((bias + peak) * 3 / len(pattern), 25)
    tracked = rng.random() > 0.015
    drop = LOW_COMPLETENESS.get(d, 0.0)
    trip_id = f"{slug}-{d.isoformat()}-{direction}-{start}"
    timepoint_deps = observed_timepoints = 0
    delay = 0.0
    for i, name in enumerate(pattern):
        minutes = start + i * MINUTES_BETWEEN_STOPS
        timepoint = i == 0 or i == len(pattern) - 1 or i % 3 == 0
        when = to_utc(d, minutes)
        observed = tracked and not lost(when) and rng.random() > max(0.02, drop)
        if i == len(pattern) - 1:
            arrival = round(delay - rng.uniform(30, 300))
            arrs.append({"date": d, "slug": slug, "stop": name, "delay": arrival, "observed": observed})
            break
        if i == 0:
            delay = rng.gauss(30, 50)
            if delay < 0 and rng.random() < 0.7:
                delay = rng.uniform(0, 25)
        else:
            delay = delay + drift + rng.gauss(0, 40)
            if timepoint and delay < 0 and rng.random() < 0.9:
                delay = rng.uniform(0, 25)
        actual = delay if timepoint else delay - rng.uniform(0, 100)
        skipped = not timepoint and rng.random() < 0.005
        deps.append({
            "date": d, "daytype": dt, "hour": minutes // 60, "slug": slug, "direction": direction,
            "stop": name, "timepoint": timepoint, "terminal": i == 0, "delay": round(actual),
            "observed": observed and not skipped, "skipped": skipped, "trip": trip_id,
        })
        if timepoint:
            timepoint_deps += 1
            observed_timepoints += observed
    trips.append({"date": d, "slug": slug, "trip": trip_id, "observed": observed_timepoints >= timepoint_deps / 2})


# Aggregation, as the pipeline will do it.


def percentile(sorted_delays, q):
    if not sorted_delays:
        return None
    return sorted_delays[max(0, math.ceil(q * len(sorted_delays)) - 1)]


def perf(delays, hist=True, percentiles=True):
    delays = sorted(delays)
    out = {"n": len(delays)}
    for suffix, (lo, hi) in (("", HEADLINE), ("_alt", ALT)):
        out["early" + suffix] = sum(1 for v in delays if v < lo)
        out["on_time" + suffix] = sum(1 for v in delays if lo <= v <= hi)
        out["late" + suffix] = sum(1 for v in delays if v > hi)
    if percentiles:
        out.update(p10=percentile(delays, 0.1), p50=percentile(delays, 0.5), p90=percentile(delays, 0.9))
    if hist:
        counts = [0] * HIST_BUCKETS
        under = over = 0
        for v in delays:
            bucket = v // 60 - HIST_START_MIN
            if bucket < 0:
                under += 1
            elif bucket >= HIST_BUCKETS:
                over += 1
            else:
                counts[bucket] += 1
        out["hist"] = {"start_min": HIST_START_MIN, "under": under, "counts": counts, "over": over}
    return out


def group(events, key):
    groups = defaultdict(list)
    for e in events:
        groups[key(e)].append(e["delay"])
    return groups


def month_partial(month: str) -> bool:
    year, m = map(int, month.split("-"))
    first = date(year, m, 1)
    following = date(year + (m == 12), m % 12 + 1, 1)
    return first < START or following - timedelta(days=1) > END


def scope_block(events, *, daily=True):
    block = {"summary": perf([e["delay"] for e in events])}
    block["by_hour"] = [{"hour": h, **perf(v, hist=False)} for h, v in sorted(group(events, lambda e: e["hour"]).items())]
    by_dt = group(events, lambda e: e["daytype"])
    block["by_daytype"] = {dt: perf(by_dt.get(dt, []), hist=False) for dt in ("weekday", "saturday", "sunday")}
    if daily:
        by_date = group(events, lambda e: e["date"])
        block["daily"] = [
            {"date": d.isoformat(), **perf(by_date.get(d, []), hist=False, percentiles=False)}
            for d in dates()[-90:]
        ]
    block["monthly"] = [
        {"month": m, "partial": month_partial(m), **perf(v, hist=False)}
        for m, v in sorted(group(events, lambda e: e["date"].isoformat()[:7]).items())
    ]
    return block


def dates():
    return [START + timedelta(days=i) for i in range((END - START).days + 1)]


def end_of_line(arrivals):
    delays = sorted(a["delay"] for a in arrivals if a["observed"])
    return {
        "n": len(delays),
        "p10": percentile(delays, 0.1), "p50": percentile(delays, 0.5), "p90": percentile(delays, 0.9),
        "early_share": round(sum(1 for v in delays if v < 0) / len(delays), 3) if delays else 0.0,
    }


def uptime(days):
    service_minutes = 20 * 60 * days
    lost_minutes = sum((end - start).total_seconds() / 60 for start, end in LOSS_WINDOWS)
    return round(1 - lost_minutes / service_minutes, 4)


def completeness(deps, trips):
    tp = [e for e in deps if e["timepoint"]]
    return {
        "trips_scheduled": len(trips),
        "trips_observed": sum(t["observed"] for t in trips),
        "departures_scheduled": len(tp),
        "departures_observed": sum(e["observed"] for e in tp),
        "uptime_service_hours": uptime(len(dates())),
    }


def trip_counts(deps, trips):
    observed = sum(t["observed"] for t in trips)
    return {
        "scheduled": len(trips), "observed": observed, "not_observed": len(trips) - observed,
        "cancelled": 0, "added": 0, "skipped_stops": sum(e["skipped"] for e in deps),
    }


def scopes(observed, daily=True):
    return {
        "timepoints": scope_block([e for e in observed if e["timepoint"]], daily=daily),
        "all_stops": scope_block(observed, daily=daily),
    }


def build(out: Path) -> None:
    rng = random.Random(SEED)
    deps, arrs, trips = simulate(rng)
    codes = stop_codes()
    observed = [e for e in deps if e["observed"]]
    long_names = {r[0]: r[1] for r in ROUTES}
    patterns = {r[0]: r[2] for r in ROUTES}

    site = out / "site"
    meta = {
        "schema": 1,
        "synthetic": True,
        "generated_at": GENERATED_AT,
        "data_through": END.isoformat(),
        "collection_start": START.isoformat(),
        "pipeline_version": "sample",
        "methodology_version": "2",
        "static_versions": ["sample"],
        "min_sample": MIN_SAMPLE,
        "windows": {"headline": list(HEADLINE), "alt": list(ALT)},
        "period": {"key": "all", "start": START.isoformat(), "end": END.isoformat()},
    }
    route_rows = []
    for slug, long_name, *_ in ROUTES:
        r = [e for e in observed if e["slug"] == slug]
        route_rows.append({
            "slug": slug, "short_name": slug, "long_name": long_name,
            "timepoints": perf([e["delay"] for e in r if e["timepoint"]], hist=False),
            "all_stops": perf([e["delay"] for e in r], hist=False),
        })
    system = {
        "schema": 1,
        "scopes": scopes(observed),
        "terminal": perf([e["delay"] for e in observed if e["terminal"]]),
        "end_of_line": end_of_line(arrs),
        "completeness": completeness(deps, trips),
        "trips": trip_counts(deps, trips),
        "routes": route_rows,
        "notices": [
            {"code": "provisional"},
            *({"code": "data_loss", "start": w["start"], "end": w["end"], "cause": w["cause"]} for w in DATA_LOSS),
            *({"code": "low_completeness", "date": d.isoformat(), "share": s} for d, s in LOW_COMPLETENESS.items()),
            {"code": "methodology_change", "date": "2026-11-01", "version": "2"},
        ],
    }
    write(site / "meta.json", meta)
    write(site / "system.json", system)

    for slug, long_name, stops, *_ in ROUTES:
        r_deps = [e for e in deps if e["slug"] == slug]
        r_obs = [e for e in observed if e["slug"] == slug]
        r_trips = [t for t in trips if t["slug"] == slug]
        doc = {
            "schema": 1,
            "route": {"slug": slug, "short_name": slug, "long_name": long_name, "route_id": f"R{slug}"},
            "scopes": scopes(r_obs),
            "terminal": perf([e["delay"] for e in r_obs if e["terminal"]]),
            "end_of_line": end_of_line([a for a in arrs if a["slug"] == slug]),
            "completeness": completeness(r_deps, r_trips),
            "trips": trip_counts(r_deps, r_trips),
            "stops": [],
            "notices": [{"code": "provisional"}],
        }
        for direction in (0, 1):
            pattern = stops if direction == 0 else stops[::-1]
            d_obs = [e for e in r_obs if e["direction"] == direction]
            headsign = pattern[-1]
            for scope, block in doc["scopes"].items():
                block.setdefault("by_direction", []).append({
                    "direction_id": direction, "headsign": headsign,
                    **perf([e["delay"] for e in d_obs if scope == "all_stops" or e["timepoint"]], hist=False),
                })
            stop_rows = []
            for i, name in enumerate(pattern):
                s_obs = [e for e in d_obs if e["stop"] == name]
                timepoint = i == 0 or i == len(pattern) - 1 or i % 3 == 0
                stop_rows.append({
                    "code": codes[name], "name": name, "timepoint": timepoint,
                    "timepoints": perf([e["delay"] for e in s_obs if e["timepoint"]], hist=False) if timepoint else None,
                    "all_stops": perf([e["delay"] for e in s_obs], hist=False),
                })
            doc["stops"].append({"direction_id": direction, "stops": stop_rows})
        write(site / "routes" / f"{slug}.json", doc)

    index = []
    for name, code in sorted(codes.items()):
        s_obs = [e for e in observed if e["stop"] == name]
        serving = sorted({slug for slug, pattern in patterns.items() if name in pattern})
        timepoint_somewhere = any(e["timepoint"] for e in deps if e["stop"] == name)
        doc = {
            "schema": 1,
            "stop": {"code": code, "stop_id": f"S{code}", "name": name, "timepoint_somewhere": timepoint_somewhere},
            "scopes": scopes(s_obs, daily=False),
            "notices": [],
        }
        if not timepoint_somewhere:
            doc["scopes"]["timepoints"] = None
        for scope, block in doc["scopes"].items():
            if block is None:
                continue
            block["by_route"] = [
                {"slug": slug, "short_name": slug,
                 **perf([e["delay"] for e in s_obs if e["slug"] == slug and (scope == "all_stops" or e["timepoint"])], hist=False)}
                for slug in serving
            ]
        write(site / "stops" / f"{code}.json", doc)
        lat, lon = coordinates(name)
        index.append({"code": code, "name": name, "routes": serving, "lat": lat, "lon": lon})
    write(site / "stops.json", {"schema": 1, "stops": index})

    days = []
    for d in dates():
        dd = [e for e in deps if e["date"] == d]
        dc = completeness(dd, [t for t in trips if t["date"] == d])
        day_lost = sum(
            (min(end, to_utc(d, 25 * 60)) - max(start, to_utc(d, 5 * 60))).total_seconds() / 60
            for start, end in LOSS_WINDOWS
            if start < to_utc(d, 25 * 60) and end > to_utc(d, 5 * 60)
        )
        dc["uptime_service_hours"] = round(1 - day_lost / (20 * 60), 4)
        days.append({"date": d.isoformat(), **dc})
    write(site / "quality.json", {"schema": 1, "days": days, "data_loss": DATA_LOSS})

    write_map(site, observed, index, codes)
    write_csvs(out / "csv", system, route_rows)


def write_map(site: Path, observed: list[dict], index: list[dict], codes: dict[str, str]) -> None:
    """Route lines and the per-preset stop counts the /stops/ map colors its markers with."""
    features = []
    for slug, _, stops, *_ in ROUTES:
        for direction in (0, 1):
            pattern = stops if direction == 0 else stops[::-1]
            features.append({
                "type": "Feature",
                "properties": {"slug": slug, "short_name": slug, "direction_id": direction},
                "geometry": {"type": "LineString", "coordinates": [[coordinates(n)[1], coordinates(n)[0]] for n in pattern]},
            })
    write(site / "routes.geojson", {"type": "FeatureCollection", "features": features}, compact=True)

    # One file per (period, day type, band); "all" means no filter on that dimension.
    groups = defaultdict(list)
    for e in observed:
        for period in ("all", e["date"].isoformat()[:7]):
            for dt in ("all", e["daytype"]):
                for b in ("all", band(e["hour"])):
                    groups[(period, dt, b, codes[e["stop"]])].append(e)
    timepoint_stops = {codes[e["stop"]] for e in observed if e["timepoint"]}
    periods = ["all"] + sorted({e["date"].isoformat()[:7] for e in observed})

    def counts(events):
        p = perf([e["delay"] for e in events], hist=False, percentiles=False)
        return [p["n"], p["early"], p["on_time"], p["late"], p["on_time_alt"]]

    for period in periods:
        for dt in ("all", "weekday", "saturday", "sunday"):
            for b in ["all"] + [key for key, *_ in BANDS]:
                doc = {"schema": 1, "timepoints": [], "all_stops": []}
                for stop in index:
                    events = groups.get((period, dt, b, stop["code"]), [])
                    doc["all_stops"].append(counts(events))
                    doc["timepoints"].append(
                        counts([e for e in events if e["timepoint"]]) if stop["code"] in timepoint_stops else None
                    )
                write(site / "map" / period / f"{dt}-{b}.json", doc, compact=True)


COUNT_COLUMNS = ["n", "early", "on_time", "late", "early_alt", "on_time_alt", "late_alt"]


def write_csvs(csv_dir: Path, system: dict, route_rows: list[dict]) -> None:
    csv_dir.mkdir(parents=True, exist_ok=True)
    with open(csv_dir / "system_daily.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "scope", *COUNT_COLUMNS])
        for scope, block in system["scopes"].items():
            for row in block["daily"]:
                w.writerow([row["date"], scope, *(row[c] for c in COUNT_COLUMNS)])
    with open(csv_dir / "routes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["route", "scope", *COUNT_COLUMNS, "p10", "p50", "p90"])
        for r in route_rows:
            for scope in ("timepoints", "all_stops"):
                p = r[scope]
                w.writerow([r["slug"], scope, *(p[c] for c in COUNT_COLUMNS), p["p10"], p["p50"], p["p90"]])


def write(path: Path, doc: dict, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(doc, indent=1, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "sample-stats")
    args = parser.parse_args()
    if args.out.exists():
        if not (args.out / "site" / "meta.json").exists():
            raise SystemExit(f"{args.out} exists and doesn't look like sample stats; refusing to delete it")
        shutil.rmtree(args.out)
    build(args.out)
    print(f"wrote sample stats to {args.out}")


if __name__ == "__main__":
    main()
