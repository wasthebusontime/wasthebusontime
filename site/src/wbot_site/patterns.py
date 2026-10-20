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
from wbot_site.contract import pattern_filters, preset_name
from wbot_site.data import directions_html, percent

log = logging.getLogger("wbot_site")

# Words a pattern sentence never uses: causes, blame (people or decisions), the schedule,
# and fixes. Stems match inside words ("caus" also catches "because").
BANNED = ("caus", "reason", r"due to", "blame", "fault", "driver", "operator", "dispatch", "schedul",
          "should", r"\bfix", "improv", r"\bwhy\b", "wrong", r"\bmust\b", r"need to")

# Where the bus came from: "mostly from" when the largest previous route covers less than this
# share of a pattern's matched late departures.
MOSTLY = 0.9

# Bands inside a slot: after a day type ("weekday PM peaks"), and on their own ("on PM peaks").
BAND_PLURAL = {"early": "early mornings", "am_peak": "AM peaks", "midday": "middays", "pm_peak": "PM peaks",
               "evening": "evenings"}
BAND_ALONE = {"early": "early in the morning", "am_peak": "on AM peaks", "midday": "at midday",
              "pm_peak": "on PM peaks", "evening": "in the evening"}
DAYTYPE_ADJ = {"weekday": "weekday", "saturday": "Saturday", "sunday": "Sunday"}


def banned_words(text: str) -> list[str]:
    """The banned patterns found in a sentence ([] when it is fine)."""
    return [w for w in BANNED if re.search(w, text, re.I)]


def share_words(p: dict) -> str:
    """A pattern's share as a whole percentage, from its counts with the site's one rounding
    rule: 510 of 588 -> "87%"."""
    return percent(p["matched"], p["late"])


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



class Plain:
    """Names a template puts in a sentence, as plain text."""

    def __init__(self, names: Names):
        self.names = names

    def stop(self, code: str) -> str:
        return self.names.stops[code]

    def route(self, slug: str) -> str:
        return self.names.routes[slug]


class Linked(Plain):
    """The same names as links (stop and route pages), as markers that sentence_html swaps
    for the links once the rest of the sentence is escaped."""

    def __init__(self, names: Names, here: str | None = None):
        super().__init__(names)
        self.here = here  # the page's own path ("/stops/E111/"), never linked
        self.links: dict[str, str] = {}

    def _mark(self, href: str, html: str) -> str:
        mark = f"~~link{len(self.links)}~~"
        self.links[mark] = html if href == self.here else f'<a href="{escape(href)}">{html}</a>'
        return mark

    def stop(self, code: str) -> str:
        return self._mark(f"/stops/{code}/", directions_html(self.names.stops[code]))

    def route(self, slug: str) -> str:
        return self._mark(f"/routes/{slug}/", escape(self.names.routes[slug]))


def came_from(p: dict, link: Plain) -> str:
    """Where the bus came from on the matched trips, between "arrived" and "too late": " from
    Route 9X", " from Route 13, going the other way,", " (mostly from Route 9X)" when the
    largest previous route covers less than MOSTLY of them, "" when unknown."""
    previous = [x for x in p["previous"] if x["route"] in link.names.routes]
    if not previous:
        return ""
    top = previous[0]
    if top["route"] != p["route"]:
        source = link.route(top["route"])
    elif top["direction_id"] != p["direction_id"]:
        source = f"{link.route(top['route'])}, going the other way"
    else:
        source = f"an earlier {link.route(top['route'])} trip"
    if top["matched"] < MOSTLY * p["matched"]:
        return f" (mostly from {source})"
    return f" from {source}," if source.endswith("the other way") else f" from {source}"


def carry_over(p: dict, names: Names, link: Plain) -> str:
    return (f"On {where(p, names)}, {share_words(p)} of late departures were on trips that left "
            f"{link.stop(p['stops'][0])} late, after the bus arrived{came_from(p, link)} too late to leave on time.")


# One sentence template per kind. A kind without one (added to the pipeline first) isn't shown.
TEMPLATES = {"carry_over": carry_over}


def sentence(p: dict, names: Names) -> str:
    return TEMPLATES[p["kind"]](p, names, Plain(names))


def sentence_html(p: dict, names: Names, here: str | None = None) -> Markup:
    """The sentence for a page: escaped, stop names and the route the bus came from as links
    to their pages (so a stop reads as a name, not part of the sentence), direction codes read
    out. The pattern's own route isn't a link: the page around it is about that route. Nor is
    the page itself (`here`, its path), on a route or stop page."""
    link = Linked(names, here)
    html = str(escape(TEMPLATES[p["kind"]](p, names, link)))
    for mark, a in link.links.items():
        html = html.replace(mark, a)
    return Markup(html)


def shown(doc: dict) -> list[dict]:
    """The patterns the site can write a sentence for, in file order; others are skipped with a warning."""
    known = []
    for p in doc["patterns"]:
        if p["kind"] in TEMPLATES:
            known.append(p)
        else:
            log.warning("%s: no sentence for pattern kind %r yet; skipping %s", "patterns.json", p["kind"], p["id"])
    return known


# Callouts: the pattern panels on a route or stop page, and the line under them that links to
# the Patterns page (spec decision 8). Measured numbers come first, so a page shows few.
MAX_CALLOUTS = 2
PAGE = "/patterns/"


@dataclass
class Callouts:
    """What a route or stop page shows: the patterns chosen (best first), their panels, and the
    link line, which is there even when no pattern is."""
    patterns: list[dict]
    panels: list[dict]
    line: Markup


def general_and_strongest_specific(ps: list[dict]) -> list[dict]:
    """For a page without a filter: the general patterns, then the first specific one, in the
    order given, up to MAX_CALLOUTS."""
    specific = [p for p in ps if p["preset"] is not None][:1]
    return [p for p in ps if p["preset"] is None or p in specific][:MAX_CALLOUTS]


def counted(n: int, what: str) -> str:
    """counted(1, "more pattern") -> "1 more pattern"; 3 -> "3 more patterns"."""
    return f"{n} {what}{'s' if n != 1 else ''}"


def section_link(slug: str, words: str) -> str:
    return f'<a href="{PAGE}#route-{escape(slug)}">{escape(words)}</a>'


ABOUT = f'<a href="{PAGE}">About patterns</a>'
ALL_FIT = f'<a href="{PAGE}">About patterns, and patterns on other routes</a>'


def route_callouts(doc: dict, slug: str, daytype: str, band: str, names: Names) -> Callouts:
    """A route page's callouts under its filter (all, all: none). Unfiltered: the general
    patterns plus the strongest specific one; filtered: the general patterns plus those for a
    slot that covers the filter (contract.pattern_filters), by rank either way. The link line
    counts the patterns left out, for that filter on a filtered page."""
    ps = sorted((p for p in shown(doc) if p["route"] == slug), key=lambda p: p["rank"])
    route = names.routes[slug]
    if (daytype, band) == ("all", "all"):
        slot, candidates = "", ps
        chosen = general_and_strongest_specific(ps)
    else:
        slot = slot_words({"daytype": daytype, "band": band})
        candidates = [p for p in ps if preset_name(daytype, band) in pattern_filters(p["preset"])]
        chosen = candidates[:MAX_CALLOUTS]
    if not ps:
        lead = ("Not enough data for patterns on this route yet." if slug in doc["too_little_data"]
                else "No patterns found for this route yet.")
        line = f"{lead} {ABOUT}"
    elif not candidates:
        line = (f"No patterns found for this route {escape(slot)}. "
                f"{section_link(slug, counted(len(ps), 'pattern') + ' for ' + route)} on the Patterns page")
    elif len(candidates) > len(chosen):
        words = f"{counted(len(candidates) - len(chosen), 'more pattern')} for {route}" + (f" {slot}" if slot else "")
        line = f"{section_link(slug, words)} on the Patterns page"
    else:
        line = ALL_FIT
    return Callouts(chosen, panels(chosen, names, f"/routes/{slug}/"), Markup(line))


def stop_callouts(doc: dict, code: str, names: Names) -> Callouts | None:
    """A stop page's callouts: the patterns at the stop (for a carry-over, the trip's first
    stop) on every route, general ones first, then by late departures covered, chosen like an
    unfiltered route page's. The link line has one link per route with a pattern here, in the
    site's route order, counting the ones left out. None for a stop without a pattern: most
    stops are never a first stop, so the page says nothing (owner's choice, 2026-10-20)."""
    order = {slug: i for i, slug in enumerate(names.routes)}
    ps = sorted((p for p in shown(doc) if code in p["stops"] and p["route"] in order),
                key=lambda p: (p["preset"] is not None, -p["matched"], order[p["route"]], p["rank"]))
    if not ps:
        return None
    chosen = general_and_strongest_specific(ps)
    links = []
    for slug in sorted({p["route"] for p in ps}, key=order.get):
        more = sum(1 for p in ps if p["route"] == slug and p not in chosen)
        what = f"{counted(more, 'more pattern')} for" if more else "patterns for"
        links.append(section_link(slug, f"{what} {names.routes[slug]}"))
    joined = links[0] if len(links) == 1 else ", ".join(links[:-1]) + " and " + links[-1]
    line = f"On the Patterns page: {joined}"
    return Callouts(chosen, panels(chosen, names, f"/stops/{code}/"), Markup(line))


def panels(ps: list[dict], names: Names, here: str) -> list[dict]:
    """What pattern_panel takes, for each pattern on a page at `here`."""
    return [{"sentence": sentence_html(p, names, here)} for p in ps]


def ratio_words(share: float) -> str:
    """A threshold share as a fraction: 0.3333 -> "1 in 3"."""
    f = Fraction(share).limit_denominator(10)
    return f"{f.numerator} in {f.denominator}"


def minutes_words(seconds: int) -> str:
    """300 -> "5 minutes", 60 -> "1 minute"."""
    m = seconds // 60
    return f"{m} minute{'s' if m != 1 else ''}" if seconds % 60 == 0 else f"{seconds} seconds"
