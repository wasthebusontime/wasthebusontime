"""Settings read from the environment, and the constants the site depends on.

Every threshold the methodology calls provisional is a setting, so the
re-validation can change it without a code change. The on-time windows and the
histogram range are constants: the site's labels describe exactly these.
"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Bump when the way stop events are derived from the archive changes. Every
# service date whose events were made by another version is reprocessed.
EVENTS_VERSION = 1

SCHEMA = 1
HEADLINE = (0, 300)
ALT = (-60, 300)
HIST_START_MIN = -10
HIST_BUCKETS = 30
SCOPES = ("timepoints", "all_stops")
DAYTYPES = ("weekday", "saturday", "sunday")
# Stop map time-of-day bands by service hour: (key, first hour, last hour + 1).
BANDS = (("early", 0, 6), ("am_peak", 6, 9), ("midday", 9, 15), ("pm_peak", 15, 18), ("evening", 18, 48))

# Passed stops stay in TripUpdates about 15 minutes; a stop is read when it is final.
RETENTION_S = 900
# The collector polls TripUpdates every 30 s.
POLL_S = 30
# A TripUpdates fetch counts as stale (a feed-side problem) when its newest
# trip update is older than this.
STALE_AFTER_S = 600

# Commits to the stats repo use the project account's GitHub noreply address.
COMMIT_NAME = "Was the Bus On Time"
COMMIT_EMAIL = "337534321+wasthebusontime@users.noreply.github.com"


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Collector data (archive/, static/, log/, spool/). Only ever read.
    data_dir: Path
    # Everything the pipeline writes: static cache, stop events, facts, output.
    derived_dir: Path
    # Local clone of the public stats repo (only used to publish).
    stats_repo: Path | None = None
    first_service_date: date = date(2026, 10, 4)
    trailing_days: int = 3
    # Share of a trip's eligible timepoints that must be observed for the trip
    # to count as observed in trip-level numbers.
    trip_coverage_min: float = 0.5
    # A percentage needs at least this many departures behind it (meta.min_sample).
    min_sample: int = 30
    # Gaps in collection up to this long lose no departures (stops stay ~15 min).
    gap_tolerance_s: int = 840
    # A day whose observed share of timepoint departures is below this gets a notice.
    low_completeness_below: float = 0.8
    provisional: bool = True
    methodology_version: str = "1"
    # Earlier methodology changes to announce, as (date, version) pairs.
    methodology_changes: tuple[tuple[str, str], ...] = ()
    daily_days: int = 90
    publish: bool = False
    hc_url: str = ""
    # Private key for pushing to the stats repo (a deploy key scoped to that repo).
    ssh_key: str = ""
    duckdb_memory: str = "256MB"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def static_dir(self) -> Path:
        return self.data_dir / "static"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "log"

    @property
    def spool_dir(self) -> Path:
        return self.data_dir / "spool"

    @property
    def out_dir(self) -> Path:
        return self.derived_dir / "out"


def load_settings(env: dict[str, str] | None = None, **overrides) -> Settings:
    env = os.environ if env is None else env
    p = "WBOT_PIPELINE_"
    data_dir = Path(env.get("WBOT_DATA_DIR", "data"))
    values = dict(
        data_dir=data_dir,
        derived_dir=Path(env.get("WBOT_DERIVED_DIR") or data_dir / "derived"),
        stats_repo=Path(env["WBOT_STATS_REPO"]) if env.get("WBOT_STATS_REPO") else None,
        hc_url=env.get("WBOT_HC_PIPELINE", "").strip(),
        ssh_key=env.get("WBOT_STATS_SSH_KEY", "").strip(),
    )
    if v := env.get(p + "FIRST_SERVICE_DATE"):
        values["first_service_date"] = date.fromisoformat(v)
    for name, cast in (("trailing_days", int), ("trip_coverage_min", float), ("min_sample", int),
                       ("gap_tolerance_s", int), ("low_completeness_below", float), ("daily_days", int),
                       ("methodology_version", str), ("duckdb_memory", str)):
        if v := env.get(p + name.upper()):
            values[name] = cast(v)
    for name in ("provisional", "publish"):
        if v := env.get(p + name.upper()):
            values[name] = _bool(v)
    if v := env.get(p + "METHODOLOGY_CHANGES"):
        # "2026-11-01:2,2027-01-15:3"
        values["methodology_changes"] = tuple(tuple(x.strip().split(":", 1)) for x in v.split(",") if x.strip())
    values.update(overrides)
    return Settings(**values)
