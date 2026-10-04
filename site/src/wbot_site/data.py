"""Loads the published statistics (JSON contract, schema 1) and formats numbers for display.

The stats directory has the same layout as the stats repo: `site/*.json` plus `csv/*.csv`.
"""

import json
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

log = logging.getLogger("wbot_site")

SCHEMA = 1
SCOPES = ("timepoints", "all_stops")
SCOPE_LABELS = {"timepoints": "Timepoints", "all_stops": "All stops"}
DAYTYPES = ("weekday", "saturday", "sunday")
DAYTYPE_LABELS = {"weekday": "Weekday", "saturday": "Saturday", "sunday": "Sunday"}

# The labels describe these exact windows, so the build refuses any others.
WINDOWS = {"headline": [0, 300], "alt": [-60, 300]}
WINDOW_LABELS = {
    "headline": "On time (Intercity Transit's definition: 0 to 5 min late)",
    "alt": "On time (1 min early to 5 min late)",
}
NOTICE_CODES = {"data_loss", "low_completeness", "provisional", "methodology_change"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class StatsError(Exception):
    """The stats can't be used for this build."""


def load_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StatsError(f"missing {path}") from None
    if doc.get("schema") != SCHEMA:
        raise StatsError(f"{path}: schema {doc.get('schema')!r}, this generator knows schema {SCHEMA}")
    return doc


def has_stats(stats_dir: Path) -> bool:
    return (stats_dir / "site" / "meta.json").is_file()


@dataclass
class Stats:
    root: Path
    meta: dict
    system: dict
    index: dict
    quality: dict
    routes: dict[str, dict] = field(default_factory=dict)
    stops: dict[str, dict] = field(default_factory=dict)

    @property
    def synthetic(self) -> bool:
        return bool(self.meta.get("synthetic"))

    @property
    def csv_files(self) -> list[Path]:
        return sorted((self.root / "csv").glob("*.csv"))


def load_stats(stats_dir: Path) -> Stats:
    site = stats_dir / "site"
    meta = load_json(site / "meta.json")
    if meta.get("windows") != WINDOWS:
        raise StatsError(f"meta.windows is {meta.get('windows')}, the page labels describe {WINDOWS}")
    system = load_json(site / "system.json")
    stats = Stats(
        root=stats_dir,
        meta=meta,
        system=system,
        index=load_json(site / "stops.json"),
        quality=load_json(site / "quality.json"),
    )
    for route in system["routes"]:
        stats.routes[route["slug"]] = load_json(site / "routes" / f"{route['slug']}.json")
    for stop in stats.index["stops"]:
        stats.stops[stop["code"]] = load_json(site / "stops" / f"{stop['code']}.json")
    return stats


# Display helpers. Percentages are computed here from counts so that one rounding
# rule and one minimum-sample rule apply everywhere.


def percent(count: int, n: int, digits: int = 0) -> str:
    value = (Decimal(count) * 100 / Decimal(n)).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP)
    return f"{value}%"


def enough(perf: dict | None, min_sample: int) -> bool:
    return perf is not None and perf["n"] >= min_sample


def split(perf: dict, window: str = "headline") -> tuple[int, int, int]:
    suffix = "_alt" if window == "alt" else ""
    return perf["early" + suffix], perf["on_time" + suffix], perf["late" + suffix]


def number(n: int) -> str:
    return f"{n:,}"


def delay_text(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    if seconds == 0:
        return "on schedule"
    m, s = divmod(abs(seconds), 60)
    amount = f"{m} min {s} s" if m and s else f"{m} min" if m else f"{s} s"
    return f"{amount} {'early' if seconds < 0 else 'late'}"


def date_text(iso: str) -> str:
    year, month, day = iso.split("-")
    return f"{MONTHS[int(month) - 1]} {int(day)}, {year}"


def month_text(iso: str) -> str:
    year, month = iso.split("-")[:2]
    return f"{MONTHS[int(month) - 1]} {year}"


def hour_text(hour: int) -> str:
    h = hour % 24
    label = f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"
    return f"{label} (next day)" if hour >= 24 else label


def timestamp_text(iso: str) -> str:
    """2026-10-04T04:32:23Z -> 2026-10-04 04:32 UTC"""
    return f"{iso[:10]} {iso[11:16]} UTC"


def period_text(meta: dict) -> str:
    period = meta["period"]
    if period["key"] == "all":
        return f"since collection began on {date_text(period['start'])}"
    return f"{date_text(period['start'])} to {date_text(period['end'])}"


def known_notices(notices: list[dict], where: str) -> list[dict]:
    """Unknown codes are skipped with a warning, so the pipeline can add codes first."""
    known = []
    for notice in notices:
        if notice.get("code") in NOTICE_CODES:
            known.append(notice)
        else:
            log.warning("%s: ignoring unknown notice code %r", where, notice.get("code"))
    return known
