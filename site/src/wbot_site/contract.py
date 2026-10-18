"""The site files format (schema 1): the names both sides use, and a check of a stats directory.

The pipeline writes these files and the site reads them; both import their names from here,
so a name is defined once. Standard library only: the pipeline imports this module without
the site's own dependencies.

A stats directory has the same layout as the stats repo:
  site/meta.json, system.json, stops.json, quality.json, routes/{slug}.json,
  stops/{code}.json, routes.geojson, map/{period}/{daytype}-{band}.json
  csv/*.csv
"""

import json
from pathlib import Path

SCHEMA = 1
SCOPES = ("timepoints", "all_stops")
DAYTYPES = ("weekday", "saturday", "sunday")
# Stop map time-of-day bands by service hour: (key, first hour, last hour + 1).
BANDS = (("early", 0, 6), ("am_peak", 6, 9), ("midday", 9, 15), ("pm_peak", 15, 18), ("evening", 18, 48))
# Stop map presets: one file per period, day type and band; "all" means no filter.
MAP_DAYTYPES = ("all", *DAYTYPES)
MAP_BANDS = ("all", *(key for key, _, _ in BANDS))
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
    notice without its fields, or a map preset that is missing or the wrong size."""
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
                name = f"map/{period}/{daytype}-{band}.json"
                _map(_load(site, name), name, n)


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
    _notices(doc["notices"], where)


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
