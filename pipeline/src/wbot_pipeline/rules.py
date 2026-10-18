"""Methodology rules for stop events and trips, each defined once.

DuckDB macros can't refer to the calling query's columns, so the rules over event and
trip columns are SQL expressions (parenthesized, safe to combine with AND). The helper
macros that take their inputs as arguments are registered on a connection with register().
"""

import duckdb
from wbot_site.contract import BANDS, SUFFIX

from .config import ALT, HEADLINE

MACROS = [
    "CREATE OR REPLACE MACRO gtfs_s(x) AS CASE WHEN x IS NULL THEN NULL ELSE "
    "CAST(split_part(x, ':', 1) AS INTEGER) * 3600 + CAST(split_part(x, ':', 2) AS INTEGER) * 60 "
    "+ CAST(split_part(x, ':', 3) AS INTEGER) END",
    # Route slug: short name lowercased and URL-safe. Stop slug: the public code, URL-safe.
    "CREATE OR REPLACE MACRO route_slug(x) AS "
    "coalesce(nullif(trim(regexp_replace(lower(x), '[^a-z0-9]+', '-', 'g'), '-'), ''), 'route')",
    "CREATE OR REPLACE MACRO stop_slug(x) AS regexp_replace(x, '[^A-Za-z0-9_-]', '-', 'g')",
]


def register(con: duckdb.DuckDBPyConnection) -> None:
    for macro in MACROS:
        con.execute(macro)


# Stop events.

# A timed stop: a timepoint that isn't the trip's last stop, so a departure from it is timed.
TIMED_STOP = "(is_timepoint AND NOT is_last)"

# A departure that counts in on-time numbers: observed, scheduled, neither the stop nor the
# trip skipped, cancelled or without data, not the last stop, and with a departure delay.
DEPARTURE = """(observed AND scheduled AND stop_sr = 'SCHEDULED' AND trip_sr = 'SCHEDULED'
               AND NOT is_last AND delay_dep_s IS NOT NULL)"""

# An end-of-line arrival that counts: the same, at the trip's last stop, on the arrival.
END_OF_LINE = """(is_last AND scheduled AND observed_arr AND stop_sr = 'SCHEDULED'
                 AND trip_sr = 'SCHEDULED' AND delay_arr_s IS NOT NULL)"""


def band(hour: str) -> str:
    """The band of a service hour column, from the contract's BANDS; the last band takes every later hour."""
    *bands, (last, _, _) = BANDS
    whens = " ".join(f"WHEN {hour} < {hi} THEN '{key}'" for key, _, hi in bands)
    return f"(CASE {whens} ELSE '{last}' END)"


# On-time windows, by the suffix of their count names: "" is the headline window, "_alt" the agency's.
WINDOWS = {SUFFIX["headline"]: HEADLINE, SUFFIX["alt"]: ALT}


def window_counts(delay: str) -> dict[str, str]:
    """Where a delay falls in each on-time window, by count name: early, on_time, late,
    then early_alt, on_time_alt, late_alt. Windows are inclusive at both ends."""
    out = {}
    for suffix, (lo, hi) in WINDOWS.items():
        out["early" + suffix] = f"({delay} < {lo})"
        out["on_time" + suffix] = f"({delay} BETWEEN {lo} AND {hi})"
        out["late" + suffix] = f"({delay} > {hi})"
    return out


def before_schedule(delay: str) -> str:
    """Any time before the schedule: how "early" is meant for end-of-line arrivals."""
    return f"({delay} < 0)"


# Trips.

# A trip that counts toward completeness: scheduled and not cancelled.
TRIP_COUNTED = "(scheduled AND coalesce(trip_sr, '') <> 'CANCELED')"


def trip_observed(min_share: float) -> str:
    """A counted trip with at least min_share of its eligible timepoints observed."""
    return (f"({TRIP_COUNTED} AND eligible_timepoints > 0"
            f" AND observed_timepoints >= {min_share} * eligible_timepoints)")
