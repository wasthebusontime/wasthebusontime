"""Data quality per service date: completeness, uptime and data-loss windows.

Used for quality.json, the notices and quality_daily.csv / data_loss.csv, so each
rule has one definition.
"""

from . import timeutil


def uptime_share(days: list[dict]) -> float:
    service = sum(m["service_s"] for m in days)
    lost = sum(m["lost_service_s"] for m in days)
    return round(1 - lost / service, 4) if service else 1.0


def data_loss(stage) -> list[dict]:
    seen, out = set(), []
    for m in stage.period.days:
        for g in m["gaps"]:
            key = (g["start"], g["end"])
            if g["end"] - g["start"] > stage.settings.gap_tolerance_s and key not in seen:
                seen.add(key)
                item = {"start": timeutil.iso_utc(g["start"]), "end": timeutil.iso_utc(g["end"]), "cause": g["cause"]}
                if g.get("note"):
                    item["note"] = g["note"]
                out.append((g["start"], item))
    return [item for _, item in sorted(out, key=lambda x: x[0])]


def quality_days(stage) -> list[dict]:
    s = stage.settings
    per_day = {r[0]: r[1:] for r in stage.con.execute(f"""
        SELECT strftime(service_date, '%Y-%m-%d'),
               count(*) FILTER (WHERE scheduled),
               count(*) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED' AND eligible_timepoints > 0
                                AND observed_timepoints >= {s.trip_coverage_min} * eligible_timepoints),
               coalesce(sum(eligible_timepoints) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED'), 0),
               coalesce(sum(observed_timepoints) FILTER (WHERE scheduled AND coalesce(trip_sr, '') <> 'CANCELED'), 0),
               count(*) FILTER (WHERE scheduled AND trip_sr = 'CANCELED'),
               count(*) FILTER (WHERE trip_sr = 'ADDED'),
               coalesce(sum(skipped_stops), 0)
        FROM tr GROUP BY 1
    """).fetchall()}
    days = []
    for m in stage.period.days:
        d = m["service_date"]
        sched, obs, deps, deps_obs, cancelled, added, skipped = per_day.get(d, (0, 0, 0, 0, 0, 0, 0))
        days.append({"date": d, "trips_scheduled": sched, "trips_observed": obs,
                     "departures_scheduled": int(deps), "departures_observed": int(deps_obs),
                     "uptime_service_hours": uptime_share([m]),
                     "_cancelled": cancelled, "_added": added, "_skipped": int(skipped)})
    return days


def public_day(day: dict) -> dict:
    return {k: v for k, v in day.items() if not k.startswith("_")}
