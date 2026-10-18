"""The agency this site measures: what the stats don't carry.

To measure another agency, change these and the pages in content/ (see "Measuring a
different agency" in the README).
"""

from zoneinfo import ZoneInfo

NAME = "Intercity Transit"
# The agency's published goal: this percent of terminal departures on time, on its own
# window (the stats' alt window).
TERMINAL_GOAL = 90
# Times on the site are the agency's local time.
TZ = ZoneInfo("America/Los_Angeles")
TZ_NAME = "Pacific time"
