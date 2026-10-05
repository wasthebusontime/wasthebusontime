"""Open data: the aggregate tables as CSV (published, CC BY 4.0) and Parquet (internal copy).

Counts only, so any rows can be added together; percentiles can't be, so the tables
carry the 1-minute histogram instead. The one exception is end_of_line_monthly.csv,
whose percentiles are per row and say so in the column names.

  C = n, early, on_time, late, early_alt, on_time_alt, late_alt
      (headline window 0 to 300 s; alt window -60 to 300 s)
  H = under, m_10 ... m_1, m0 ... m19, over (departures per 1-minute delay bucket;
      m_3 is 3 to 2 minutes early, m4 is 4 to 5 minutes late)
"""

import csv
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .config import HIST_BUCKETS, HIST_START_MIN
from .perf import COUNTS
from .quality import data_loss, quality_days

HIST_COLUMNS = ["under", *(f"m{m}".replace("-", "_") for m in range(HIST_START_MIN, HIST_START_MIN + HIST_BUCKETS)),
                "over"]

# file name: (key columns as written, key columns in table d, where, histogram)
TABLES = {
    "system_daily": (["date", "scope"], ["date", "scope"], "TRUE", True),
    "routes_daily": (["date", "route", "direction_id", "scope"], ["date", "route", "direction_id", "scope"],
                     "TRUE", False),
    "routes_monthly": (["month", "route", "direction_id", "scope", "day_type"],
                       ["month", "route", "direction_id", "scope", "day_type"], "TRUE", True),
    "routes_hourly_monthly": (["month", "route", "scope", "day_type", "hour"],
                              ["month", "route", "scope", "day_type", "hour"], "TRUE", False),
    "stops_monthly": (["month", "stop_code", "route", "scope"], ["month", "stop", "route", "scope"], "TRUE", False),
    "terminal_monthly": (["month", "route"], ["month", "route"], "scope = 'all_stops' AND is_first", False),
}


def _sort_key(row: list) -> list:
    return [(0, v, "") if isinstance(v, int) else (1, 0, str(v)) for v in row]


def rows_for(stage, keys_d: list[str], where: str, hist: bool) -> list[list]:
    out = []
    for key, p in stage.pq.get(keys_d, where=where, hist=hist, percentiles=False).items():
        row = [*key, *(p[c] for c in COUNTS)]
        if hist:
            h = p["hist"]
            row += [h["under"], *h["counts"], h["over"]]
        out.append(row)
    return sorted(out, key=_sort_key)


def write(stage, csv_dir: Path, agg_dir: Path) -> None:
    csv_dir.mkdir(parents=True, exist_ok=True)
    agg_dir.mkdir(parents=True, exist_ok=True)
    tables = {}
    for name, (header, keys_d, where, hist) in TABLES.items():
        rows = rows_for(stage, keys_d, where, hist)
        header = header + list(COUNTS) + (HIST_COLUMNS if hist else [])
        if name == "stops_monthly":
            header.insert(2, "stop_name")
            rows = [[r[0], r[1], stage.stops.get(r[1], {}).get("name", r[1]), *r[2:]] for r in rows]
        tables[name] = (header, rows)

    eol = []
    for (month, route), p in stage.eol.get(["month", "route"]).items():
        eol.append([month, route, p["n"], p["early"], p["p10"], p["p50"], p["p90"]])
    tables["end_of_line_monthly"] = (["month", "route", "n", "early", "p10", "p50", "p90"], sorted(eol, key=_sort_key))

    days = quality_days(stage)
    tables["quality_daily"] = (
        ["date", "trips_scheduled", "trips_observed", "trips_not_observed", "cancelled", "added", "skipped_stops",
         "departures_scheduled", "departures_observed", "uptime_service_hours"],
        [[d["date"], d["trips_scheduled"], d["trips_observed"],
          d["trips_scheduled"] - d["trips_observed"] - d["_cancelled"], d["_cancelled"], d["_added"], d["_skipped"],
          d["departures_scheduled"], d["departures_observed"], d["uptime_service_hours"]] for d in days])
    tables["data_loss"] = (["start", "end", "cause"], [[g["start"], g["end"], g["cause"]] for g in data_loss(stage)])

    for name, (header, rows) in tables.items():
        with open(csv_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
        columns = {h: [r[i] for r in rows] for i, h in enumerate(header)}
        pq.write_table(pa.table(columns) if rows else pa.table({h: pa.array([], pa.string()) for h in header}),
                       agg_dir / f"{name}.parquet")
