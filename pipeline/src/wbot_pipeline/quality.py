"""Data quality from the day manifests: uptime and data-loss windows.

Used for quality.json, the notices and quality_daily.csv / data_loss.csv, so each
rule has one definition. Completeness per day comes from the trips (Stage.quality_days).
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


def public_day(day: dict) -> dict:
    return {k: v for k, v in day.items() if not k.startswith("_")}
