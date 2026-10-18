"""Loads the published statistics and formats numbers for display.

The format of the stats directory (schema 1) is defined and checked in contract.py.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from markupsafe import Markup, escape

from wbot_site import agency, contract
from wbot_site.windows import Windows, from_meta

log = logging.getLogger("wbot_site")

SCOPE_LABELS = {"timepoints": "Timepoints", "all_stops": "All stops"}
DAYTYPE_LABELS = {"weekday": "Weekday", "saturday": "Saturday", "sunday": "Sunday"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class StatsError(Exception):
    """The stats can't be used for this build."""


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    def windows(self) -> Windows:
        return from_meta(self.meta.get("windows"))

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
        return contract.map_periods(self.root)


def load_stats(stats_dir: Path) -> Stats:
    """The stats, once they pass the contract's check and their windows can be shown."""
    site = stats_dir / "site"
    try:
        contract.validate(stats_dir)
        meta = load_json(site / "meta.json")
        from_meta(meta["windows"])
    except ValueError as e:   # ContractError is one
        raise StatsError(str(e)) from None
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
    return tuple(perf[contract.count_key(part, window)] for part in contract.PARTS)


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
    """2026-10-16T13:17:30Z -> Oct 16, 2026, 6:17 am (the agency's local time)"""
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(agency.TZ)
    return f"{date_text(t.date().isoformat())}, {t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def period_text(meta: dict) -> str:
    period = meta["period"]
    if period["key"] == "all":
        return f"since collection began on {date_text(period['start'])}"
    return f"{date_text(period['start'])} to {date_text(period['end'])}"


def alert_headline(header: str) -> str:
    """An alert's first non-empty line. Intercity Transit puts the whole message in the header."""
    return next((line.strip() for line in header.splitlines() if line.strip()), "")


def alert_details(header: str, description: str) -> str:
    """Everything after the headline: the rest of the header, then the description."""
    lines = header.strip().splitlines()
    rest = "\n".join(lines[1:]).strip()
    return "\n\n".join(part for part in (rest, description.strip()) if part)


def known_notices(notices: list[dict], where: str) -> list[dict]:
    """Unknown codes are skipped with a warning, so the pipeline can add codes first."""
    known = []
    for notice in notices:
        if notice.get("code") in contract.NOTICE_CODES:
            known.append(notice)
        else:
            log.warning("%s: ignoring unknown notice code %r", where, notice.get("code"))
    return known
