"""On-time windows as the site shows them.

The window numbers come from the stats (meta.windows, seconds late, inclusive), so a
window change in the pipeline needs no change here: the wording, the histogram's
on-time bracket and colors and the stop map's thresholds all follow from them. This
module also holds the display choices that aren't in the stats.
"""

from dataclasses import dataclass

# Intercity Transit's own window (the alt window) is computed but hidden on the site for now.
SHOW_ALT = False
# Intercity Transit's standard: this percent of terminal departures on time.
TERMINAL_STANDARD = 90
# Lower edges of the stop map's color bins, in percent on time, best first.
MAP_BINS = (90, 80, 70)


@dataclass(frozen=True)
class Window:
    lo: int  # seconds late (negative is early), inclusive
    hi: int

    @property
    def span(self) -> str:
        """In words: "1 min early to 5 min late", "0 to 5 min late"."""
        lo, hi = self.lo // 60, self.hi // 60
        if lo < 0 < hi:
            return f"{-lo} min early to {hi} min late"
        if lo >= 0:
            return f"{lo} to {hi} min late"
        return f"{-lo} to {-hi} min early"

    def kind(self, minute: int) -> str:
        """The color of the 1-minute histogram bucket from `minute` to `minute + 1`:
        on-time only if the whole bucket is inside the window."""
        start, end = minute * 60, minute * 60 + 59
        if self.lo <= start and end <= self.hi:
            return "on-time"
        return "early" if start < self.lo else "late"


@dataclass(frozen=True)
class Windows:
    headline: Window
    alt: Window
    show_alt: bool = False

    @property
    def headline_label(self) -> str:
        return f"On time ({self.headline.span})"

    @property
    def alt_label(self) -> str:
        return f"On time (Intercity Transit's own definition: {self.alt.span})"


def from_meta(windows: dict) -> Windows:
    """meta.windows -> Windows. ValueError unless both windows are there in whole minutes."""
    out = {}
    for key in ("headline", "alt"):
        pair = windows.get(key) if isinstance(windows, dict) else None
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(v, int) for v in pair)):
            raise ValueError(f"meta.windows.{key} is {pair!r}, expected [lo, hi] in seconds")
        lo, hi = pair
        if lo % 60 or hi % 60 or lo >= hi:
            raise ValueError(f"meta.windows.{key} is {pair}, expected whole minutes with lo < hi")
        out[key] = Window(lo, hi)
    return Windows(**out, show_alt=SHOW_ALT)


def map_legend() -> list[str]:
    """The stop map's bins in words, best first: "90% or more on time", "80 to 89%", ..."""
    out = [f"{MAP_BINS[0]}% or more on time"]
    out += [f"{lo} to {hi - 1}%" for hi, lo in zip(MAP_BINS, MAP_BINS[1:])]
    out.append(f"Under {MAP_BINS[-1]}%")
    return out
