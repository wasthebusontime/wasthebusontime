"""The period's stop events and trips, and the questions the site files and open data ask of them.

A Stage is built on a DuckDB connection on which the caller has already provided three relations:

  ev  stop events, one row per scheduled or seen stop of a trip (the columns events.py writes)
  tr  trip facts, one row per trip and service date
  al  service alerts, one row per alert and service date (the columns alerts.py writes)

In production they are views over the day Parquet files (sitefiles.prepare); tests make small
in-memory tables. The tables derived from them stay inside this module: callers ask questions.

Departures can be grouped by these dimensions (perf keys): date, month, day_type, hour, band,
scope, route, direction_id, stop, is_first. Every departure appears once per scope it counts in.

only() narrows a Stage to one day type and band, for the page presets.
"""

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb
from wbot_site.contract import DAYTYPES, MAP_BANDS, MAP_CELL, MAP_DAYTYPES, SCOPES

from . import alerts, rules, timeutil
from .config import Settings
from .patterns import Cell
from .perf import PerfQuery, empty
from .quality import uptime_share


@dataclass
class Period:
    start: date
    end: date
    days: list[dict] = field(default_factory=list)   # manifests of processed ("ok") dates in the period

    @property
    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range((self.end - self.start).days + 1)]


EMPTY_TRIP_FACTS = {
    "completeness": {"trips_scheduled": 0, "trips_observed": 0, "departures_scheduled": 0,
                     "departures_observed": 0, "uptime_service_hours": 1.0},
    "trips": {"scheduled": 0, "observed": 0, "not_observed": 0, "cancelled": 0, "added": 0, "skipped_stops": 0},
}


class Stage:
    def __init__(self, settings: Settings, period: Period, con: duckdb.DuckDBPyConnection,
                 extra_routes: dict[str, list[str]] | None = None):
        """extra_routes: {alert id: route ids} the alerts' tags miss (default: alert_routes.toml)."""
        self.settings, self.period, self._con = settings, period, con
        rules.register(con)
        # Departures, once per scope they count in.
        cols = f"""strftime(service_date, '%Y-%m-%d') AS date, strftime(service_date, '%Y-%m') AS month, day_type,
                  service_hour AS hour, {rules.band('service_hour')} AS band,
                  route_slug AS route, coalesce(direction_id, 0) AS direction_id, stop_code AS stop, is_first,
                  delay_dep_s AS delay"""
        con.execute(f"""
            CREATE TABLE d AS
            SELECT 'all_stops' AS scope, {cols} FROM ev WHERE {rules.DEPARTURE}
            UNION ALL
            SELECT 'timepoints' AS scope, {cols} FROM ev WHERE {rules.DEPARTURE} AND is_timepoint
        """)
        # End-of-line arrivals: each trip's last stop.
        con.execute(f"""
            CREATE TABLE eol AS
            SELECT strftime(service_date, '%Y-%m') AS month, day_type, {rules.band('service_hour')} AS band,
                   route_slug AS route, delay_arr_s AS delay, method
            FROM ev WHERE {rules.END_OF_LINE}
        """)
        # Scheduled departures, counted, by day type, band, route and whether at a timepoint.
        con.execute(f"""
            CREATE TABLE sd AS
            SELECT day_type, {rules.band('service_hour')} AS band, route_slug AS route, is_timepoint, count(*) AS n
            FROM ev WHERE {rules.SCHEDULED_DEPARTURE} GROUP BY ALL
        """)
        self._pq = PerfQuery(con, "d")
        self._eol = PerfQuery(con, "eol", extra={"before_schedule": rules.before_schedule("delay"),
                                                 "from_block": "(method = 'block')", "from_gps": "(method = 'gps')"})
        self._flag_detours(alerts.load_extra_routes() if extra_routes is None else extra_routes)
        self._only = {}

    def only(self, daytype: str, band: str) -> "Stage":
        """This Stage narrowed to one day type and band (from MAP_DAYTYPES and MAP_BANDS; "all" is
        no filter), on the same connection. Departures, end-of-line arrivals and scheduled
        departures are filtered, with two exceptions in perf_blocks: by_hour keeps every band
        and by_daytype every day type. Trips, quality and the network as scheduled are not."""
        if daytype not in MAP_DAYTYPES or band not in MAP_BANDS:
            raise ValueError(f"no preset {daytype}-{band}")
        narrowed = copy.copy(self)
        narrowed._only = {k: v for k, v in (("day_type", daytype), ("band", band)) if v != "all"}
        return narrowed

    def _where(self, *dims: str) -> str:
        """only()'s filter on the given dimensions (default: day_type and band), as SQL."""
        return " AND ".join(f"{d} = '{v}'" for d, v in self._only.items() if d in (dims or self._only)) or "TRUE"

    def _daily_dates(self) -> list[date]:
        """The dates of the "daily" list: the last daily_days of the period, of only()'s day type."""
        dates = self.period.dates[-self.settings.daily_days:]
        if "day_type" in self._only:
            dates = [d for d in dates if timeutil.daytype(d) == self._only["day_type"]]
        return dates

    def close(self) -> None:
        self._con.close()

    # Departures.

    def perf(self, keys: list[str], *, terminal: bool = False, hist: bool = False,
             percentiles: bool = True) -> dict[tuple, dict]:
        """Perf blocks per group of `keys` (dimensions). terminal: only terminal departures, all stops."""
        where = "scope = 'all_stops' AND is_first" if terminal else "TRUE"
        return self._pq.get(keys, where=f"{where} AND {self._where()}", hist=hist, percentiles=percentiles)

    def perf_blocks(self, entity: str | None, daily: bool) -> dict[tuple, dict]:
        """{(scope, entity value or None): block} with summary, by_hour, by_daytype,
        daily (optional) and monthly, for every value of `entity` (a dimension) at once.
        Under only(), by_hour follows the day type alone and by_daytype the band alone."""
        pq, ek, where = self._pq, ([entity] if entity else []), self._where()
        summary = pq.get(["scope", *ek], where=where, hist=True)
        by_hour = pq.get(["scope", *ek, "hour"], where=self._where("day_type"))
        by_daytype = pq.get(["scope", *ek, "day_type"], where=self._where("band"))
        monthly = pq.get(["scope", *ek, "month"], where=where)
        by_date = pq.get(["scope", *ek, "date"], where=where, percentiles=False) if daily else {}
        dates = self._daily_dates()
        hours, months = _by_base(by_hour), _by_base(monthly)
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
                block["monthly"] = [{"month": m, "partial": self._month_partial(m), **v}
                                    for m, v in months.get(base, [])]
                blocks[(scope, e)] = block
        return blocks

    def empty_block(self, daily: bool) -> dict:
        """A perf_blocks block with no departures."""
        block = {"summary": empty(hist=True), "by_hour": [], "by_daytype": {dt: empty() for dt in DAYTYPES}}
        if daily:
            block["daily"] = [{"date": d.isoformat(), **empty(percentiles=False)} for d in self._daily_dates()]
        block["monthly"] = []
        return block

    def map_cells(self) -> dict[tuple, list[int]]:
        """Stop map cells (the contract's MAP_CELL counts) per (period, day type, band, scope, stop),
        where period, day type and band can each also be "all". Counts add up, so the finest grain
        is queried once and the "all" cells are sums."""
        cells = {}
        w = rules.window_counts("delay")
        counts_sql = ", ".join("count(*)" if name == "n" else f"count(*) FILTER (WHERE {w[name]})" for name in MAP_CELL)
        for scope, month, dt, b, stop, *counts in self._con.execute(f"""
            SELECT scope, month, day_type, band, stop, {counts_sql}
            FROM d GROUP BY ALL
        """).fetchall():
            for period in ("all", month):
                for dkey in ("all", dt):
                    for bkey in ("all", b):
                        c = cells.setdefault((period, dkey, bkey, scope, stop), [0] * len(MAP_CELL))
                        for i, v in enumerate(counts):
                            c[i] += v
        return cells

    # End-of-line arrivals.

    def end_of_line(self, keys: list[str]) -> dict[tuple, dict]:
        """{key: {n, early, p10, p50, p90, n_block, n_gps}} per group of `keys` (month, route); early is
        any time before the schedule; n_block and n_gps count arrivals by where they came from."""
        return {key: {"n": p["n"], "early": p["before_schedule"], "p10": p["p10"], "p50": p["p50"], "p90": p["p90"],
                      "n_block": p["from_block"], "n_gps": p["from_gps"]}
                for key, p in self._eol.get(keys, where=self._where()).items()}

    # Scheduled departures.

    def scheduled_departures(self, entity: str | None) -> dict:
        """{route (entity "route") or None: {"timepoints": n, "all_stops": n}} for every route or the
        system with any; timepoints matches completeness's departures_scheduled."""
        ek = "route" if entity else "NULL"
        return {e: {"timepoints": int(tp), "all_stops": int(n)} for e, tp, n in self._con.execute(f"""
            SELECT {ek} AS e, coalesce(sum(n) FILTER (WHERE is_timepoint), 0), sum(n)
            FROM sd WHERE {self._where()} GROUP BY e
        """).fetchall()}

    # Detours.

    def _flag_detours(self, extra_routes: dict[str, list[str]]) -> None:
        """Table dw: each DETOUR alert's active windows per route; dtr: the scheduled trips
        they flag (on the route, overlapping a window)."""
        con = self._con
        rows = con.execute("""
            SELECT alert_id, arg_max(periods, last_seen), min(first_seen), max(last_seen),
                   list_distinct(flatten(list(routes)))
            FROM al WHERE effect = 'DETOUR' GROUP BY alert_id
        """).fetchall()
        windows = []
        for alert_id, periods, first, last, routes in rows:
            spans = alerts.windows([(p["start"], p["end"]) for p in periods or []], first, last)
            for route in sorted(set(routes) | set(extra_routes.get(alert_id, ()))):
                windows += [(alert_id, route, lo, hi) for lo, hi in spans]
        con.execute("CREATE TABLE dw (alert_id VARCHAR, route_id VARCHAR, lo BIGINT, hi BIGINT)")
        if windows:
            con.executemany("INSERT INTO dw VALUES (?, ?, ?, ?)", windows)
        con.execute("""
            CREATE TABLE dtr AS
            SELECT DISTINCT dw.alert_id, tr.service_date, tr.trip_id, tr.route_slug,
                   coalesce(tr.direction_id, 0) AS direction_id
            FROM dw JOIN tr ON tr.route_id = dw.route_id AND tr.scheduled
                           AND tr.start_ts < dw.hi AND dw.lo < tr.end_ts
        """)

    def detours(self) -> list[dict]:
        """Per DETOUR alert and route, if it flagged any trip in the period: its active time
        (first to last window), the alert text and how many trips it flagged. By start."""
        rows = self._con.execute("""
            WITH t AS (SELECT alert_id, route_slug, count(DISTINCT (service_date, trip_id)) AS trips
                       FROM dtr GROUP BY ALL),
                 w AS (SELECT dw.alert_id, r.route_slug, min(dw.lo) AS lo, max(dw.hi) AS hi
                       FROM dw JOIN (SELECT DISTINCT route_id, route_slug FROM tr) r USING (route_id) GROUP BY ALL),
                 a AS (SELECT alert_id, arg_max(header, last_seen) AS header,
                              arg_max(description, last_seen) AS description FROM al GROUP BY alert_id)
            SELECT t.alert_id, t.route_slug, w.lo, w.hi, a.header, a.description, t.trips
            FROM t JOIN w USING (alert_id, route_slug) JOIN a USING (alert_id)
            ORDER BY w.lo, t.route_slug, t.alert_id
        """).fetchall()
        return [{"alert_id": aid, "route": slug, "start": timeutil.iso_utc(lo), "end": timeutil.iso_utc(hi),
                 "header": header or "", "description": description or "", "trips": trips}
                for aid, slug, lo, hi, header, description, trips in rows]

    def detour_trips(self, keys: list[str]) -> dict[tuple, int]:
        """Trips flagged by any detour, per group of keys (date, route, direction_id)."""
        cols = {"date": "strftime(service_date, '%Y-%m-%d')", "route": "route_slug", "direction_id": "direction_id"}
        sel = ", ".join(cols[k] for k in keys)
        return {tuple(r[:-1]): r[-1] for r in self._con.execute(
            f"SELECT {sel}, count(DISTINCT (service_date, trip_id)) FROM dtr GROUP BY ALL").fetchall()}

    # Trips.

    def trip_facts(self, entity: str | None) -> dict:
        """completeness and trips blocks per route (entity "route") or for the system (None)."""
        s = self.settings
        ek = "route_slug" if entity else "NULL"
        rows = self._con.execute(f"""
            SELECT {ek} AS e,
                   count(*) FILTER (WHERE scheduled) AS scheduled,
                   count(*) FILTER (WHERE {rules.trip_observed(s.trip_coverage_min)}) AS observed,
                   count(*) FILTER (WHERE scheduled AND trip_sr = 'CANCELED') AS cancelled,
                   count(*) FILTER (WHERE trip_sr = 'ADDED') AS added,
                   coalesce(sum(skipped_stops), 0) AS skipped,
                   coalesce(sum(eligible_timepoints) FILTER (WHERE {rules.TRIP_COUNTED}), 0),
                   coalesce(sum(observed_timepoints) FILTER (WHERE {rules.TRIP_COUNTED}), 0)
            FROM tr GROUP BY e
        """).fetchall()
        uptime = uptime_share(self.period.days)
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

    def quality_days(self) -> list[dict]:
        """Completeness and uptime per service date in the period. Keys starting with "_" are internal."""
        s = self.settings
        per_day = {r[0]: r[1:] for r in self._con.execute(f"""
            SELECT strftime(service_date, '%Y-%m-%d'),
                   count(*) FILTER (WHERE scheduled),
                   count(*) FILTER (WHERE {rules.trip_observed(s.trip_coverage_min)}),
                   coalesce(sum(eligible_timepoints) FILTER (WHERE {rules.TRIP_COUNTED}), 0),
                   coalesce(sum(observed_timepoints) FILTER (WHERE {rules.TRIP_COUNTED}), 0),
                   count(*) FILTER (WHERE scheduled AND trip_sr = 'CANCELED'),
                   count(*) FILTER (WHERE trip_sr = 'ADDED'),
                   coalesce(sum(skipped_stops), 0)
            FROM tr GROUP BY 1
        """).fetchall()}
        days = []
        for m in self.period.days:
            d = m["service_date"]
            sched, obs, deps, deps_obs, cancelled, added, skipped = per_day.get(d, (0, 0, 0, 0, 0, 0, 0))
            days.append({"date": d, "trips_scheduled": sched, "trips_observed": obs,
                         "departures_scheduled": int(deps), "departures_observed": int(deps_obs),
                         "uptime_service_hours": uptime_share([m]),
                         "_cancelled": cancelled, "_added": added, "_skipped": int(skipped)})
        return days

    def trip_rows(self, month: str) -> list[tuple]:
        """Scheduled trips of one month (YYYY-MM) for the per-trip download, by date and start:
        (date, route, direction_id, trip_id, start_ts, then the rest of contract.TRIPS_COLUMNS)."""
        return self._con.execute("""
            SELECT strftime(service_date, '%Y-%m-%d'), route_slug, coalesce(direction_id, 0), trip_id, start_ts,
                   start_delay_s, end_delay_s, block_id, prev_trip_id, sched_layover_s, prev_end_delay_s, carry_over
            FROM tr WHERE scheduled AND strftime(service_date, '%Y-%m') = ?
            ORDER BY service_date, start_ts, trip_id
        """, [month]).fetchall()

    def carry_over_cells(self) -> list[Cell]:
        """The carry-over measure's counts (patterns.py), per service date, route, direction,
        first stop, day type and band (of the first stop's scheduled departure): over scheduled
        trips with a known carry-over value, a counted first-stop departure and at least one
        late departure, their late departures at all stops (headline window, first stop
        included), those on trips that left the first stop late right after a carry-over,
        and the trips."""
        late = rules.window_counts("delay_dep_s")["late"]
        start_late = rules.window_counts("t.start_delay_s")["late"]
        rows = self._con.execute(f"""
            WITH t AS (
                SELECT service_date, trip_id, carry_over AND {start_late} AS matched
                FROM tr t WHERE scheduled AND carry_over IS NOT NULL AND start_delay_s IS NOT NULL
            ),
            f AS (
                SELECT service_date, trip_id, route_slug, coalesce(direction_id, 0) AS direction_id, stop_code,
                       day_type, {rules.band('service_hour')} AS band
                FROM ev WHERE scheduled AND is_first
            ),
            n AS (SELECT service_date, trip_id, count(*) AS late FROM ev WHERE {rules.DEPARTURE} AND {late} GROUP BY ALL)
            SELECT strftime(t.service_date, '%Y-%m-%d'), f.route_slug, f.direction_id, f.stop_code, f.day_type, f.band,
                   sum(n.late), coalesce(sum(n.late) FILTER (WHERE t.matched), 0), count(*)
            FROM t JOIN f USING (service_date, trip_id) JOIN n USING (service_date, trip_id)
            GROUP BY ALL ORDER BY ALL
        """).fetchall()
        return [Cell(d, r, int(di), s, dt, b, int(late), int(matched), int(trips))
                for d, r, di, s, dt, b, late, matched, trips in rows]

    def route_spans(self) -> dict[str, list[tuple[int, int]]]:
        """Per route, the (first departure, last arrival) of its scheduled trips on each service date."""
        out = {}
        for slug, lo, hi in self._con.execute(
                "SELECT route_slug, min(start_ts), max(end_ts) FROM tr WHERE scheduled GROUP BY route_slug, service_date"
        ).fetchall():
            out.setdefault(slug, []).append((lo, hi))
        return out

    # The network as scheduled.

    def served_routes(self) -> list[str]:
        """Routes with scheduled trips in the period, in no particular order."""
        return [r[0] for r in self._con.execute("SELECT DISTINCT route_slug FROM tr WHERE scheduled").fetchall()]

    def stop_routes(self) -> tuple[dict[str, set], dict[str, set]]:
        """({stop: routes serving it}, {stop: routes for which it is a timed stop})."""
        serving, timed = {}, {}
        for code, slug in self._con.execute("SELECT DISTINCT stop_code, route_slug FROM ev WHERE scheduled").fetchall():
            serving.setdefault(code, set()).add(slug)
        for code, slug in self._con.execute(
                f"SELECT DISTINCT stop_code, route_slug FROM ev WHERE scheduled AND {rules.TIMED_STOP}").fetchall():
            timed.setdefault(code, set()).add(slug)
        return serving, timed

    def route_stop_order(self) -> dict[tuple[str, int], list[tuple[str, bool]]]:
        """Stops of each route and direction in route order: by their average relative
        position along the trips that serve them. Value: [(code, timed stop here)]."""
        out = {}
        for slug, direction, code, _, timepoint in self._con.execute(f"""
            WITH t AS (
                SELECT route_slug, coalesce(direction_id, 0) AS direction_id, stop_code, is_timepoint, is_last,
                       (row_number() OVER w - 1) / greatest(count(*) OVER (PARTITION BY service_date, trip_id) - 1, 1) AS rel
                FROM ev WHERE scheduled
                WINDOW w AS (PARTITION BY service_date, trip_id ORDER BY stop_sequence)
            )
            SELECT route_slug, direction_id, stop_code, avg(rel) AS pos, bool_or({rules.TIMED_STOP})
            FROM t GROUP BY ALL ORDER BY route_slug, direction_id, pos, stop_code
        """).fetchall():
            out.setdefault((slug, direction), []).append((code, timepoint))
        return out

    def _month_partial(self, month: str) -> bool:
        year, m = map(int, month.split("-"))
        first = date(year, m, 1)
        last = date(year + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        return first < self.period.start or last > self.period.end


def _by_base(table: dict[tuple, dict]) -> dict[tuple, list[tuple]]:
    """{(a, b, last): v} -> {(a, b): [(last, v), ...] sorted by last}."""
    out = {}
    for key in sorted(table, key=lambda k: k[-1]):
        out.setdefault(key[:-1], []).append((key[-1], table[key]))
    return out
