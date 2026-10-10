"""Stop events -> the site files (JSON contract schema 1, see the website notes).

Everything is recomputed from all stop events in the period on every run, which takes
seconds: counts can't drift, and a threshold change applies everywhere at once.

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
from datetime import date, timedelta
from pathlib import Path

import duckdb

from . import events, opendata, shapes, static, timeutil
from .quality import data_loss, public_day, quality_days, uptime_share
from .config import ALT, BANDS, DAYTYPES, EVENTS_VERSION, HEADLINE, SCHEMA, SCOPES, Settings
from .db import connect, quote
from .perf import PerfQuery, empty

log = logging.getLogger(__name__)

MAP_DAYTYPES = ("all", *DAYTYPES)
MAP_BANDS = ("all", *(b[0] for b in BANDS))


def route_order(slug: str):
    """Numeric route names in number order, then the rest alphabetically."""
    digits = "".join(c for c in slug if c.isdigit())
    return (0, int(digits), slug) if digits and slug[: len(digits)] == digits else (1, 0, slug)


@dataclass
class Period:
    start: date
    end: date
    days: list[dict]          # manifests of processed ("ok") dates in the period

    @property
    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


@dataclass
class Stage:
    """A DuckDB connection with the period's data prepared, plus static names."""
    settings: Settings
    con: duckdb.DuckDBPyConnection
    period: Period
    versions: list[static.Version]
    routes: dict = field(default_factory=dict)      # slug -> {short_name, long_name, route_id}
    stops: dict = field(default_factory=dict)       # code -> {stop_id, name, lat, lon}
    headsigns: dict = field(default_factory=dict)   # (slug, direction) -> headsign
    pq: PerfQuery = None
    eol: PerfQuery = None


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


def prepare(settings: Settings, period: Period, versions: list[static.Version], db_path: Path) -> Stage:
    con = connect(settings, db_path)
    for macro in events.MACROS:
        con.execute(macro)
    files = [events.paths(settings, date.fromisoformat(m["service_date"])) for m in period.days]
    ev_list = ", ".join(f"'{quote(p.events)}'" for p in files)
    tr_list = ", ".join(f"'{quote(p.trips)}'" for p in files)
    con.execute(f"CREATE VIEW ev AS SELECT * FROM read_parquet([{ev_list}], union_by_name = true)")
    con.execute(f"CREATE VIEW tr AS SELECT * FROM read_parquet([{tr_list}], union_by_name = true)")
    # Observed, on-time-eligible departures, once per scope they count in.
    eligible = """observed AND scheduled AND stop_sr = 'SCHEDULED' AND trip_sr = 'SCHEDULED'
                  AND NOT is_last AND delay_dep_s IS NOT NULL"""
    cols = """strftime(service_date, '%Y-%m-%d') AS date, strftime(service_date, '%Y-%m') AS month, day_type,
              service_hour AS hour,
              CASE WHEN service_hour < 6 THEN 'early' WHEN service_hour < 9 THEN 'am_peak'
                   WHEN service_hour < 15 THEN 'midday' WHEN service_hour < 18 THEN 'pm_peak' ELSE 'evening' END AS band,
              route_slug AS route, coalesce(direction_id, 0) AS direction_id, stop_code AS stop, is_first,
              delay_dep_s AS delay"""
    con.execute(f"""
        CREATE TABLE d AS
        SELECT 'all_stops' AS scope, {cols} FROM ev WHERE {eligible}
        UNION ALL
        SELECT 'timepoints' AS scope, {cols} FROM ev WHERE {eligible} AND is_timepoint
    """)
    # End-of-line arrivals: each trip's last stop.
    con.execute("""
        CREATE TABLE eol AS
        SELECT strftime(service_date, '%Y-%m') AS month, route_slug AS route, delay_arr_s AS delay
        FROM ev WHERE is_last AND scheduled AND observed_arr AND stop_sr = 'SCHEDULED'
              AND trip_sr = 'SCHEDULED' AND delay_arr_s IS NOT NULL
    """)
    stage = Stage(settings, con, period, versions, pq=PerfQuery(con, "d"), eol=PerfQuery(con, "eol"))
    load_names(stage)
    return stage


def load_names(stage: Stage) -> None:
    """Route and stop names and coordinates. Versions used in the period are read oldest
    first, so the most current one wins."""
    used = {static.choose(stage.versions, d) for d in stage.period.dates} - {None}
    con = stage.con
    for v in sorted(used, key=lambda v: (v.fetched_at or 0, v.stem)):
        cache = static.ensure_cache(stage.settings.derived_dir, v)
        static.register(con, cache, prefix="s_")
        for slug, short, long, route_id in con.execute(
                "SELECT route_slug(coalesce(route_short_name, route_id)), coalesce(route_short_name, route_id), "
                "coalesce(route_long_name, ''), route_id FROM s_routes").fetchall():
            stage.routes[slug] = {"short_name": short, "long_name": long, "route_id": route_id}
        for code, stop_id, name, lat, lon in con.execute(
                "SELECT stop_slug(coalesce(stop_code, stop_id)), stop_id, coalesce(stop_name, stop_id), "
                "TRY_CAST(stop_lat AS DOUBLE), TRY_CAST(stop_lon AS DOUBLE) FROM s_stops").fetchall():
            stage.stops[code] = {"stop_id": stop_id, "name": name, "lat": lat, "lon": lon}
        for slug, direction, headsign in con.execute("""
                SELECT route_slug(coalesce(r.route_short_name, r.route_id)), coalesce(TRY_CAST(t.direction_id AS INTEGER), 0),
                       mode(t.trip_headsign) FROM s_trips t JOIN s_routes r USING (route_id)
                WHERE t.trip_headsign IS NOT NULL GROUP BY ALL""").fetchall():
            stage.headsigns[(slug, direction)] = headsign


# Building blocks.


def strip(p: dict, hist: bool = False, percentiles: bool = True) -> dict:
    p = dict(p)
    if not hist:
        p.pop("hist", None)
    if not percentiles:
        for k in ("p10", "p50", "p90"):
            p.pop(k, None)
    return p


def scope_blocks(stage: Stage, entity: str | None, daily: bool) -> dict[tuple, dict]:
    """{(scope, entity value or None): block} with summary, by_hour, by_daytype,
    daily (optional) and monthly, for every entity at once."""
    pq, ek = stage.pq, ([entity] if entity else [])
    summary = pq.get(["scope", *ek], hist=True)
    by_hour = pq.get(["scope", *ek, "hour"])
    by_daytype = pq.get(["scope", *ek, "day_type"])
    monthly = pq.get(["scope", *ek, "month"])
    by_date = pq.get(["scope", *ek, "date"], percentiles=False) if daily else {}
    dates = stage.period.dates[-stage.settings.daily_days:]
    hours, months = by_base(by_hour), by_base(monthly)
    blocks = {}
    entities = {k[1] if entity else None for k in summary}
    for scope in SCOPES:
        for e in entities:
            base = (scope, e) if entity else (scope,)
            block = {"summary": summary.get(base, empty(hist=True))}
            block["by_hour"] = [{"hour": h, **v} for h, v in hours.get(base, [])]
            block["by_daytype"] = {dt: by_daytype.get((*base, dt), empty()) for dt in DAYTYPES}
            if daily:
                block["daily"] = [{"date": d.isoformat(), **by_date.get((*base, d.isoformat()), empty(percentiles=False))}
                                  for d in dates]
            block["monthly"] = [{"month": m, "partial": month_partial(m, stage.period), **v}
                                for m, v in months.get(base, [])]
            blocks[(scope, e)] = block
    return blocks


def by_base(table: dict[tuple, dict]) -> dict[tuple, list[tuple]]:
    """{(a, b, last): v} -> {(a, b): [(last, v), ...] sorted by last}."""
    out = {}
    for key in sorted(table, key=lambda k: k[-1]):
        out.setdefault(key[:-1], []).append((key[-1], table[key]))
    return out


def month_partial(month: str, period: Period) -> bool:
    year, m = map(int, month.split("-"))
    first = date(year, m, 1)
    last = date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return first < period.start or last > period.end


def end_of_line(stage: Stage, entity: str | None) -> dict:
    out = {}
    for key, p in stage.eol.get([entity] if entity else []).items():
        out[key[0] if entity else None] = {
            "n": p["n"], "p10": p["p10"], "p50": p["p50"], "p90": p["p90"],
            # Early means any time before the schedule, which is the alt window's lower edge.
            "early_share": round(p["early_alt"] / p["n"], 3),
        }
    return out


EMPTY_EOL = {"n": 0, "p10": None, "p50": None, "p90": None, "early_share": 0.0}


def trip_facts(stage: Stage, entity: str | None) -> dict:
    """completeness and trips blocks per entity (None for the system)."""
    s = stage.settings
    ek = "route_slug" if entity else "NULL"
    rows = stage.con.execute(f"""
        SELECT {ek} AS e,
               count(*) FILTER (WHERE scheduled) AS scheduled,
               count(*) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED' AND eligible_timepoints > 0
                                AND observed_timepoints >= {s.trip_coverage_min} * eligible_timepoints) AS observed,
               count(*) FILTER (WHERE scheduled AND trip_sr = 'CANCELED') AS cancelled,
               count(*) FILTER (WHERE trip_sr = 'ADDED') AS added,
               coalesce(sum(skipped_stops), 0) AS skipped,
               coalesce(sum(eligible_timepoints) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED'), 0),
               coalesce(sum(observed_timepoints) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED'), 0)
        FROM tr GROUP BY e
    """).fetchall()
    uptime = uptime_share(stage.period.days)
    out = {}
    for e, scheduled, observed, cancelled, added, skipped, deps, deps_obs in rows:
        out[e] = {
            "completeness": {"trips_scheduled": scheduled, "trips_observed": observed,
                             "departures_scheduled": int(deps), "departures_observed": int(deps_obs),
                             "uptime_service_hours": uptime},
            "trips": {"scheduled": scheduled, "observed": observed, "not_observed": scheduled - observed - cancelled,
                      "cancelled": cancelled, "added": added, "skipped_stops": int(skipped)},
        }
    return out










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


def route_spans(stage: Stage) -> dict[str, list[tuple[int, int]]]:
    out = {}
    for slug, lo, hi in stage.con.execute(
            "SELECT route_slug, min(start_ts), max(end_ts) FROM tr WHERE scheduled GROUP BY route_slug, service_date"
    ).fetchall():
        out.setdefault(slug, []).append((lo, hi))
    return out


# The files.


def build(settings: Settings, out: Path, *, pipeline_version: str, generated_at: str) -> bool:
    """Write the site files and CSVs into `out` (replaced). False if there is nothing to publish."""
    period = find_period(settings)
    if period is None:
        log.warning("no processed service dates yet; nothing to write")
        return False
    versions = static.list_versions(settings.static_dir)
    tmp_root = settings.derived_dir / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_root / "site.duckdb"
    db_path.unlink(missing_ok=True)
    stage = prepare(settings, period, versions, db_path)
    try:
        staging = out.with_name(out.name + ".new")
        shutil.rmtree(staging, ignore_errors=True)
        write_all(stage, staging, pipeline_version, generated_at)
        opendata.write(stage, staging / "csv", settings.derived_dir / "agg")
    finally:
        stage.con.close()
        db_path.unlink(missing_ok=True)
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    staging.rename(out)
    shutil.rmtree(old, ignore_errors=True)
    return True


def write_all(stage: Stage, out: Path, pipeline_version: str, generated_at: str) -> None:
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
        "synthetic": False,
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
    days = quality_days(stage)
    facts_sys = trip_facts(stage, None).get(None) or trip_facts_empty()
    route_facts = trip_facts(stage, "route")
    sys_blocks = scope_blocks(stage, None, daily=True)
    route_blocks = scope_blocks(stage, "route", daily=True)
    terminal = stage.pq.get(["route"], where="scope = 'all_stops' AND is_first", hist=True)
    terminal_sys = stage.pq.get([], where="scope = 'all_stops' AND is_first", hist=True)
    eol_sys, eol_routes = end_of_line(stage, None), end_of_line(stage, "route")

    # Routes: every route with scheduled trips in the period.
    served = [r[0] for r in stage.con.execute("SELECT DISTINCT route_slug FROM tr WHERE scheduled").fetchall()]
    slugs = sorted(served, key=route_order)
    route_rows = []
    for slug in slugs:
        info = stage.routes.get(slug, {"short_name": slug, "long_name": "", "route_id": slug})
        route_rows.append({
            "slug": slug, "short_name": info["short_name"], "long_name": info["long_name"],
            "timepoints": strip(route_blocks.get(("timepoints", slug), {"summary": empty()})["summary"]),
            "all_stops": strip(route_blocks.get(("all_stops", slug), {"summary": empty()})["summary"]),
        })
    write(site / "system.json", {
        "schema": SCHEMA,
        "scopes": {scope: sys_blocks.get((scope, None)) or empty_block(stage, daily=True) for scope in SCOPES},
        "terminal": terminal_sys.get((), empty(hist=True)),
        "end_of_line": eol_sys.get(None, EMPTY_EOL),
        "completeness": facts_sys["completeness"],
        "trips": facts_sys["trips"],
        "routes": route_rows,
        "notices": system_notices(stage, loss, days),
    })
    write(site / "quality.json", {"schema": SCHEMA, "days": [public_day(d) for d in days], "data_loss": loss})

    write_routes(stage, site, slugs, route_blocks, terminal, eol_routes, route_facts, loss)
    index = write_stops(stage, site)
    write_map(stage, site, index)
    write_geojson(stage, site, slugs)


def trip_facts_empty() -> dict:
    return {"completeness": {"trips_scheduled": 0, "trips_observed": 0, "departures_scheduled": 0,
                             "departures_observed": 0, "uptime_service_hours": 1.0},
            "trips": {"scheduled": 0, "observed": 0, "not_observed": 0, "cancelled": 0, "added": 0, "skipped_stops": 0}}


def empty_block(stage: Stage, daily: bool) -> dict:
    block = {"summary": empty(hist=True), "by_hour": [], "by_daytype": {dt: empty() for dt in DAYTYPES}}
    if daily:
        block["daily"] = [{"date": d.isoformat(), **empty(percentiles=False)}
                          for d in stage.period.dates[-stage.settings.daily_days:]]
    block["monthly"] = []
    return block


def route_stop_order(stage: Stage) -> dict[tuple[str, int], list[tuple[str, bool]]]:
    """Stops of each route and direction in route order: by their average relative
    position along the trips that serve them. Value: [(code, timepoint here)]."""
    out = {}
    for slug, direction, code, _, timepoint in stage.con.execute("""
        WITH t AS (
            SELECT route_slug, coalesce(direction_id, 0) AS direction_id, stop_code, is_timepoint, is_last,
                   (row_number() OVER w - 1) / greatest(count(*) OVER (PARTITION BY service_date, trip_id) - 1, 1) AS rel
            FROM ev WHERE scheduled
            WINDOW w AS (PARTITION BY service_date, trip_id ORDER BY stop_sequence)
        )
        SELECT route_slug, direction_id, stop_code, avg(rel) AS pos, bool_or(is_timepoint AND NOT is_last)
        FROM t GROUP BY ALL ORDER BY route_slug, direction_id, pos, stop_code
    """).fetchall():
        out.setdefault((slug, direction), []).append((code, timepoint))
    return out


def write_routes(stage, site, slugs, route_blocks, terminal, eol_routes, route_facts, loss) -> None:
    pq = stage.pq
    by_direction = pq.get(["scope", "route", "direction_id"])
    by_stop = pq.get(["scope", "route", "direction_id", "stop"])
    order = route_stop_order(stage)
    spans = route_spans(stage)
    notices_base = [{"code": "provisional"}] if stage.settings.provisional else []
    for slug in slugs:
        info = stage.routes.get(slug, {"short_name": slug, "long_name": "", "route_id": slug})
        directions = sorted(d for (r, d) in order if r == slug)
        scopes = {}
        for scope in SCOPES:
            block = dict(route_blocks.get((scope, slug)) or empty_block(stage, daily=True))
            block["by_direction"] = [{
                "direction_id": d, "headsign": headsign(stage, slug, d, order),
                **by_direction.get((scope, slug, d), empty()),
            } for d in directions]
            scopes[scope] = block
        stop_lists = []
        for d in directions:
            rows = []
            for code, timepoint in order[(slug, d)]:
                rows.append({
                    "code": code, "name": stage.stops.get(code, {}).get("name", code), "timepoint": timepoint,
                    "timepoints": by_stop.get(("timepoints", slug, d, code), empty()) if timepoint else None,
                    "all_stops": by_stop.get(("all_stops", slug, d, code), empty()),
                })
            stop_lists.append({"direction_id": d, "stops": rows})
        facts = route_facts.get(slug) or trip_facts_empty()
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


def headsign(stage: Stage, slug: str, direction: int, order) -> str:
    if (slug, direction) in stage.headsigns:
        return stage.headsigns[(slug, direction)]
    last = order[(slug, direction)][-1][0]
    return stage.stops.get(last, {}).get("name", last)


def write_stops(stage: Stage, site: Path) -> list[dict]:
    con, pq = stage.con, stage.pq
    serving = {}
    for code, slug in con.execute("SELECT DISTINCT stop_code, route_slug FROM ev WHERE scheduled").fetchall():
        serving.setdefault(code, set()).add(slug)
    # Routes for which each stop is a timepoint (a departure from it is timed).
    timepoint_routes = {}
    for code, slug in con.execute(
            "SELECT DISTINCT stop_code, route_slug FROM ev WHERE scheduled AND is_timepoint AND NOT is_last").fetchall():
        timepoint_routes.setdefault(code, set()).add(slug)
    timepoint_somewhere = set(timepoint_routes)
    blocks = scope_blocks(stage, "stop", daily=False)
    by_route = pq.get(["scope", "stop", "route"])
    index = []
    for code in sorted(serving, key=lambda c: (stage.stops.get(c, {}).get("name", c), c)):
        info = stage.stops.get(code, {"stop_id": code, "name": code, "lat": None, "lon": None})
        routes = sorted(serving[code], key=route_order)
        scopes = {}
        for scope in SCOPES:
            if scope == "timepoints" and code not in timepoint_somewhere:
                scopes[scope] = None
                continue
            block = dict(blocks.get((scope, code)) or empty_block(stage, daily=False))
            block["by_route"] = [{
                "slug": slug, "short_name": stage.routes.get(slug, {}).get("short_name", slug),
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
    """Per-stop counts for every preset of the stop map. Counts add up, so the finest
    grain is queried once and the "all" presets are sums."""
    if not index or not all("lat" in e for e, _ in index):
        return
    cells = {}
    (h_lo, h_hi), (a_lo, a_hi) = HEADLINE, ALT
    for scope, month, dt, b, stop, n, early, on_time, late, on_time_alt in stage.con.execute(f"""
        SELECT scope, month, day_type, band, stop, count(*),
               count(*) FILTER (WHERE delay < {h_lo}), count(*) FILTER (WHERE delay BETWEEN {h_lo} AND {h_hi}),
               count(*) FILTER (WHERE delay > {h_hi}), count(*) FILTER (WHERE delay BETWEEN {a_lo} AND {a_hi})
        FROM d GROUP BY ALL
    """).fetchall():
        for period in ("all", month):
            for dkey in ("all", dt):
                for bkey in ("all", b):
                    key = (period, dkey, bkey, scope, stop)
                    c = cells.setdefault(key, [0, 0, 0, 0, 0])
                    for i, v in enumerate((n, early, on_time, late, on_time_alt)):
                        c[i] += v
    months = sorted({k[0] for k in cells if k[0] != "all"})
    for period in ["all", *months]:
        for dt in MAP_DAYTYPES:
            for b in MAP_BANDS:
                doc = {"schema": SCHEMA, "timepoints": [], "all_stops": []}
                for entry, is_tp in index:
                    code = entry["code"]
                    doc["all_stops"].append(cells.get((period, dt, b, "all_stops", code), [0, 0, 0, 0, 0]))
                    doc["timepoints"].append(cells.get((period, dt, b, "timepoints", code), [0, 0, 0, 0, 0])
                                             if is_tp else None)
                write(site / "map" / period / f"{dt}-{b}.json", doc, compact=True)


def write_geojson(stage: Stage, site: Path, slugs: list[str]) -> None:
    """One simplified line per route and direction, from the most current static version."""
    v = static.most_current(stage.versions, stage.period.start, stage.period.end)
    if v is None:
        return
    con = stage.con
    static.register(con, static.ensure_cache(stage.settings.derived_dir, v), prefix="g_")
    if not con.execute("SELECT count(*) FROM g_shapes").fetchone()[0]:
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
            "properties": {"slug": slug, "short_name": stage.routes.get(slug, {}).get("short_name", slug),
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
