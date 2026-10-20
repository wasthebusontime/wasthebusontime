"""Open data: the aggregate tables as CSV (published, CC BY 4.0) and Parquet (internal copy).

Counts only, so any rows can be added together; percentiles can't be, so the tables
carry the 1-minute histogram instead. The one exception is end_of_line_monthly.csv,
whose percentiles are per row and say so in the column names.

  C = n, early, on_time, late, early_alt, on_time_alt, late_alt
      (headline window -60 to 300 s; alt window 0 to 300 s)
  H = under, m_10 ... m_1, m0 ... m19, over (departures per 1-minute delay bucket;
      m_3 is 3 to 2 minutes early, m4 is 4 to 5 minutes late)

patterns.csv (write_patterns) has one row per current pattern with the counts behind it
(patterns.py), rewritten nightly.

The per-trip downloads (trips/trips_YYYY-MM.csv, write_trips) are the exception to
"aggregates only": one row per scheduled trip, delays we computed, no vehicle IDs and no
raw feed timestamps. One file per finished month, so each is written once.
"""

import csv
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from wbot_site.contract import CARRY_OVER, COUNTS, PATTERNS_COLUMNS, PATTERNS_CSV, TRIPS_COLUMNS, trips_file

from . import timeutil
from .config import HIST_BUCKETS, HIST_START_MIN
from .quality import data_loss

HIST_COLUMNS = ["under", *(f"m{m}".replace("-", "_") for m in range(HIST_START_MIN, HIST_START_MIN + HIST_BUCKETS)),
                "over"]

# file name: (key columns as written, departure dimensions, terminal departures only, histogram)
TABLES = {
    "system_daily": (["date", "scope"], ["date", "scope"], False, True),
    "routes_daily": (["date", "route", "direction_id", "scope"], ["date", "route", "direction_id", "scope"],
                     False, False),
    "routes_monthly": (["month", "route", "direction_id", "scope", "day_type"],
                       ["month", "route", "direction_id", "scope", "day_type"], False, True),
    "routes_hourly_monthly": (["month", "route", "scope", "day_type", "hour"],
                              ["month", "route", "scope", "day_type", "hour"], False, False),
    "stops_monthly": (["month", "stop_code", "route", "scope"], ["month", "stop", "route", "scope"], False, False),
    "terminal_monthly": (["month", "route"], ["month", "route"], True, False),
}


def _sort_key(row: list) -> list:
    return [(0, v, "") if isinstance(v, int) else (1, 0, str(v)) for v in row]


def rows_for(stage, keys: list[str], terminal: bool, hist: bool) -> list[list]:
    out = []
    for key, p in stage.perf(keys, terminal=terminal, hist=hist, percentiles=False).items():
        row = [*key, *(p[c] for c in COUNTS)]
        if hist:
            h = p["hist"]
            row += [h["under"], *h["counts"], h["over"]]
        out.append(row)
    return sorted(out, key=_sort_key)


def write(stage, names, csv_dir: Path, agg_dir: Path) -> None:
    csv_dir.mkdir(parents=True, exist_ok=True)
    agg_dir.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name, (header, keys, terminal, hist) in TABLES.items():
        rows = rows_for(stage, keys, terminal, hist)
        header = header + list(COUNTS) + (HIST_COLUMNS if hist else [])
        if name == "stops_monthly":
            header.insert(2, "stop_name")
            rows = [[r[0], r[1], names.stops.get(r[1], {}).get("name", r[1]), *r[2:]] for r in rows]
        if name == "routes_daily":
            # Trips on a posted detour that day; the numbers include them.
            flagged = stage.detour_trips(["date", "route", "direction_id"])
            header.append("detour_trips")
            rows = [[*r, flagged.get((r[0], r[1], r[2]), 0)] for r in rows]
        tables[name] = (header, rows)

    eol = []
    for (month, route), p in stage.end_of_line(["month", "route"]).items():
        eol.append([month, route, p["n"], p["early"], p["p10"], p["p50"], p["p90"], p["n_block"], p["n_gps"]])
    tables["end_of_line_monthly"] = (["month", "route", "n", "early", "p10", "p50", "p90", "n_block", "n_gps"],
                                     sorted(eol, key=_sort_key))

    days = stage.quality_days()
    tables["quality_daily"] = (
        ["date", "trips_scheduled", "trips_observed", "trips_not_observed", "cancelled", "added", "skipped_stops",
         "departures_scheduled", "departures_observed", "uptime_service_hours"],
        [[d["date"], d["trips_scheduled"], d["trips_observed"],
          d["trips_scheduled"] - d["trips_observed"] - d["_cancelled"], d["_cancelled"], d["_added"], d["_skipped"],
          d["departures_scheduled"], d["departures_observed"], d["uptime_service_hours"]] for d in days])
    tables["data_loss"] = (["start", "end", "cause"], [[g["start"], g["end"], g["cause"]] for g in data_loss(stage)])
    tables["detours"] = (["alert_id", "route", "start", "end", "trips", "header"],
                         [[d["alert_id"], d["route"], d["start"], d["end"], d["trips"], d["header"]]
                          for d in stage.detours()])

    for name, (header, rows) in tables.items():
        with open(csv_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
        columns = {h: [r[i] for r in rows] for i, h in enumerate(header)}
        pq.write_table(pa.table(columns) if rows else pa.table({h: pa.array([], pa.string()) for h in header}),
                       agg_dir / f"{name}.parquet")


def write_patterns(rows: list[list], csv_dir: Path) -> None:
    csv_dir.mkdir(parents=True, exist_ok=True)
    with open(csv_dir / PATTERNS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(PATTERNS_COLUMNS)
        w.writerows(rows)


def finished_months(start: date, end: date, trailing_days: int) -> list[str]:
    """Months (YYYY-MM) of the period from start to end whose last day is behind the trailing
    window, so no nightly run redoes any of their dates (only a late pack or a reprocess can)."""
    months, d = [], start.replace(day=1)
    while True:
        last = (d + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if last > end - timedelta(days=trailing_days):
            return months
        months.append(f"{d:%Y-%m}")
        d = last + timedelta(days=1)


def write_trips(stage, trips_dir: Path) -> list[str]:
    """One CSV per finished month of the period. Returns the months written."""
    period = stage.period
    months = finished_months(period.start, period.end, stage.settings.trailing_days)
    origins = {}
    for month in months:
        trips_dir.mkdir(parents=True, exist_ok=True)
        with open(trips_dir / trips_file(month), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(TRIPS_COLUMNS)
            for day, route, direction, trip_id, start_ts, *rest, carry_over in stage.trip_rows(month):
                if day not in origins:
                    origins[day] = timeutil.origin(date.fromisoformat(day))
                start = "" if start_ts is None else timeutil.hms(start_ts - origins[day])
                w.writerow([day, route, direction, trip_id, start,
                            *("" if v is None else v for v in rest), CARRY_OVER[carry_over]])
    return months
