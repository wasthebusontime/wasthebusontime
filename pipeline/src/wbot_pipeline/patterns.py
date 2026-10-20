"""Patterns: conditions many late departures share, found by fixed, published rules (ADR 0002).

Numbers only: the site turns them into sentences. The thresholds are constants in config.py
(PATTERNS_RULES_VERSION and the PATTERN_ and CARRY_OVER_ names); changing one and rerunning
the pipeline is all it takes.

Carry-over patterns, for a group (a route, direction and first stop):

- The measure: of the group's late departures (headline window, counted departures at all
  stops, the first stop included) on trips with a known carry-over value and a counted
  first-stop departure, the share on trips that left their first stop late right after a
  carry-over (`matched` of `late`). Stage.carry_over_cells counts them per day, day type and band.
- A slot: the whole period (a general pattern), or one day type and band as a page preset
  names it ("all days, PM peak", "weekday, all day", "weekday, PM peak"; a specific
  pattern). A trip's day type and band are those of its first stop's scheduled departure.
- A slot is a pattern when it has at least PATTERN_MIN_TRIPS trips with a late departure,
  matched trips on at least PATTERN_MIN_DAYS service days, and a share of at least
  PATTERN_MIN_SHARE. A specific slot also needs a share PATTERN_SPECIFIC_MARGIN above the
  group's general share, and is left out when a broader specific slot that contains it
  (all days in its band, or its day type all day) is a pattern, unless it is another
  PATTERN_SPECIFIC_MARGIN above that one.
- A route has too little data for any pattern yet when none of its groups has
  PATTERN_MIN_TRIPS trips with a late departure (or it has none at all).
- Ranking within a route: general patterns first, then specific; within each, by matched
  late departures, then share.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import NamedTuple

from wbot_site.contract import PRESETS, SCHEMA, pattern_counts, preset_name

from .config import (
    CARRY_OVER_MARGIN_S,
    HEADLINE,
    MAX_BLOCK_LAYOVER_S,
    PATTERN_MIN_DAYS,
    PATTERN_MIN_SHARE,
    PATTERN_MIN_TRIPS,
    PATTERN_SPECIFIC_MARGIN,
    PATTERNS_RULES_VERSION,
)

GENERAL = ("all", "all")
# Slots in a fixed order, for ties.
SLOT_ORDER = {s: i for i, s in enumerate([GENERAL, *(tuple(name.split("-")) for name in PRESETS)])}


def thresholds() -> dict:
    """The thresholds in use, as patterns.json publishes them (the site prints them)."""
    return {"carry_over": {
        "late_after_s": HEADLINE[1],
        "min_trips": PATTERN_MIN_TRIPS,
        "min_days": PATTERN_MIN_DAYS,
        "min_share": round(float(PATTERN_MIN_SHARE), 4),
        "specific_margin": round(float(PATTERN_SPECIFIC_MARGIN), 4),
        "long_layover_s": MAX_BLOCK_LAYOVER_S,
        "long_layover_margin_s": CARRY_OVER_MARGIN_S,
    }}


class Cell(NamedTuple):
    """The trips of one service date, route, direction, first stop, day type and band that had a
    late departure and count in the measure (Stage.carry_over_cells)."""
    date: str
    route: str
    direction_id: int
    stop: str
    daytype: str
    band: str
    late: int      # their late departures
    matched: int   # those on trips that left the first stop late right after a carry-over
    trips: int


@dataclass
class Pattern:
    kind: str
    route: str
    direction_id: int
    stops: list[str]
    daytype: str
    band: str
    trips: int
    days: int
    late: int
    matched: int
    rank: int = 0

    @property
    def share(self) -> Fraction:
        return Fraction(self.matched, self.late)

    @property
    def preset(self) -> dict | None:
        return None if (self.daytype, self.band) == GENERAL else {"daytype": self.daytype, "band": self.band}

    @property
    def id(self) -> str:
        """Stable across runs: the same condition in the same place and slot keeps its id."""
        return ".".join([self.kind, self.route, str(self.direction_id), *self.stops,
                         preset_name(self.daytype, self.band)])

    def doc(self) -> dict:
        return {"id": self.id, "kind": self.kind, "route": self.route, "direction_id": self.direction_id,
                "stops": self.stops, "preset": self.preset, "rank": self.rank, "trips": self.trips,
                "days": self.days, "late": self.late, "matched": self.matched,
                "share": round(self.matched / self.late, 4)}


@dataclass
class _Slot:
    late: int = 0
    matched: int = 0
    trips: int = 0
    days: set = field(default_factory=set)

    @property
    def share(self) -> Fraction:
        return Fraction(self.matched, self.late) if self.late else Fraction(0)

    @property
    def enough(self) -> bool:
        return self.trips >= PATTERN_MIN_TRIPS

    @property
    def qualifies(self) -> bool:
        return self.enough and len(self.days) >= PATTERN_MIN_DAYS and self.share >= PATTERN_MIN_SHARE


def carry_over(cells: list[Cell], routes: list[str]) -> tuple[list[Pattern], list[str]]:
    """Carry-over patterns from the measure's cells, by the rules above: (patterns in `routes`
    order then rank, routes with too little data for any pattern yet, in `routes` order)."""
    groups: dict[tuple, dict[tuple, _Slot]] = {}
    for c in cells:
        slots = groups.setdefault((c.route, c.direction_id, c.stop), {})
        for dt in ("all", c.daytype):
            for b in ("all", c.band):
                s = slots.setdefault((dt, b), _Slot())
                s.late += c.late
                s.matched += c.matched
                s.trips += c.trips
                if c.matched:
                    s.days.add(c.date)
    found, enough = {}, set()
    for (route, direction, stop), slots in groups.items():
        general = slots[GENERAL]
        if general.enough:
            enough.add(route)
        keep = {GENERAL: general} if general.qualifies else {}
        specific = {k: s for k, s in slots.items()
                    if k != GENERAL and s.qualifies and s.share >= general.share + PATTERN_SPECIFIC_MARGIN}
        for (dt, b), s in specific.items():
            if dt != "all" and b != "all" and any(
                    w is not None and s.share < w.share + PATTERN_SPECIFIC_MARGIN
                    for w in (specific.get(("all", b)), specific.get((dt, "all")))):
                continue
            keep[(dt, b)] = s
        for (dt, b), s in keep.items():
            found.setdefault(route, []).append(Pattern("carry_over", route, direction, [stop], dt, b, s.trips,
                                                       len(s.days), s.late, s.matched))
    out = []
    for route in [*routes, *sorted(set(found) - set(routes))]:
        ranked = sorted(found.get(route, []), key=lambda p: (
            p.preset is not None, -p.matched, -p.share, p.direction_id, p.stops, SLOT_ORDER[(p.daytype, p.band)]))
        for i, p in enumerate(ranked, start=1):
            p.rank = i
        out += ranked
    return out, [r for r in routes if r not in enough]


def document(patterns: list[Pattern], too_little_data: list[str], period) -> dict:
    """patterns.json (layout in wbot_site.contract)."""
    docs = [p.doc() for p in patterns]
    by_route, by_stop = {}, {}
    for d in docs:
        by_route.setdefault(d["route"], []).append(d)
        for code in d["stops"]:
            by_stop.setdefault(code, []).append(d)
    return {
        "schema": SCHEMA,
        "rules_version": PATTERNS_RULES_VERSION,
        "period": {"key": "all", "start": period.start.isoformat(), "end": period.end.isoformat()},
        "thresholds": thresholds(),
        "patterns": docs,
        "routes": {slug: pattern_counts(ps) for slug, ps in by_route.items()},
        "stops": {code: pattern_counts(ps) for code, ps in sorted(by_stop.items())},
        "too_little_data": too_little_data,
    }


def csv_rows(patterns: list[Pattern]) -> list[list]:
    """patterns.csv rows (contract.PATTERNS_COLUMNS): general patterns have day type and band "all"."""
    return [[p.id, p.kind, p.route, p.direction_id, " ".join(p.stops), p.daytype, p.band, p.rank, p.trips, p.days,
             p.late, p.matched, round(p.matched / p.late, 4)] for p in patterns]

