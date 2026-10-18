"""The period's stop events and trips, and the questions the site files and open data ask of them.

A Stage is built on a DuckDB connection on which the caller has already provided two relations:

  ev  stop events, one row per scheduled or seen stop of a trip (the columns events.py writes)
  tr  trip facts, one row per trip and service date

In production they are views over the day Parquet files (sitefiles.prepare); tests make small
in-memory tables. The tables derived from them stay inside this module: callers ask questions.

Departures can be grouped by these dimensions (perf keys): date, month, day_type, hour, band,
scope, route, direction_id, stop, is_first. Every departure appears once per scope it counts in.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import duckdb

from . import rules
from .config import DAYTYPES, SCOPES, Settings
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
    def __init__(self, settings: Settings, period: Period, con: duckdb.DuckDBPyConnection):
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
            SELECT strftime(service_date, '%Y-%m') AS month, route_slug AS route, delay_arr_s AS delay
            FROM ev WHERE {rules.END_OF_LINE}
        """)
        self._pq = PerfQuery(con, "d")
        self._eol = PerfQuery(con, "eol", extra={"before_schedule": rules.before_schedule("delay")})

    def close(self) -> None:
        self._con.close()

    # Departures.

    def perf(self, keys: list[str], *, terminal: bool = False, hist: bool = False,
             percentiles: bool = True) -> dict[tuple, dict]:
        """Perf blocks per group of `keys` (dimensions). terminal: only terminal departures, all stops."""
        where = "scope = 'all_stops' AND is_first" if terminal else "TRUE"
        return self._pq.get(keys, where=where, hist=hist, percentiles=percentiles)

    def perf_blocks(self, entity: str | None, daily: bool) -> dict[tuple, dict]:
        """{(scope, entity value or None): block} with summary, by_hour, by_daytype,
        daily (optional) and monthly, for every value of `entity` (a dimension) at once."""
        pq, ek = self._pq, ([entity] if entity else [])
        summary = pq.get(["scope", *ek], hist=True)
        by_hour = pq.get(["scope", *ek, "hour"])
        by_daytype = pq.get(["scope", *ek, "day_type"])
        monthly = pq.get(["scope", *ek, "month"])
        by_date = pq.get(["scope", *ek, "date"], percentiles=False) if daily else {}
        dates = self.period.dates[-self.settings.daily_days:]
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
            block["daily"] = [{"date": d.isoformat(), **empty(percentiles=False)}
                              for d in self.period.dates[-self.settings.daily_days:]]
        block["monthly"] = []
        return block

    def map_cells(self) -> dict[tuple, list[int]]:
        """Stop map counts [n, early, on_time, late, on_time_alt] per (period, day type, band, scope, stop),
        where period, day type and band can each also be "all". Counts add up, so the finest grain
        is queried once and the "all" cells are sums."""
        cells = {}
        w = rules.window_counts("delay")
        filters = ", ".join(f"count(*) FILTER (WHERE {w[name]})" for name in ("early", "on_time", "late", "on_time_alt"))
        for scope, month, dt, b, stop, *counts in self._con.execute(f"""
            SELECT scope, month, day_type, band, stop, count(*), {filters}
            FROM d GROUP BY ALL
        """).fetchall():
            for period in ("all", month):
                for dkey in ("all", dt):
                    for bkey in ("all", b):
                        c = cells.setdefault((period, dkey, bkey, scope, stop), [0, 0, 0, 0, 0])
                        for i, v in enumerate(counts):
                            c[i] += v
        return cells

    # End-of-line arrivals.

    def end_of_line(self, keys: list[str]) -> dict[tuple, dict]:
        """{key: {n, early, p10, p50, p90}} per group of `keys` (month, route); early is any time before the schedule."""
        return {key: {"n": p["n"], "early": p["before_schedule"], "p10": p["p10"], "p50": p["p50"], "p90": p["p90"]}
                for key, p in self._eol.get(keys).items()}

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
