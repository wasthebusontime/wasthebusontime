"""Day type and time of day filters on the home and route pages: URLs, labels and menus.

Each page preset (contract.PRESETS) is built as its own page. The unfiltered page keeps the
plain URL; a filtered page's URL always has both parts, /{daytype}/{band}/ under the page's
own path. The slugs here are the site's names for the contract's keys.
"""

from dataclasses import dataclass

from wbot_site import contract
from wbot_site.data import hour_text

DAYTYPE_SLUGS = {"all": "all-days", "weekday": "weekdays", "saturday": "saturday", "sunday": "sunday"}
BAND_SLUGS = {"all": "all-day", "early": "early", "am_peak": "am-peak", "midday": "midday",
              "pm_peak": "pm-peak", "evening": "evening"}
# Menu labels, and the words for titles and headlines.
DAYTYPE_MENU = {"all": "All days", "weekday": "Weekdays", "saturday": "Saturday", "sunday": "Sunday"}
DAYTYPE_WORDS = {"all": "all days", "weekday": "weekdays", "saturday": "Saturdays", "sunday": "Sundays"}
BAND_NAMES = {"early": "Early", "am_peak": "AM peak", "midday": "Midday", "pm_peak": "PM peak", "evening": "Evening"}
BAND_HOURS = {key: (first, end) for key, first, end in contract.BANDS}


def path(daytype: str, band: str) -> str:
    """The filter's part of a URL: "" for no filter, else "weekdays/am-peak/"."""
    if (daytype, band) == ("all", "all"):
        return ""
    return f"{DAYTYPE_SLUGS[daytype]}/{BAND_SLUGS[band]}/"


def band_hours(key: str) -> str:
    """"6 to 9 am", "before 6 am", "6 pm on", from the contract's band."""
    first, end = BAND_HOURS[key]
    if first == 0:
        return f"before {hour_text(end)}"
    if end >= 24:
        return f"{hour_text(first)} on"
    a, b = hour_text(first), hour_text(end)
    return f"{a.split()[0]} to {b}" if a.split()[1] == b.split()[1] else f"{a} to {b}"


def band_label(key: str) -> str:
    """"AM peak (6 to 9 am)"; "All day" for no band."""
    return "All day" if key == "all" else f"{BAND_NAMES[key]} ({band_hours(key)})"


def band_words(key: str) -> str:
    """"AM peak", "early", "all day": the band inside a sentence."""
    return "all day" if key == "all" else BAND_NAMES[key] if key.endswith("_peak") else BAND_NAMES[key].lower()


@dataclass
class Choice:
    label: str
    href: str | None  # None: no service in that preset, so no page
    current: bool
    short: str  # the closed menu's button: "Midday", so the settings bar fits on one row


@dataclass
class Filter:
    """The filter a page shows, and its two menus. "all", "all" is the unfiltered page."""
    daytype: str
    band: str
    daytypes: list[Choice]
    bands: list[Choice]

    @property
    def active(self) -> bool:
        return (self.daytype, self.band) != ("all", "all")

    @property
    def words(self) -> str:
        """"weekdays, AM peak", for titles after a name."""
        return f"{DAYTYPE_WORDS[self.daytype]}, {band_words(self.band)}"

    @property
    def title(self) -> str:
        """"Weekdays, AM peak", the home page's title."""
        return self.words[0].upper() + self.words[1:]

    @property
    def lead(self) -> str:
        """"Weekdays, AM peak (6 to 9 am)", which starts the headline sentence."""
        band = band_words(self.band) + ("" if self.band == "all" else f" ({band_hours(self.band)})")
        words = f"{DAYTYPE_WORDS[self.daytype]}, {band}"
        return words[0].upper() + words[1:]


def make(base: str, daytype: str, band: str, exists) -> Filter:
    """The filter for the page at `base` ("/" or "/routes/13/"); exists(daytype, band) says
    whether that preset has a page here. A menu choice keeps the other menu's choice."""
    def choice(dt: str, b: str, label: str, current: bool, short: str) -> Choice:
        return Choice(label, base + path(dt, b) if exists(dt, b) else None, current, short)

    return Filter(
        daytype, band,
        [choice(dt, band, DAYTYPE_MENU[dt], dt == daytype, DAYTYPE_MENU[dt]) for dt in contract.MAP_DAYTYPES],
        [choice(daytype, b, band_label(b), b == band, BAND_NAMES.get(b, "All day")) for b in contract.MAP_BANDS],
    )
