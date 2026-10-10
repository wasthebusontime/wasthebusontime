"""Loads the published statistics (JSON contract, schema 1) and formats numbers for display.

The stats directory has the same layout as the stats repo: `site/*.json` plus `csv/*.csv`.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from markupsafe import Markup, escape

log = logging.getLogger("wbot_site")

SCHEMA = 1
SCOPES = ("timepoints", "all_stops")
SCOPE_LABELS = {"timepoints": "Timepoints", "all_stops": "All stops"}
DAYTYPES = ("weekday", "saturday", "sunday")
DAYTYPE_LABELS = {"weekday": "Weekday", "saturday": "Saturday", "sunday": "Sunday"}

# The labels describe these exact windows, so the build refuses any others.
WINDOWS = {"headline": [-60, 300], "alt": [0, 300]}
WINDOW_LABELS = {
    "headline": "On time (1 min early to 5 min late)",
    "alt": "On time (Intercity Transit's own definition: 0 to 5 min late)",
}
# Stop map presets, written by the pipeline as site/map/{period}/{daytype}-{band}.json.
MAP_DAYTYPES = ("all", *DAYTYPES)
MAP_BANDS = ("all", "early", "am_peak", "midday", "pm_peak", "evening")
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

    @property
    def map_dir(self) -> Path:
        return self.root / "site" / "map"

    @property
    def routes_geojson(self) -> Path:
        return self.root / "site" / "routes.geojson"

    def map_periods(self) -> list[str]:
        """Periods with stop map presets ("all" first, then months), or [] if the stats have no map."""
        stops = self.index["stops"]
        if not stops or not all("lat" in s and "lon" in s for s in stops) or not self.routes_geojson.is_file():
            return []
        periods = sorted(p.name for p in self.map_dir.iterdir() if p.is_dir()) if self.map_dir.is_dir() else []
        return ["all", *[p for p in periods if p != "all"]] if "all" in periods else []


def check_map(stats: Stats) -> None:
    """Every preset file must exist and list every stop, in stops.json order."""
    n = len(stats.index["stops"])
    for period in stats.map_periods():
        for daytype in MAP_DAYTYPES:
            for band in MAP_BANDS:
                path = stats.map_dir / period / f"{daytype}-{band}.json"
                doc = load_json(path)
                if len(doc["timepoints"]) != n or len(doc["all_stops"]) != n:
                    raise StatsError(f"{path}: {len(doc['all_stops'])} stops, stops.json has {n}")


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
    check_map(stats)
    return stats


# Display helpers. Percentages are computed here from counts so that one rounding
# rule and one minimum-sample rule apply everywhere.


def percent(count: int, n: int, digits: int = 0) -> str:
    if n == 0:
        return "n/a"
    value = (Decimal(count) * 100 / Decimal(n)).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP)
    return f"{value}%"


def fraction(share: float, digits: int = 0) -> str:
    """0.62 -> '62%'. For shares the pipeline already computed (completeness, uptime)."""
    value = (Decimal(str(share)) * 100).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP)
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
    amount = f"{m} min {s} sec" if m and s else f"{m} min" if m else f"{s} sec"
    return f"{amount} {'early' if seconds < 0 else 'late'}"


# Stop names carry the agency's direction codes ("Example St & 1st Ave [sb]"). Pages show
# them as written, with the word for screen readers.
DIRECTIONS = {"nb": "northbound", "sb": "southbound", "eb": "eastbound", "wb": "westbound"}
DIRECTION_CODE = re.compile(r"\[(nb|sb|eb|wb)\]")


def directions_html(text: str) -> Markup:
    """Escaped text with each [sb]-style code read out as its word."""
    return Markup(DIRECTION_CODE.sub(
        lambda m: f'<span aria-hidden="true">{m.group(0)}</span><span class="visually-hidden">{DIRECTIONS[m.group(1)]}</span>',
        str(escape(text)),
    ))


def directions_text(text: str) -> str:
    """Plain text with each code as its word in brackets, for tab titles."""
    return DIRECTION_CODE.sub(lambda m: f"({DIRECTIONS[m.group(1)]})", text)


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
