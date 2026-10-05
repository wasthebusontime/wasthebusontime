"""Static GTFS versions: which one applies to a service date, and a Parquet cache of each.

The collector keeps every version as static/{feed_version}_{sha8}.zip with a JSON
sidecar saying when it was fetched. Each zip is converted once into Parquet tables
under derived/static/{stem}/, which DuckDB queries cheaply.
"""

import csv
import io
import json
import logging
import shutil
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb

from . import timeutil
from .db import quote

log = logging.getLogger(__name__)

# Columns the pipeline uses, per file. Missing optional files or columns become
# empty tables or NULL columns, so queries can always name them.
COLUMNS = {
    "routes": ["route_id", "route_short_name", "route_long_name"],
    "trips": ["trip_id", "route_id", "service_id", "direction_id", "trip_headsign", "shape_id"],
    "stop_times": ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time", "timepoint"],
    "stops": ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"],
    "calendar": ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                 "start_date", "end_date"],
    "calendar_dates": ["service_id", "date", "exception_type"],
    "shapes": ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"],
}
CACHE_VERSION = 1
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Version:
    stem: str            # file name without .zip, e.g. 20260927_9de242c4
    zip_path: Path
    sha256: str
    fetched_at: int | None
    feed_version: str
    start: date
    end: date

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end


def _read_member(z: zipfile.ZipFile, name: str) -> list[dict]:
    if name not in z.namelist():
        return []
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")))


def _date_range(z: zipfile.ZipFile, feed_info: dict) -> tuple[date, date]:
    start, end = feed_info.get("feed_start_date", ""), feed_info.get("feed_end_date", "")
    if start and end:
        return timeutil.parse_yyyymmdd(start), timeutil.parse_yyyymmdd(end)
    calendar = _read_member(z, "calendar.txt")
    days = [r["start_date"] for r in calendar] + [r["end_date"] for r in calendar]
    days += [r["date"] for r in _read_member(z, "calendar_dates.txt")]
    days = sorted(d for d in days if d)
    return timeutil.parse_yyyymmdd(days[0]), timeutil.parse_yyyymmdd(days[-1])


def list_versions(static_dir: Path) -> list[Version]:
    versions = []
    for zip_path in sorted(static_dir.glob("*.zip")):
        sidecar = zip_path.with_suffix(".json")
        meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        with zipfile.ZipFile(zip_path) as z:
            feed_info = meta.get("feed_info") or next(iter(_read_member(z, "feed_info.txt")), {})
            start, end = _date_range(z, feed_info)
        fetched = meta.get("fetched_at")
        versions.append(Version(
            stem=zip_path.stem,
            zip_path=zip_path,
            sha256=meta.get("sha256", ""),
            fetched_at=timeutil.parse_iso_utc(fetched) if fetched else None,
            feed_version=feed_info.get("feed_version") or zip_path.stem,
            start=start,
            end=end,
        ))
    return versions


def _order(v: Version):
    return (v.fetched_at or 0, v.stem)


def choose(versions: list[Version], d: date) -> Version | None:
    """The version in effect on a service date.

    Among versions whose dates cover the day, the newest one fetched before the day
    ended. If none had been fetched yet (the first days of collection), the earliest
    fetched one that covers it. A version fetched later never rewrites history, so a
    rebuild gives the same answer.
    """
    covering = [v for v in versions if v.covers(d)]
    if not covering:
        return None
    before = [v for v in covering if (v.fetched_at or 0) < timeutil.day_end(d)]
    return max(before, key=_order) if before else min(covering, key=_order)


def most_current(versions: list[Version], start: date, end: date) -> Version | None:
    """The newest of the versions that applied at some point in a period."""
    used = {choose(versions, start + timedelta(days=i)) for i in range((end - start).days + 1)}
    used.discard(None)
    return max(used, key=_order) if used else None


def ensure_cache(derived_dir: Path, version: Version) -> Path:
    """Convert the zip to Parquet tables once; returns the cache directory."""
    target = derived_dir / "static" / version.stem
    marker = target / "done.json"
    if marker.exists() and json.loads(marker.read_text())["cache_version"] == CACHE_VERSION:
        return target
    tmp = target.with_name(target.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    con = duckdb.connect()
    with zipfile.ZipFile(version.zip_path) as z:
        for table, columns in COLUMNS.items():
            name = f"{table}.txt"
            csv_path = tmp / name
            if name in z.namelist():
                # Re-encode without a byte order mark, so DuckDB sees clean headers.
                with z.open(name) as src, open(csv_path, "w", encoding="utf-8", newline="") as dst:
                    shutil.copyfileobj(io.TextIOWrapper(src, encoding="utf-8-sig", newline=""), dst)
            header = csv_path.read_text(encoding="utf-8").partition("\n")[0] if csv_path.exists() else ""
            if not header.strip():
                csv_path.write_text(",".join(columns) + "\n", encoding="utf-8")
            con.execute(f"CREATE TABLE t AS SELECT * FROM read_csv('{quote(csv_path)}', header=true, "
                        "all_varchar=true, delim=',', quote='\"')")
            present = {r[0] for r in con.execute("DESCRIBE t").fetchall()}
            for c in columns:
                if c not in present:
                    con.execute(f"ALTER TABLE t ADD COLUMN {c} VARCHAR")
            select = ", ".join(f"nullif(trim({c}), '') AS {c}" for c in columns)
            con.execute(f"COPY (SELECT {select} FROM t) TO '{quote(tmp / f'{table}.parquet')}' (FORMAT parquet)")
            con.execute("DROP TABLE t")
            csv_path.unlink()
    con.close()
    (tmp / "done.json").write_text(json.dumps({"cache_version": CACHE_VERSION, "sha256": version.sha256}))
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    log.info("cached static version %s", version.stem)
    return target


def register(con: duckdb.DuckDBPyConnection, cache: Path, prefix: str = "") -> None:
    """Views named after the GTFS files (routes, trips, stop_times, ...) over a version's cache."""
    for table in COLUMNS:
        con.execute(f"CREATE OR REPLACE VIEW {prefix}{table} AS "
                    f"SELECT * FROM read_parquet('{quote(cache / f'{table}.parquet')}')")


def active_services(con: duckdb.DuckDBPyConnection, d: date, prefix: str = "") -> list[str]:
    """Service ids running on a date, from calendar plus calendar_dates."""
    day, wd = timeutil.yyyymmdd(d), WEEKDAYS[d.weekday()]
    active = {r[0] for r in con.execute(
        f"SELECT service_id FROM {prefix}calendar WHERE {wd} = '1' AND start_date <= ? AND end_date >= ?",
        [day, day]).fetchall()}
    for service_id, exception in con.execute(
            f"SELECT service_id, exception_type FROM {prefix}calendar_dates WHERE date = ?", [day]).fetchall():
        if exception == "1":
            active.add(service_id)
        elif exception == "2":
            active.discard(service_id)
    return sorted(active)

