"""Stop events -> the site files (schema 1, defined and checked in wbot_site.contract).

Everything is recomputed from all stop events in the period on every run, which takes
seconds: counts can't drift, and a threshold change applies everywhere at once. The numbers
come from a Stage (stage.py); this module lays them out as files.

Output layout (written to a fresh directory, then swapped in):
  site/meta.json, system.json, stops.json, quality.json, routes/{slug}.json,
  stops/{code}.json, routes.geojson, map/{period}/{daytype}-{band}.json
  csv/*.csv (see opendata.py)
"""

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wbot_site import contract
from wbot_site.contract import MAP_BANDS, MAP_CELL, MAP_DAYTYPES, SCHEMA, SCOPES

from . import events, opendata, rules, shapes, static, timeutil
from .config import ALT, EVENTS_VERSION, HEADLINE, Settings
from .db import connect, quote
from .perf import empty
from .quality import data_loss, public_day
from .stage import EMPTY_TRIP_FACTS, Period, Stage

log = logging.getLogger(__name__)


def route_order(slug: str):
    """Numeric route names in number order, then the rest alphabetically."""
    digits = "".join(c for c in slug if c.isdigit())
    return (0, int(digits), slug) if digits and slug[: len(digits)] == digits else (1, 0, slug)


@dataclass
class Names:
    """Route and stop names and coordinates from the static versions used in the period."""
    routes: dict = field(default_factory=dict)      # slug -> {short_name, long_name, route_id}
    stops: dict = field(default_factory=dict)       # code -> {stop_id, name, lat, lon}
    headsigns: dict = field(default_factory=dict)   # (slug, direction) -> headsign


def find_period(settings: Settings) -> Period | None:
    days = []
    for path in sorted((settings.derived_dir / "days").glob("*.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        d = date.fromisoformat(m["service_date"])
        if m["status"] == "ok" and d >= settings.first_service_date:
            days.append(m)
    if not days:
        return None
    return Period(settings.first_service_date, date.fromisoformat(days[-1]["service_date"]), days)


def prepare(settings: Settings, period: Period, db_path: Path) -> Stage:
    """A Stage over the period's stop event and trip Parquet files."""
    con = connect(settings, db_path)
    files = [events.paths(settings, date.fromisoformat(m["service_date"])) for m in period.days]
    ev_list = ", ".join(f"'{quote(p.events)}'" for p in files)
    tr_list = ", ".join(f"'{quote(p.trips)}'" for p in files)
    con.execute(f"CREATE VIEW ev AS SELECT * FROM read_parquet([{ev_list}], union_by_name = true)")
    con.execute(f"CREATE VIEW tr AS SELECT * FROM read_parquet([{tr_list}], union_by_name = true)")
    return Stage(settings, period, con)


def load_names(settings: Settings, period: Period, versions: list[static.Version]) -> Names:
    """Versions used in the period are read oldest first, so the most current one wins."""
    names = Names()
    used = {static.choose(versions, d) for d in period.dates} - {None}
    con = connect(settings)
    rules.register(con)
    for v in sorted(used, key=lambda v: (v.fetched_at or 0, v.stem)):
        cache = static.ensure_cache(settings.derived_dir, v)
        static.register(con, cache, prefix="s_")
        for slug, short, long, route_id in con.execute(
                "SELECT route_slug(coalesce(route_short_name, route_id)), coalesce(route_short_name, route_id), "
                "coalesce(route_long_name, ''), route_id FROM s_routes").fetchall():
            names.routes[slug] = {"short_name": short, "long_name": long, "route_id": route_id}
        for code, stop_id, name, lat, lon in con.execute(
                "SELECT stop_slug(coalesce(stop_code, stop_id)), stop_id, coalesce(stop_name, stop_id), "
                "TRY_CAST(stop_lat AS DOUBLE), TRY_CAST(stop_lon AS DOUBLE) FROM s_stops").fetchall():
            names.stops[code] = {"stop_id": stop_id, "name": name, "lat": lat, "lon": lon}
        for slug, direction, headsign in con.execute("""
                SELECT route_slug(coalesce(r.route_short_name, r.route_id)), coalesce(TRY_CAST(t.direction_id AS INTEGER), 0),
                       mode(t.trip_headsign) FROM s_trips t JOIN s_routes r USING (route_id)
                WHERE t.trip_headsign IS NOT NULL GROUP BY ALL""").fetchall():
            names.headsigns[(slug, direction)] = headsign
    con.close()
    return names


# Building blocks.


def strip(p: dict, hist: bool = False, percentiles: bool = True) -> dict:
    p = dict(p)
    if not hist:
        p.pop("hist", None)
    if not percentiles:
        for k in ("p10", "p50", "p90"):
            p.pop(k, None)
    return p


def end_of_line(stage: Stage, entity: str | None) -> dict:
    out = {}
    for key, p in stage.end_of_line([entity] if entity else []).items():
        out[key[0] if entity else None] = {
            "n": p["n"], "p10": p["p10"], "p50": p["p50"], "p90": p["p90"],
            "early_share": round(p["early"] / p["n"], 3),
        }
    return out


EMPTY_EOL = {"n": 0, "p10": None, "p50": None, "p90": None, "early_share": 0.0}


def system_notices(stage: Stage, loss: list[dict], days: list[dict]) -> list[dict]:
    s = stage.settings
    notices = [{"code": "provisional"}] if s.provisional else []
    notices += [{"code": "data_loss", "start": g["start"], "end": g["end"], "cause": g["cause"]} for g in loss]
    for day in days:
        if day["departures_scheduled"]:
            share = day["departures_observed"] / day["departures_scheduled"]
            if share < s.low_completeness_below:
                notices.append({"code": "low_completeness", "date": day["date"], "share": round(share, 2)})
    notices += [{"code": "methodology_change", "date": d, "version": v} for d, v in s.methodology_changes]
    return notices


# The files.


def build(settings: Settings, out: Path, *, pipeline_version: str, generated_at: str,
          synthetic: bool = False) -> bool:
    """Write the site files and CSVs into `out` (replaced). False if there is nothing to publish.
    The files are checked against the contract before they replace `out`, so a failed check
    (ContractError) leaves the last good output in place. synthetic marks invented data."""
    period = find_period(settings)
    if period is None:
        log.warning("no processed service dates yet; nothing to write")
        return False
    versions = static.list_versions(settings.static_dir)
    names = load_names(settings, period, versions)
    tmp_root = settings.derived_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_root / "site.duckdb"
    db_path.unlink(missing_ok=True)
    stage = prepare(settings, period, db_path)
    try:
        staging = out.with_name(out.name + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        write_all(stage, names, versions, staging, pipeline_version, generated_at, synthetic)
        opendata.write(stage, names, staging / "csv", settings.derived_dir / "agg")
    finally:
        stage.close()
        db_path.unlink(missing_ok=True)
    contract.validate(staging)
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    staging.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    return True


def write_all(stage: Stage, names: Names, versions: list[static.Version], out: Path,
              pipeline_version: str, generated_at: str, synthetic: bool = False) -> None:
    site = out / "site"
    s, period = stage.settings, stage.period
    used = sorted({m["static"]["feed_version"] for m in period.days})
    digest = hashlib.sha256()
    for m in period.days:
        digest.update(m["static"]["sha256"].encode())
        for p in m["packs"]:
            digest.update(f"{p['path']} {p['sha256']}\n".encode())
    meta = {
        "schema": SCHEMA,
        "synthetic": synthetic,
        "generated_at": generated_at,
        "data_through": period.end.isoformat(),
        "collection_start": period.start.isoformat(),
        "pipeline_version": pipeline_version,
        "methodology_version": s.methodology_version,
        "static_versions": used,
        "min_sample": s.min_sample,
        "windows": {"headline": list(HEADLINE), "alt": list(ALT)},
        "period": {"key": "all", "start": period.start.isoformat(), "end": period.end.isoformat()},
        "events_version": EVENTS_VERSION,
        "inputs_sha256": digest.hexdigest(),
    }
    write(site / "meta.json", meta)

    loss = data_loss(stage)
    days = stage.quality_days()
    facts_sys = stage.trip_facts(None).get(None) or EMPTY_TRIP_FACTS
    route_facts = stage.trip_facts("route")
    sys_blocks = stage.perf_blocks(None, daily=True)
    route_blocks = stage.perf_blocks("route", daily=True)
    terminal = stage.perf(["route"], terminal=True, hist=True)
    terminal_sys = stage.perf([], terminal=True, hist=True)
    eol_sys, eol_routes = end_of_line(stage, None), end_of_line(stage, "route")

    # Routes: every route with scheduled trips in the period.
    slugs = sorted(stage.served_routes(), key=route_order)
    route_rows = []
    for slug in slugs:
        info = names.routes.get(slug, {"short_name": slug, "long_name": "", "route_id": slug})
        route_rows.append({
            "slug": slug, "short_name": info["short_name"], "long_name": info["long_name"],
            "timepoints": strip(route_blocks.get(("timepoints", slug), {"summary": empty()})["summary"]),
            "all_stops": strip(route_blocks.get(("all_stops", slug), {"summary": empty()})["summary"]),
        })
    write(site / "system.json", {
        "schema": SCHEMA,
        "scopes": {scope: sys_blocks.get((scope, None)) or stage.empty_block(daily=True) for scope in SCOPES},
        "terminal": terminal_sys.get((), empty(hist=True)),
        "end_of_line": eol_sys.get(None, EMPTY_EOL),
        "completeness": facts_sys["completeness"],
        "trips": facts_sys["trips"],
        "routes": route_rows,
        "notices": system_notices(stage, loss, days),
    })
    write(site / "quality.json", {"schema": SCHEMA, "days": [public_day(d) for d in days], "data_loss": loss})

    write_routes(stage, names, site, slugs, route_blocks, terminal, eol_routes, route_facts, loss)
    index = write_stops(stage, names, site)
    write_map(stage, site, index)
    write_geojson(stage, names, versions, site, slugs)


def write_routes(stage, names, site, slugs, route_blocks, terminal, eol_routes, route_facts, loss) -> None:
    by_direction = stage.perf(["scope", "route", "direction_id"])
    by_stop = stage.perf(["scope", "route", "direction_id", "stop"])
    order = stage.route_stop_order()
    spans = stage.route_spans()
    notices_base = [{"code": "provisional"}] if stage.settings.provisional else []
    for slug in slugs:
        info = names.routes.get(slug, {"short_name": slug, "long_name": "", "route_id": slug})
        directions = sorted(d for (r, d) in order if r == slug)
        scopes = {}
        for scope in SCOPES:
            block = dict(route_blocks.get((scope, slug)) or stage.empty_block(daily=True))
            block["by_direction"] = [{
                "direction_id": d, "headsign": headsign(names, slug, d, order),
                **by_direction.get((scope, slug, d), empty()),
            } for d in directions]
            scopes[scope] = block
        stop_lists = []
        for d in directions:
            rows = []
            for code, timepoint in order[(slug, d)]:
                rows.append({
                    "code": code, "name": names.stops.get(code, {}).get("name", code), "timepoint": timepoint,
                    "timepoints": by_stop.get(("timepoints", slug, d, code), empty()) if timepoint else None,
                    "all_stops": by_stop.get(("all_stops", slug, d, code), empty()),
                })
            stop_lists.append({"direction_id": d, "stops": rows})
        facts = route_facts.get(slug) or EMPTY_TRIP_FACTS
        windows = spans.get(slug, [])
        notices = notices_base + [
            {"code": "data_loss", "start": g["start"], "end": g["end"], "cause": g["cause"]} for g in loss
            if any(timeutil.parse_iso_utc(g["start"]) < hi and lo < timeutil.parse_iso_utc(g["end"])
                   for lo, hi in windows)]
        write(site / "routes" / f"{slug}.json", {
            "schema": SCHEMA,
            "route": {"slug": slug, "short_name": info["short_name"], "long_name": info["long_name"],
                      "route_id": info["route_id"]},
            "scopes": scopes,
            "terminal": terminal.get((slug,), empty(hist=True)),
            "end_of_line": eol_routes.get(slug, EMPTY_EOL),
            "completeness": facts["completeness"],
            "trips": facts["trips"],
            "stops": stop_lists,
            "notices": notices,
        })


def headsign(names: Names, slug: str, direction: int, order) -> str:
    if (slug, direction) in names.headsigns:
        return names.headsigns[(slug, direction)]
    last = order[(slug, direction)][-1][0]
    return names.stops.get(last, {}).get("name", last)


def write_stops(stage: Stage, names: Names, site: Path) -> list[dict]:
    serving, timepoint_routes = stage.stop_routes()
    timepoint_somewhere = set(timepoint_routes)
    blocks = stage.perf_blocks("stop", daily=False)
    by_route = stage.perf(["scope", "stop", "route"])
    index = []
    for code in sorted(serving, key=lambda c: (names.stops.get(c, {}).get("name", c), c)):
        info = names.stops.get(code, {"stop_id": code, "name": code, "lat": None, "lon": None})
        routes = sorted(serving[code], key=route_order)
        scopes = {}
        for scope in SCOPES:
            if scope == "timepoints" and code not in timepoint_somewhere:
                scopes[scope] = None
                continue
            block = dict(blocks.get((scope, code)) or stage.empty_block(daily=False))
            block["by_route"] = [{
                "slug": slug, "short_name": names.routes.get(slug, {}).get("short_name", slug),
                **by_route.get((scope, code, slug), empty()),
            } for slug in routes]
            scopes[scope] = block
        write(site / "stops" / f"{code}.json", {
            "schema": SCHEMA,
            "stop": {"code": code, "stop_id": info["stop_id"], "name": info["name"],
                     "timepoint_somewhere": code in timepoint_somewhere,
                     "timepoint_routes": sorted(timepoint_routes.get(code, ()), key=route_order)},
            "scopes": scopes,
            "notices": [],
        })
        entry = {"code": code, "name": info["name"], "routes": routes}
        if info["lat"] is not None and info["lon"] is not None:
            entry.update(lat=info["lat"], lon=info["lon"])
        index.append((entry, code in timepoint_somewhere))
    write(site / "stops.json", {"schema": SCHEMA, "stops": [e for e, _ in index]})
    return index


def write_map(stage: Stage, site: Path, index: list) -> None:
    """Per-stop counts for every preset of the stop map."""
    if not index or not all("lat" in e for e, _ in index):
        return
    cells = stage.map_cells()
    months = sorted({k[0] for k in cells if k[0] != "all"})
    for period in ["all", *months]:
        for dt in MAP_DAYTYPES:
            for b in MAP_BANDS:
                doc = {"schema": SCHEMA, "timepoints": [], "all_stops": []}
                for entry, is_tp in index:
                    code = entry["code"]
                    doc["all_stops"].append(cells.get((period, dt, b, "all_stops", code), [0] * len(MAP_CELL)))
                    doc["timepoints"].append(cells.get((period, dt, b, "timepoints", code), [0] * len(MAP_CELL))
                                             if is_tp else None)
                write(site / "map" / period / f"{dt}-{b}.json", doc, compact=True)


def write_geojson(stage: Stage, names: Names, versions: list[static.Version], site: Path, slugs: list[str]) -> None:
    """One simplified line per route and direction, from the most current static version."""
    v = static.most_current(versions, stage.period.start, stage.period.end)
    if v is None:
        return
    con = connect(stage.settings)
    rules.register(con)
    static.register(con, static.ensure_cache(stage.settings.derived_dir, v), prefix="g_")
    if not con.execute("SELECT count(*) FROM g_shapes").fetchone()[0]:
        con.close()
        return
    rows = con.execute("""
        WITH pick AS (
            SELECT route_slug(coalesce(r.route_short_name, r.route_id)) AS slug,
                   coalesce(TRY_CAST(t.direction_id AS INTEGER), 0) AS direction_id, mode(t.shape_id) AS shape_id
            FROM g_trips t JOIN g_routes r USING (route_id) WHERE t.shape_id IS NOT NULL GROUP BY ALL
        )
        SELECT p.slug, p.direction_id, CAST(s.shape_pt_lat AS DOUBLE), CAST(s.shape_pt_lon AS DOUBLE)
        FROM pick p JOIN g_shapes s ON s.shape_id = p.shape_id
        ORDER BY p.slug, p.direction_id, CAST(s.shape_pt_sequence AS INTEGER)
    """).fetchall()
    con.close()
    lines = {}
    for slug, direction, lat, lon in rows:
        lines.setdefault((slug, direction), []).append((lat, lon))
    wanted = set(slugs)
    features = []
    for (slug, direction), points in sorted(lines.items(), key=lambda kv: (route_order(kv[0][0]), kv[0][1])):
        if slug not in wanted:
            continue
        features.append({
            "type": "Feature",
            "properties": {"slug": slug, "short_name": names.routes.get(slug, {}).get("short_name", slug),
                           "direction_id": direction},
            "geometry": {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in shapes.simplify(points)]},
        })
    write(site / "routes.geojson", {"type": "FeatureCollection", "features": features}, compact=True)


def write(path: Path, doc: dict, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(doc, separators=(",", ":"), ensure_ascii=False)
    else:
        text = json.dumps(doc, indent=1, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
