"""One service date: archive + schedule -> stop events, trip facts and a day manifest.

Writes, under derived/:
  events/YYYY/YYYY-MM-DD.parquet  one row per scheduled stop of every active trip, plus
                                  any stop the feed reported that the schedule doesn't have
  trips/YYYY/YYYY-MM-DD.parquet   one row per trip, with its block, the previous trip on the
                                  block, the layover and whether it was a carry-over
  days/YYYY-MM-DD.json            what was read (packs and their sha256, static version),
                                  versions, gaps in collection, and a few counts
  alerts/YYYY/YYYY-MM-DD.parquet  every service alert seen in the same hours (alerts.py)

Unobserved stops are kept with observed = false; nothing is ever guessed.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
from wbot_collector import layout

from . import alerts, archive, dateplan, gaps, gps, rules, static, timeutil
from .config import EVENTS_VERSION, MAX_BLOCK_LAYOVER_S, MIN_SEEN_AFTER_DEP_S, Settings
from .db import connect, quote
from .rt import Reduced

log = logging.getLogger(__name__)

FEED = layout.TRIPUPDATES
# Every feed a date reads. Their packs are listed in the day manifest, so a late pack in any
# of them gets the date reprocessed.
FEEDS = (layout.TRIPUPDATES, layout.ALERTS, layout.VEHICLEPOSITIONS)
# Read packs from this long before the first scheduled departure (trips appear in the
# feed ahead of time) to this long after the last scheduled arrival.
READ_BEFORE_S = 2 * 3600
READ_AFTER_S = 3600

@dataclass
class Paths:
    events: Path
    trips: Path
    manifest: Path


def paths(settings: Settings, d: date) -> Paths:
    root = settings.derived_dir
    return Paths(
        events=root / "events" / f"{d:%Y}" / f"{d.isoformat()}.parquet",
        trips=root / "trips" / f"{d:%Y}" / f"{d.isoformat()}.parquet",
        manifest=root / "days" / f"{d.isoformat()}.json",
    )


def read_manifest(settings: Settings, d: date) -> dict | None:
    p = paths(settings, d).manifest
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pack_list(settings: Settings, hour_list: list[int]) -> list[dict]:
    out = []
    for feed in FEEDS:
        for hour in hour_list:
            for p in archive.packs_for_hour(settings.archive_dir, feed, hour):
                out.append({"path": p.relative_to(settings.data_dir).as_posix(), "size": p.stat().st_size})
    return out


def day(settings: Settings, versions: list[static.Version], d: date) -> dateplan.Day:
    """What the files say about a date, for the date plan."""
    m = read_manifest(settings, d)
    v = static.choose(versions, d)
    packs = pack_list(settings, archive.hours(*m["hours"])) if m and "hours" in m else []
    return dateplan.Day(d, m, v.stem if v else None, packs)


def _schedule(settings: Settings, v: static.Version, d: date):
    """A connection with the date's scheduled stops (table sched) and the static views, and
    the scheduled service span (first departure, last arrival), None if nothing runs."""
    cache = static.ensure_cache(settings.derived_dir, v)
    con = connect(settings)
    rules.register(con)
    static.register(con, cache)
    services = static.active_services(con, d)
    con.execute("""
        CREATE TEMP TABLE sched AS
        SELECT st.trip_id, t.route_id, TRY_CAST(t.direction_id AS INTEGER) AS direction_id,
               CAST(st.stop_sequence AS INTEGER) AS stop_sequence, st.stop_id,
               gtfs_s(coalesce(st.arrival_time, st.departure_time)) AS sched_arr_s,
               gtfs_s(coalesce(st.departure_time, st.arrival_time)) AS sched_dep_s,
               coalesce(st.timepoint = '1' OR (st.timepoint IS NULL AND st.departure_time IS NOT NULL), false)
                   AS is_timepoint
        FROM stop_times st JOIN trips t ON st.trip_id = t.trip_id
        WHERE list_contains(?, t.service_id)
    """, [services])
    first_dep, last_arr = con.execute("SELECT min(sched_dep_s), max(sched_arr_s) FROM sched").fetchone()
    if first_dep is None:
        return con, None
    o = timeutil.origin(d)
    return con, (o + first_dep, o + last_arr)


def _hours(span: tuple[int, int]) -> list[int]:
    return archive.hours(span[0] - READ_BEFORE_S, span[1] + READ_AFTER_S)


def hours_read(settings: Settings, versions: list[static.Version], d: date) -> list[int] | None:
    """The UTC hour starts processing a date reads, or None if it reads none (no static
    version or no service)."""
    v = static.choose(versions, d)
    if v is None:
        return None
    con, span = _schedule(settings, v, d)
    con.close()
    return _hours(span) if span else None


def _write_manifest(target: Path, doc: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def block_pairs(con) -> None:
    """Table block_pairs: each scheduled trip with a block and the trip before it on that block
    (by scheduled start, within the service), whether the pair passes the pairing rules (same
    bus, the next trip starts where the previous one ends, a scheduled layover under
    MAX_BLOCK_LAYOVER_S) and the next trip's recorded arrival at its first stop: the bus's
    arrival from the previous trip. One row per trip that has a previous trip."""
    con.execute(f"""
        CREATE TEMP TABLE block_pairs AS
        WITH tb AS (
            SELECT e.trip_id, t.block_id, t.service_id, min(e.sched_dep_ts) AS start_ts
            FROM ev e JOIN trips t ON t.trip_id = e.trip_id
            WHERE e.scheduled AND coalesce(t.block_id, '') <> ''
            GROUP BY ALL
        ),
        p AS (SELECT trip_id AS a, lead(trip_id) OVER (PARTITION BY block_id, service_id
                                                       ORDER BY start_ts, trip_id) AS b FROM tb)
        SELECT p.b AS trip_id, p.a AS prev_trip_id,
               CAST(fb.sched_dep_ts - la.sched_arr_ts AS INTEGER) AS sched_layover_s,
               coalesce(la.stop_id = fb.stop_id AND ra.last_vehicle_id = rb.first_vehicle_id
                        AND fb.sched_dep_ts - la.sched_arr_ts < {MAX_BLOCK_LAYOVER_S}, false) AS paired,
               CASE WHEN fb.observed_arr THEN fb.act_arr_ts END AS first_arr_ts,
               fb.sched_dep_ts AS first_dep_ts
        FROM p
        JOIN ev la ON la.trip_id = p.a AND la.is_last
        JOIN ev fb ON fb.trip_id = p.b AND fb.is_first
        LEFT JOIN rt_trips ra ON ra.trip_id = p.a
        LEFT JOIN rt_trips rb ON rb.trip_id = p.b
        WHERE p.b IS NOT NULL
    """)


def end_of_line(con, positions: gps.Positions) -> None:
    """Fill in arrivals at each trip's last stop, which TripUpdates never lists: from the next
    trip in the same block, when the pair passes the pairing rules (block_pairs) and the next
    trip's arrival at its first stop was recorded (method 'block'); otherwise from the bus's
    GPS (method 'gps'); otherwise they stay not recorded."""
    block_pairs(con)
    con.execute("""
        UPDATE ev SET act_arr_ts = n.first_arr_ts, delay_arr_s = CAST(n.first_arr_ts - ev.sched_arr_ts AS INTEGER),
                      observed_arr = true, method = 'block'
        FROM block_pairs n
        WHERE n.paired AND n.first_arr_ts IS NOT NULL
          AND ev.trip_id = n.prev_trip_id AND ev.is_last AND NOT ev.observed_arr
    """)
    need = con.execute("""
        WITH prev AS (
            SELECT trip_id, stop_sequence,
                   lag(coalesce(act_dep_ts, sched_dep_ts)) OVER (PARTITION BY trip_id ORDER BY stop_sequence) AS left_ts
            FROM ev WHERE scheduled
        )
        SELECT e.trip_id, e.sched_arr_ts, p.left_ts, r.last_vehicle_id,
               TRY_CAST(st.stop_lat AS DOUBLE), TRY_CAST(st.stop_lon AS DOUBLE)
        FROM ev e
        JOIN prev p ON p.trip_id = e.trip_id AND p.stop_sequence = e.stop_sequence
        JOIN rt_trips r ON r.trip_id = e.trip_id
        JOIN stops st ON st.stop_id = e.stop_id
        WHERE e.is_last AND e.scheduled AND NOT e.observed_arr AND r.last_vehicle_id IS NOT NULL
    """).fetchall()
    found = []
    for trip_id, sched_arr, left_ts, vehicle, lat, lon in need:
        if lat is None or lon is None or sched_arr is None:
            continue
        start = left_ts if left_ts is not None else sched_arr - 1800
        arr = gps.arrival(positions.reports(vehicle, start, sched_arr + 1800), (lat, lon))
        if arr is not None:
            found.append((trip_id, arr))
    if found:
        con.execute("CREATE TEMP TABLE gps_arr (trip_id VARCHAR, arr BIGINT)")
        con.executemany("INSERT INTO gps_arr VALUES (?, ?)", found)
        con.execute("""
            UPDATE ev SET act_arr_ts = g.arr, delay_arr_s = CAST(g.arr - ev.sched_arr_ts AS INTEGER),
                          observed_arr = true, method = 'gps'
            FROM gps_arr g WHERE ev.trip_id = g.trip_id AND ev.is_last
        """)


def process_date(settings: Settings, versions: list[static.Version], d: date, pipeline_version: str) -> str:
    """Process one service date (one the date plan says is ready). Returns 'ok', 'no_static'
    or 'no_service'."""
    out = paths(settings, d)
    base = {
        "service_date": d.isoformat(),
        "events_version": EVENTS_VERSION,
        "methodology_version": settings.methodology_version,
        "pipeline_version": pipeline_version,
    }
    v = static.choose(versions, d)
    if v is None:
        log.warning("%s: no static GTFS version covers this date", d)
        _write_manifest(out.manifest, {**base, "status": "no_static", "static": {}})
        return "no_static"

    con, span = _schedule(settings, v, d)
    static_info = {"stem": v.stem, "feed_version": v.feed_version, "sha256": v.sha256}
    if span is None:
        log.info("%s: no scheduled service", d)
        _write_manifest(out.manifest, {**base, "status": "no_service", "static": static_info})
        con.close()
        return "no_service"

    o = timeutil.origin(d)
    hour_list = _hours(span)

    reduced = Reduced()
    seen_alerts = alerts.Alerts()
    positions = gps.Positions()
    packs = []
    day = timeutil.yyyymmdd(d)
    for feed in FEEDS:
        for hour in hour_list:
            hour_packs = archive.packs_for_hour(settings.archive_dir, feed, hour)
            for p in hour_packs:
                packs.append({"path": p.relative_to(settings.data_dir).as_posix(), "size": p.stat().st_size,
                              "sha256": archive.sha256_file(p)})
            for snap, _, data in archive.snapshots(hour_packs):
                if feed == FEED:
                    reduced.add(snap, data, day)
                elif feed == layout.ALERTS:
                    seen_alerts.add(snap, data)
                else:
                    positions.add(data)

    # When trips are scheduled to be running: gaps and uptime only count then.
    service = gaps.merge([(o + a, o + b) for a, b in con.execute(
        "SELECT min(sched_dep_s), max(sched_arr_s) FROM sched GROUP BY trip_id").fetchall()])

    con.register("rt", reduced.stops_table())
    con.register("rt_trips", reduced.trips_table())
    con.execute("""
        CREATE TEMP TABLE s AS
        SELECT *, stop_sequence = min(stop_sequence) OVER (PARTITION BY trip_id) AS is_first,
                  stop_sequence = max(stop_sequence) OVER (PARTITION BY trip_id) AS is_last
        FROM sched
    """)
    for p in (out.events, out.trips):
        p.parent.mkdir(parents=True, exist_ok=True)
    events_tmp = out.events.with_name(out.events.name + ".tmp")
    con.execute(f"""
        CREATE TEMP TABLE ev AS
        SELECT DATE '{d.isoformat()}' AS service_date,
               coalesce(s.trip_id, r.trip_id) AS trip_id,
               coalesce(s.route_id, rt.rt_route_id) AS route_id,
               route_slug(coalesce(ro.route_short_name, s.route_id, rt.rt_route_id)) AS route_slug,
               s.direction_id,
               coalesce(s.stop_sequence, r.stop_sequence) AS stop_sequence,
               coalesce(s.stop_id, r.rt_stop_id) AS stop_id,
               stop_slug(coalesce(so.stop_code, s.stop_id, r.rt_stop_id)) AS stop_code,
               s.trip_id IS NOT NULL AS scheduled,
               coalesce(s.is_timepoint, false) AS is_timepoint,
               coalesce(s.is_first, false) AS is_first,
               coalesce(s.is_last, false) AS is_last,
               {o} + s.sched_arr_s AS sched_arr_ts,
               {o} + s.sched_dep_s AS sched_dep_ts,
               CAST(coalesce(s.sched_dep_s, s.sched_arr_s) // 3600 AS INTEGER) AS service_hour,
               '{timeutil.daytype(d)}' AS day_type,
               r.act_arr_ts, r.act_dep_ts,
               CAST(r.act_dep_ts - ({o} + s.sched_dep_s) AS INTEGER) AS delay_dep_s,
               CAST(r.act_arr_ts - ({o} + s.sched_arr_s) AS INTEGER) AS delay_arr_s,
               r.stop_sr, rt.trip_sr,
               r.trip_id IS NOT NULL AS seen,
               coalesce(r.after_dep AND r.tu_ts >= r.act_dep_ts
                        AND r.last_seen_ts - r.act_dep_ts >= {MIN_SEEN_AFTER_DEP_S}, false) AS observed,
               coalesce(r.after_arr AND r.tu_ts >= r.act_arr_ts, false) AS observed_arr,
               r.last_seen_ts,
               CAST(r.last_seen_ts - r.act_dep_ts AS INTEGER) AS seen_after_dep_s,
               'tu' AS method,
               '{v.stem}' AS static_version,
               {EVENTS_VERSION} AS events_version
        FROM s FULL OUTER JOIN rt r ON s.trip_id = r.trip_id AND s.stop_sequence = r.stop_sequence
        LEFT JOIN rt_trips rt ON rt.trip_id = coalesce(s.trip_id, r.trip_id)
        LEFT JOIN routes ro ON ro.route_id = coalesce(s.route_id, rt.rt_route_id)
        LEFT JOIN stops so ON so.stop_id = coalesce(s.stop_id, r.rt_stop_id)
    """)
    end_of_line(con, positions)
    con.execute(f"COPY (SELECT * FROM ev ORDER BY 2, 6) TO '{quote(events_tmp)}' (FORMAT parquet, COMPRESSION zstd)")
    trips_tmp = out.trips.with_name(out.trips.name + ".tmp")
    con.execute(f"""
        COPY (
        WITH e AS (SELECT * FROM ev),
        a AS (
            SELECT trip_id, bool_or(scheduled) AS scheduled, min(route_id) AS route_id,
                   min(route_slug) AS route_slug, min(direction_id) AS direction_id,
                   count(*) FILTER (WHERE scheduled AND {rules.TIMED_STOP} AND sched_dep_ts IS NOT NULL)
                       AS eligible_timepoints,
                   count(*) FILTER (WHERE scheduled AND {rules.TIMED_STOP} AND sched_dep_ts IS NOT NULL
                                    AND observed AND stop_sr = 'SCHEDULED') AS observed_timepoints,
                   count(*) FILTER (WHERE stop_sr = 'SKIPPED') AS skipped_stops,
                   count(*) FILTER (WHERE stop_sr = 'NO_DATA') AS no_data_stops,
                   min(sched_dep_ts) AS start_ts, max(sched_arr_ts) AS end_ts
            FROM e GROUP BY trip_id
        ),
        -- Delays that count in the published numbers: the first-stop departure, and the
        -- end-of-line arrival (from TripUpdates, the block or GPS).
        dep AS (SELECT trip_id, delay_dep_s FROM e WHERE is_first AND {rules.DEPARTURE}),
        eol AS (SELECT trip_id, delay_arr_s FROM e WHERE {rules.END_OF_LINE})
        SELECT DATE '{d.isoformat()}' AS service_date, a.trip_id, a.route_id, a.route_slug, a.direction_id,
               a.scheduled, rt.trip_id IS NOT NULL AS seen, coalesce(rt.vehicle_seen, false) AS seen_with_vehicle,
               rt.trip_sr, a.eligible_timepoints, a.observed_timepoints, a.skipped_stops, a.no_data_stops,
               a.start_ts, a.end_ts,
               nullif(t.block_id, '') AS block_id, bp.prev_trip_id, bp.sched_layover_s,
               pe.delay_arr_s AS prev_end_delay_s, dep.delay_dep_s AS start_delay_s, eol.delay_arr_s AS end_delay_s,
               CASE WHEN bp.paired AND bp.first_arr_ts IS NOT NULL THEN bp.first_arr_ts > bp.first_dep_ts END
                   AS carry_over
        FROM a LEFT JOIN rt_trips rt USING (trip_id)
        LEFT JOIN trips t ON t.trip_id = a.trip_id AND a.scheduled
        LEFT JOIN block_pairs bp ON bp.trip_id = a.trip_id
        LEFT JOIN eol pe ON pe.trip_id = bp.prev_trip_id
        LEFT JOIN dep ON dep.trip_id = a.trip_id
        LEFT JOIN eol ON eol.trip_id = a.trip_id
        UNION ALL
        SELECT DATE '{d.isoformat()}', rt.trip_id, rt.rt_route_id,
               route_slug(coalesce(ro.route_short_name, rt.rt_route_id)), NULL, false, true, rt.vehicle_seen,
               rt.trip_sr, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM rt_trips rt LEFT JOIN routes ro ON ro.route_id = rt.rt_route_id
        WHERE rt.trip_id NOT IN (SELECT trip_id FROM a)
        ORDER BY 2
        ) TO '{quote(trips_tmp)}' (FORMAT parquet, COMPRESSION zstd)
    """)
    counts = dict(zip(
        ("events", "observed", "rt_trips", "unmatched_trips"),
        con.execute(f"""
            SELECT (SELECT count(*) FROM read_parquet('{quote(events_tmp)}')),
                   (SELECT count(*) FROM read_parquet('{quote(events_tmp)}') WHERE observed),
                   (SELECT count(*) FROM rt_trips),
                   (SELECT count(*) FROM read_parquet('{quote(trips_tmp)}') WHERE NOT scheduled
                        AND coalesce(trip_sr, '') <> 'ADDED')
        """).fetchone(),
    ))
    con.close()
    os.replace(events_tmp, out.events)
    os.replace(trips_tmp, out.trips)
    alerts_path = alerts.path(settings, d)
    alerts_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(seen_alerts.table(), alerts_path.with_name(alerts_path.name + ".tmp"))
    os.replace(alerts_path.with_name(alerts_path.name + ".tmp"), alerts_path)

    fetches = gaps.read_fetches(settings.log_dir, span[0], span[1])
    found = gaps.apply_overrides(gaps.find_gaps(fetches, service), gaps.load_overrides())
    _write_manifest(out.manifest, {
        **base,
        "status": "ok",
        "static": static_info,
        "service_span": [span[0], span[1]],
        "hours": [hour_list[0], hour_list[-1]],
        "packs": packs,
        "snapshots": reduced.snapshots,
        "unreadable_snapshots": reduced.unreadable,
        "gaps": found,
        "service_s": sum(b - a for a, b in service),
        "lost_service_s": gaps.lost_seconds(found, service),
        "counts": counts,
    })
    log.info("%s: %d snapshots, %d events, %d observed", d, reduced.snapshots, counts["events"], counts["observed"])
    return "ok"

