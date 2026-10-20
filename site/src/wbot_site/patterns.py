"""Patterns: the sentences the site writes from the numbers in patterns.json (contract.py).

A pattern is an interpretation, not a measurement (docs/adr/0002-patterns-by-documented-rules.md),
so every sentence comes from a fixed template per kind, with hedged words only. Templates
never say a pattern caused anything, never name people or decisions, and never suggest a
change: banned_words() is the check, and the tests run it on every template's output.
"""

import logging
import re
from dataclasses import dataclass
from fractions import Fraction

from markupsafe import Markup, escape

from wbot_site import filters
from wbot_site.data import directions_html, number, percent

log = logging.getLogger("wbot_site")

# Words a pattern sentence never uses: causes, blame (people or decisions), the schedule,
# and fixes. Stems match inside words ("caus" also catches "because").
BANNED = ("caus", "reason", r"due to", "blame", "fault", "driver", "operator", "dispatch", "schedul",
          "should", r"\bfix", "improv", r"\bwhy\b", "wrong", r"\bmust\b", r"need to")

# A share in words: the nearest of these fractions, "nearly all" from NEARLY_ALL, "all" at 1.
SHARE_FRACTIONS = (Fraction(1, 3), Fraction(2, 5), Fraction(1, 2), Fraction(3, 5), Fraction(2, 3),
                   Fraction(3, 4), Fraction(4, 5), Fraction(9, 10))
NEARLY_ALL = 0.95

# Bands inside a slot: after a day type ("weekday PM peaks"), and on their own ("on PM peaks").
BAND_PLURAL = {"early": "early mornings", "am_peak": "AM peaks", "midday": "middays", "pm_peak": "PM peaks",
               "evening": "evenings"}
BAND_ALONE = {"early": "early in the morning", "am_peak": "on AM peaks", "midday": "at midday",
              "pm_peak": "on PM peaks", "evening": "in the evening"}
DAYTYPE_ADJ = {"weekday": "weekday", "saturday": "Saturday", "sunday": "Sunday"}


def banned_words(text: str) -> list[str]:
    """The banned patterns found in a sentence ([] when it is fine)."""
    return [w for w in BANNED if re.search(w, text, re.I)]


def share_words(share: float) -> str:
    """0.3513 -> "about 1 in 3", 0.5 -> "about half", 0.97 -> "nearly all"."""
    if share >= 1:
        return "all"
    if share >= NEARLY_ALL:
        return "nearly all"
    f = min(SHARE_FRACTIONS, key=lambda f: abs(float(f) - share))
    return "about half" if f == Fraction(1, 2) else f"about {f.numerator} in {f.denominator}"


def slot_words(preset: dict | None) -> str:
    """The slot of a specific pattern inside a sentence: "on weekday PM peaks (3 to 6 pm)";
    "" for a general pattern."""
    if preset is None:
        return ""
    dt, band = preset["daytype"], preset["band"]
    if band == "all":
        return f"on {filters.DAYTYPE_WORDS[dt]}"
    hours = f" ({filters.band_hours(band)})"
    if dt == "all":
        return BAND_ALONE[band] + hours
    return f"on {DAYTYPE_ADJ[dt]} {BAND_PLURAL[band]}{hours}"


@dataclass
class Names:
    """What a sentence calls things: routes ("Route 21"), each route direction's headsign,
    and stop names as the stats have them (with direction codes; the page reads them out)."""
    routes: dict[str, str]
    headsigns: dict[tuple[str, int], str]
    stops: dict[str, str]

    @classmethod
    def from_stats(cls, stats) -> "Names":
        headsigns = {}
        for slug, route in stats.routes.items():
            for d in route["scopes"]["timepoints"]["by_direction"]:
                headsigns[(slug, d["direction_id"])] = d["headsign"]
        return cls(
            routes={r["slug"]: f"Route {r['short_name']}" for r in stats.system["routes"]},
            headsigns=headsigns,
            stops={code: s["stop"]["name"] for code, s in stats.stops.items()},
        )


def where(p: dict, names: Names) -> str:
    """"Route 21 to Downtown", and the slot of a specific pattern."""
    headsign = names.headsigns.get((p["route"], p["direction_id"]))
    route = names.routes[p["route"]] + (f" to {headsign}" if headsign else "")
    slot = slot_words(p["preset"])
    return f"{route} {slot}" if slot else route


def share_of(share: float, noun: str) -> str:
    """"about 1 in 3 late departures", "about half of the late departures"."""
    words = share_words(share)
    return f"{words} of the {noun}" if words == "about half" else f"{words} {noun}"


def carry_over(p: dict, names: Names) -> str:
    return (f"On {where(p, names)}, {share_of(p['share'], 'late departures')} were on trips that left "
            f"{names.stops[p['stops'][0]]} late right after a carry-over.")


# One sentence template per kind. A kind without one (added to the pipeline first) isn't shown.
TEMPLATES = {"carry_over": carry_over}


def sentence(p: dict, names: Names) -> str:
    return TEMPLATES[p["kind"]](p, names)


def sentence_html(p: dict, names: Names) -> Markup:
    """The sentence for a page: escaped, each stop name a link to its stop page (so it reads
    as a name, not part of the sentence), direction codes read out."""
    marks = {f"~~stop{i}~~": code for i, code in enumerate(p["stops"])}
    marked = Names(names.routes, names.headsigns, {**names.stops, **{code: m for m, code in marks.items()}})
    html = str(escape(sentence(p, marked)))
    for mark, code in marks.items():
        html = html.replace(mark, f'<a href="/stops/{escape(code)}/">{directions_html(names.stops[code])}</a>')
    return Markup(html)


def detail(p: dict) -> str:
    """The counts behind a pattern, under its sentence."""
    return (f"{number(p['matched'])} of {number(p['late'])} late departures ({percent(p['matched'], p['late'])}), "
            f"on {number(p['trips'])} trips with a late departure; seen on {number(p['days'])} different days.")


def shown(doc: dict) -> list[dict]:
    """The patterns the site can write a sentence for, in file order; others are skipped with a warning."""
    known = []
    for p in doc["patterns"]:
        if p["kind"] in TEMPLATES:
            known.append(p)
        else:
            log.warning("%s: no sentence for pattern kind %r yet; skipping %s", "patterns.json", p["kind"], p["id"])
    return known


def ratio_words(share: float) -> str:
    """A threshold share as a fraction: 0.3333 -> "1 in 3"."""
    f = Fraction(share).limit_denominator(10)
    return f"{f.numerator} in {f.denominator}"


def minutes_words(seconds: int) -> str:
    """300 -> "5 minutes", 60 -> "1 minute"."""
    m = seconds // 60
    return f"{m} minute{'s' if m != 1 else ''}" if seconds % 60 == 0 else f"{seconds} seconds"
