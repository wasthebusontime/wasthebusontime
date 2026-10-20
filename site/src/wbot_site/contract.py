"""The site files format (schema 1): the names both sides use, and a check of a stats directory.

The pipeline writes these files and the site reads them; both import their names from here,
so a name is defined once. Standard library only: the pipeline imports this module without
the site's own dependencies.

A stats directory has the same layout as the stats repo:
  site/meta.json, system.json, stops.json, quality.json, routes/{slug}.json,
  stops/{code}.json, routes.geojson, map/{period}/{daytype}-{band}.json,
  presets/{daytype}-{band}/system.json, presets/{daytype}-{band}/routes/{slug}.json
  csv/*.csv
  trips/trips_YYYY-MM.csv

Per-trip downloads (trips/, optional, TRIPS_COLUMNS): one row per scheduled trip, one file
per finished month, written once. They are published in the stats repo only; the site
doesn't copy them.

Page presets (presets/, optional: stats without them are valid) are the route and system
numbers for one day type and band, for every name in PRESETS. A preset file has the shapes
of system.json or routes/{slug}.json for the parts that can be filtered: scopes, terminal,
end_of_line, stops (routes) and the routes table (system), counted only on departures in that
day type and band, except that by_hour keeps every band and by_daytype every day type. A
departure's band is that of its own scheduled time and an end-of-line arrival's that of its
scheduled arrival, not the trip's start. It
adds "preset" ({daytype, band}) and "scheduled" ({timepoints, all_stops}: scheduled departures,
not counting cancelled trips; a routes table row carries its route's). It leaves out
completeness, trips and notices, which are never filtered. A route, or the whole system, with
no scheduled departures in a preset has no file there: a preset file always has
scheduled.all_stops above 0, and a preset's routes table lists exactly its route files.
"""

import csv
import json
import re
from pathlib import Path

SCHEMA = 1
SCOPES = ("timepoints", "all_stops")
DAYTYPES = ("weekday", "saturday", "sunday")
# Stop map time-of-day bands by service hour: (key, first hour, last hour + 1).
BANDS = (("early", 0, 6), ("am_peak", 6, 9), ("midday", 9, 15), ("pm_peak", 15, 18), ("evening", 18, 48))
# Stop map presets: one file per period, day type and band; "all" means no filter.
MAP_DAYTYPES = ("all", *DAYTYPES)
MAP_BANDS = ("all", *(key for key, _, _ in BANDS))


def preset_name(daytype: str, band: str) -> str:
    """The name of a map or page preset: preset_name("weekday", "am_peak") -> "weekday-am_peak"."""
    return f"{daytype}-{band}"


# Page presets: every day type and band but all-all, which is the files themselves.
PRESETS = tuple(preset_name(dt, b) for dt in MAP_DAYTYPES for b in MAP_BANDS if (dt, b) != ("all", "all"))

# Notice codes the site knows. A file may carry others (the pipeline can add a code first);
# the site skips them.
NOTICE_FIELDS = {
    "provisional": (),
    "data_loss": ("start", "end", "cause"),
    "low_completeness": ("date", "share"),
    "methodology_change": ("date", "version"),
    # Route files add header, description and trips; stop files add route and short_name.
    "detour": ("start", "end"),
}
NOTICE_CODES = tuple(NOTICE_FIELDS)

# Perf blocks count departures in each on-time window (meta.windows): the headline window's
# counts are early, on_time and late, the alt window's carry the suffix _alt.
WINDOWS = ("headline", "alt")
PARTS = ("early", "on_time", "late")
SUFFIX = {"headline": "", "alt": "_alt"}
COUNTS = ("n", *(part + SUFFIX[w] for w in WINDOWS for part in PARTS))
PERCENTILES = ("p10", "p50", "p90")
# A stop map cell is a list of these counts, in this order.
MAP_CELL = ("n", "early", "on_time", "late", "on_time_alt")


def count_key(part: str, window: str = "headline") -> str:
    """The name of a perf count: count_key("on_time", "alt") -> "on_time_alt"."""
    return part + SUFFIX[window]


# Per-trip download columns. Delays are in seconds, blank when not recorded; carry_over is
# yes, no or blank (unknown). sched_start is the scheduled first departure as a GTFS time.
TRIPS_DIR = "trips"
TRIPS_COLUMNS = ("date", "route", "direction_id", "trip_id", "sched_start", "start_delay_s", "end_delay_s",
                 "block_id", "prev_trip_id", "sched_layover_s", "prev_end_delay_s", "carry_over")
CARRY_OVER = {True: "yes", False: "no", None: ""}


def trips_file(month: str) -> str:
    """The per-trip download for a month: trips_file("2026-10") -> "trips_2026-10.csv"."""
    return f"trips_{month}.csv"


class ContractError(ValueError):
    """A stats directory doesn't follow the format."""


def map_periods(stats_dir: Path) -> list[str]:
    """Periods with stop map presets ("all" first, then months), or [] if the stats have no map.
    A map needs every stop's position, the route lines and the "all" period."""
    site = Path(stats_dir) / "site"
    stops = _load(site, "stops.json").get("stops", [])
    map_dir = site / "map"
    if not stops or not all("lat" in s and "lon" in s for s in stops) or not (site / "routes.geojson").is_file():
        return []
    periods = sorted(p.name for p in map_dir.iterdir() if p.is_dir()) if map_dir.is_dir() else []
    return ["all", *[p for p in periods if p != "all"]] if "all" in periods else []


def validate(stats_dir: Path) -> None:
    """Check a stats directory's site files. Raises ContractError naming the file and the
    first problem: a missing file or key, a scope missing, counts that don't add up, a
    notice without its fields, a map preset that is missing or the wrong size, or a page
    preset that is malformed, has no service, or doesn't add up with the others."""
    site = Path(stats_dir) / "site"
    meta = _load(site, "meta.json")
    _keys(meta, ("synthetic", "generated_at", "data_through", "collection_start", "pipeline_version",
                 "methodology_version", "static_versions", "min_sample", "windows", "period"), "meta.json")
    _keys(meta["windows"], WINDOWS, "meta.json: windows")
    _keys(meta["period"], ("key", "start", "end"), "meta.json: period")

    system = _load(site, "system.json")
    _keys(system, ("scopes", "terminal", "end_of_line", "completeness", "trips", "routes", "notices"), "system.json")
    _scopes(system["scopes"], "system.json", daily=True)
    _perf(system["terminal"], "system.json: terminal")
    _notices(system["notices"], "system.json")
    for i, row in enumerate(system["routes"]):
        where = f"system.json: routes[{i}]"
        _keys(row, ("slug", "short_name", "long_name", *SCOPES), where)
        for scope in SCOPES:
            _perf(row[scope], f"{where}.{scope}")

    quality = _load(site, "quality.json")
    _keys(quality, ("days", "data_loss"), "quality.json")
    for i, row in enumerate(quality.get("detours", [])):   # optional: stats from before 2026-10-18 have none
        _keys(row, ("route", "short_name", "start", "end", "header", "trips"), f"quality.json: detours[{i}]")

    index = _load(site, "stops.json")
    _keys(index, ("stops",), "stops.json")
    for i, stop in enumerate(index["stops"]):
        _keys(stop, ("code", "name", "routes"), f"stops.json: stops[{i}]")

    for row in system["routes"]:
        _route(_load(site, f"routes/{row['slug']}.json"), f"routes/{row['slug']}.json")
    for stop in index["stops"]:
        _stop(_load(site, f"stops/{stop['code']}.json"), f"stops/{stop['code']}.json")

    n = len(index["stops"])
    for period in map_periods(stats_dir):
        for daytype in MAP_DAYTYPES:
            for band in MAP_BANDS:
                name = f"map/{period}/{preset_name(daytype, band)}.json"
                _map(_load(site, name), name, n)

    if (site / "presets").is_dir():
        _presets(site, system)

    if (Path(stats_dir) / TRIPS_DIR).is_dir():
        validate_trips(Path(stats_dir) / TRIPS_DIR)


# The checks. `where` names the file and the place in it, for the error message.


def _load(site: Path, name: str) -> dict:
    try:
        doc = json.loads((site / name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError(f"missing site/{name}") from None
    except json.JSONDecodeError as e:
        raise ContractError(f"{name}: not JSON ({e})") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        found = doc.get("schema") if isinstance(doc, dict) else None
        raise ContractError(f"{name}: schema {found!r}, expected schema {SCHEMA}")
    return doc


def _keys(doc, keys, where: str) -> None:
    if not isinstance(doc, dict):
        raise ContractError(f"{where}: expected an object, found {type(doc).__name__}")
    missing = [k for k in keys if k not in doc]
    if missing:
        raise ContractError(f"{where}: missing {', '.join(missing)}")


def _perf(p, where: str) -> None:
    _keys(p, COUNTS, where)
    for k in COUNTS:
        if not isinstance(p[k], int) or isinstance(p[k], bool) or p[k] < 0:
            raise ContractError(f"{where}: {k} is {p[k]!r}, expected a count")
    for w in WINDOWS:
        parts = [p[count_key(part, w)] for part in PARTS]
        if sum(parts) != p["n"]:
            raise ContractError(f"{where}: {w} window counts add up to {sum(parts)}, n is {p['n']}")
    if "hist" in p:
        h = p["hist"]
        _keys(h, ("start_min", "under", "counts", "over"), f"{where}.hist")
        if h["under"] + sum(h["counts"]) + h["over"] != p["n"]:
            raise ContractError(f"{where}.hist: counts don't add up to n ({p['n']})")


def _list(doc, key: str, where: str) -> list:
    if not isinstance(doc[key], list):
        raise ContractError(f"{where}.{key}: expected a list")
    return doc[key]


def _scope_block(block, where: str, daily: bool) -> None:
    _keys(block, ("summary", "by_hour", "by_daytype", "monthly", *(("daily",) if daily else ())), where)
    _perf(block["summary"], f"{where}.summary")
    for i, row in enumerate(_list(block, "by_hour", where)):
        _keys(row, ("hour",), f"{where}.by_hour[{i}]")
        _perf(row, f"{where}.by_hour[{i}]")
    _keys(block["by_daytype"], DAYTYPES, f"{where}.by_daytype")
    for dt in DAYTYPES:
        _perf(block["by_daytype"][dt], f"{where}.by_daytype.{dt}")
    for i, row in enumerate(_list(block, "monthly", where)):
        _keys(row, ("month", "partial"), f"{where}.monthly[{i}]")
        _perf(row, f"{where}.monthly[{i}]")
    if daily:
        for i, row in enumerate(_list(block, "daily", where)):
            _keys(row, ("date",), f"{where}.daily[{i}]")
            _perf(row, f"{where}.daily[{i}]")


def _scopes(scopes, where: str, daily: bool, optional_timepoints: bool = False) -> None:
    _keys(scopes, SCOPES, f"{where}: scopes")
    for scope in SCOPES:
        if scopes[scope] is None and scope == "timepoints" and optional_timepoints:
            continue
        _scope_block(scopes[scope], f"{where}: scopes.{scope}", daily)


def _notices(notices, where: str) -> None:
    if not isinstance(notices, list):
        raise ContractError(f"{where}: notices: expected a list")
    for i, notice in enumerate(notices):
        _keys(notice, ("code",), f"{where}: notices[{i}]")
        _keys(notice, NOTICE_FIELDS.get(notice["code"], ()), f"{where}: notices[{i}] ({notice['code']})")


def _route(doc: dict, where: str) -> None:
    _keys(doc, ("route", "scopes", "terminal", "end_of_line", "completeness", "trips", "stops", "notices"), where)
    _route_numbers(doc, where)
    _notices(doc["notices"], where)


def _route_numbers(doc: dict, where: str) -> None:
    """The parts a route file and a route's page preset share."""
    _keys(doc["route"], ("slug", "short_name", "long_name", "route_id"), f"{where}: route")
    _scopes(doc["scopes"], where, daily=True)
    for scope in SCOPES:
        for i, row in enumerate(_list(doc["scopes"][scope], "by_direction", f"{where}: scopes.{scope}")):
            _keys(row, ("direction_id", "headsign"), f"{where}: scopes.{scope}.by_direction[{i}]")
            _perf(row, f"{where}: scopes.{scope}.by_direction[{i}]")
    _perf(doc["terminal"], f"{where}: terminal")
    for i, direction in enumerate(_list(doc, "stops", where)):
        _keys(direction, ("direction_id", "stops"), f"{where}: stops[{i}]")
        for j, stop in enumerate(direction["stops"]):
            at = f"{where}: stops[{i}].stops[{j}]"
            _keys(stop, ("code", "name", "timepoint", *SCOPES), at)
            if stop["timepoints"] is not None:
                _perf(stop["timepoints"], f"{at}.timepoints")
            _perf(stop["all_stops"], f"{at}.all_stops")


def _stop(doc: dict, where: str) -> None:
    _keys(doc, ("stop", "scopes", "notices"), where)
    _keys(doc["stop"], ("code", "stop_id", "name", "timepoint_somewhere", "timepoint_routes"), f"{where}: stop")
    _scopes(doc["scopes"], where, daily=False, optional_timepoints=True)
    for scope in SCOPES:
        if doc["scopes"][scope] is None:
            continue
        for i, row in enumerate(_list(doc["scopes"][scope], "by_route", f"{where}: scopes.{scope}")):
            _keys(row, ("slug", "short_name"), f"{where}: scopes.{scope}.by_route[{i}]")
            _perf(row, f"{where}: scopes.{scope}.by_route[{i}]")
    _notices(doc["notices"], where)


def _map(doc: dict, where: str, n: int) -> None:
    _keys(doc, SCOPES, where)
    for scope in SCOPES:
        cells = _list(doc, scope, where)
        if len(cells) != n:
            raise ContractError(f"{where}: {scope} has {len(cells)} stops, stops.json has {n}")
        for i, cell in enumerate(cells):
            if cell is None and scope == "timepoints":
                continue
            if not isinstance(cell, list) or len(cell) != len(MAP_CELL) or not all(isinstance(v, int) for v in cell):
                raise ContractError(f"{where}: {scope}[{i}] is {cell!r}, expected {len(MAP_CELL)} counts "
                                    f"({', '.join(MAP_CELL)})")


def _presets(site: Path, system: dict) -> None:
    """Page presets: each file well formed, with service, and the right routes; then the counts
    add up across presets (_preset_sums)."""
    slugs = {row["slug"] for row in system["routes"]}
    totals = {}
    for folder in sorted((site / "presets").iterdir()):
        name = folder.name
        if name not in PRESETS or not folder.is_dir():
            raise ContractError(f"presets/{name}: not a page preset (expected a folder named daytype-band "
                                f"from MAP_DAYTYPES and MAP_BANDS, not all-all)")
        daytype, band = name.split("-")
        where = f"presets/{name}/system.json"
        doc = _load(site, where)
        _keys(doc, ("preset", "scheduled", "scopes", "terminal", "end_of_line", "routes"), where)
        _preset_head(doc, where, daytype, band)
        _scopes(doc["scopes"], where, daily=True)
        _perf(doc["terminal"], f"{where}: terminal")
        totals[(daytype, band, None)] = _preset_counts(doc)
        rows = set()
        for i, row in enumerate(_list(doc, "routes", where)):
            at = f"{where}: routes[{i}]"
            _keys(row, ("slug", "short_name", "long_name", "scheduled", *SCOPES), at)
            if row["slug"] not in slugs:
                raise ContractError(f"{at}: route {row['slug']!r} is not in system.json")
            for scope in SCOPES:
                _perf(row[scope], f"{at}.{scope}")
            rows.add(row["slug"])
            route_where = f"presets/{name}/routes/{row['slug']}.json"
            route = _load(site, route_where)
            _keys(route, ("preset", "route", "scheduled", "scopes", "terminal", "end_of_line", "stops"), route_where)
            _preset_head(route, route_where, daytype, band)
            _route_numbers(route, route_where)
            if route["route"]["slug"] != row["slug"] or route["scheduled"] != row["scheduled"]:
                raise ContractError(f"{route_where}: route or scheduled differ from its row in {where}")
            totals[(daytype, band, row["slug"])] = _preset_counts(route)
        files = {p.stem for p in (folder / "routes").glob("*.json")}
        if files - rows:
            raise ContractError(f"presets/{name}/routes/{sorted(files - rows)[0]}.json: "
                                f"route has no row in {where}")
    base = {None: system}
    for slug in slugs:
        base[slug] = _load(site, f"routes/{slug}.json")
    for e, doc in base.items():
        totals[("all", "all", e)] = {"scheduled.timepoints": doc["completeness"]["departures_scheduled"],
                                     **{f"scopes.{scope}.summary.n": doc["scopes"][scope]["summary"]["n"]
                                        for scope in SCOPES}}
    _preset_sums(totals, [None, *sorted(slugs)])


def _preset_head(doc: dict, where: str, daytype: str, band: str) -> None:
    """A page preset's "preset" names its folder, and its "scheduled" counts show service."""
    if doc["preset"] != {"daytype": daytype, "band": band}:
        raise ContractError(f"{where}: preset is {doc['preset']!r}, expected "
                            f"{{'daytype': {daytype!r}, 'band': {band!r}}} from its folder")
    _keys(doc["scheduled"], SCOPES, f"{where}: scheduled")
    for scope in SCOPES:
        n = doc["scheduled"][scope]
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            raise ContractError(f"{where}: scheduled.{scope} is {n!r}, expected a count")
    if doc["scheduled"]["all_stops"] == 0:
        raise ContractError(f"{where}: scheduled.all_stops is 0; a preset with no scheduled departures has no file")


# The counts that add up across page presets (_preset_sums).
PRESET_SUMS = (*(f"scheduled.{scope}" for scope in SCOPES), *(f"scopes.{scope}.summary.n" for scope in SCOPES))


def _preset_counts(doc: dict) -> dict:
    """A preset file's PRESET_SUMS counts."""
    return {**{f"scheduled.{scope}": doc["scheduled"][scope] for scope in SCOPES},
            **{f"scopes.{scope}.summary.n": doc["scopes"][scope]["summary"]["n"] for scope in SCOPES}}


def _preset_sums(totals: dict, entities: list) -> None:
    """Within a day type the bands add up to its all-day preset, and within a band the day types
    add up to all days; all-all is the files themselves (whose scheduled.timepoints is
    completeness.departures_scheduled). A preset without a file counts 0."""
    def file(daytype, band, e):
        folder = "" if (daytype, band) == ("all", "all") else f"presets/{preset_name(daytype, band)}/"
        return folder + ("system.json" if e is None else f"routes/{e}.json")

    groups = [((dt, "all"), [(dt, b) for b in MAP_BANDS[1:]], (dt, "*")) for dt in MAP_DAYTYPES]
    groups += [(("all", b), [(dt, b) for dt in DAYTYPES], ("*", b)) for b in MAP_BANDS]
    for e in entities:
        for whole, parts, label in groups:
            expected = totals.get((*whole, e))
            for key, want in (expected or dict.fromkeys(PRESET_SUMS, 0)).items():
                total = sum(totals.get((*part, e), {}).get(key, 0) for part in parts)
                if total != want:
                    raise ContractError(f"{file(*whole, e)}: {key} is {want}, but {file(*label, e)} "
                                        f"add up to {total}")


def validate_trips(folder: Path) -> None:
    """Per-trip downloads: named by month, with TRIPS_COLUMNS, every row in that month."""
    for path in sorted(folder.iterdir()):
        month = path.name[len("trips_"):-len(".csv")]
        if not re.fullmatch(r"\d{4}-\d{2}", month) or path.name != trips_file(month):
            raise ContractError(f"trips/{path.name}: expected trips_YYYY-MM.csv")
        with open(path, newline="", encoding="utf-8") as f:
            rows = csv.reader(f)
            if tuple(next(rows, ())) != TRIPS_COLUMNS:
                raise ContractError(f"trips/{path.name}: columns aren't {', '.join(TRIPS_COLUMNS)}")
            for i, row in enumerate(rows, start=2):
                if (len(row) != len(TRIPS_COLUMNS) or not row[0].startswith(month + "-")
                        or row[-1] not in CARRY_OVER.values()):
                    raise ContractError(f"trips/{path.name}: line {i} is malformed")
