"""Feed definitions and settings read from the environment."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

USER_AGENT = (
    f"wasthebusontime-collector/{__version__} "
    "(+https://wasthebusontime.com; hello@wasthebusontime.com)"
)

REALTIME_BASE = "https://its.rideralerts.com/InfoPoint/GTFS-Realtime.ashx"
STATIC_URL = "https://intercitytransit.com/googledata/google_transit.zip"

HTTP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    interval_s: int
    # Seconds after each interval boundary to fetch, to spread requests out.
    offset_s: int = 0


# Never poll any feed more often than every 30 seconds.
FEEDS = (
    Feed("tripupdates", f"{REALTIME_BASE}?Type=TripUpdate", 30),
    Feed("vehiclepositions", f"{REALTIME_BASE}?Type=VehiclePosition", 30),
    Feed("alerts", f"{REALTIME_BASE}?Type=alert", 300, offset_s=15),
)

# The poll loop pings the heartbeat checks this often.
HEARTBEAT_INTERVAL_S = 300
# The feed counts as stale if its header timestamp hasn't advanced in this long.
FEED_STALE_AFTER_S = 600


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    disk_min_free_pct: float = 10.0
    # healthchecks.io ping URLs by check name; missing or blank means disabled.
    hc_urls: dict[str, str] = field(default_factory=dict)
    b2_remote: str = "b2"
    b2_bucket: str = ""

    @property
    def spool_dir(self) -> Path:
        return self.data_dir / "spool"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "log"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def static_dir(self) -> Path:
        return self.data_dir / "static"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"


HC_CHECKS = ("collector", "feed_stale", "pack", "backup", "static", "disk")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    hc_urls = {}
    for check in HC_CHECKS:
        url = env.get(f"WBOT_HC_{check.upper()}", "").strip()
        if url:
            hc_urls[check] = url
    return Settings(
        data_dir=Path(env.get("WBOT_DATA_DIR", "data")),
        disk_min_free_pct=float(env.get("WBOT_DISK_MIN_FREE_PCT", "10")),
        hc_urls=hc_urls,
        b2_remote=env.get("WBOT_B2_REMOTE", "b2"),
        b2_bucket=env.get("WBOT_B2_BUCKET", ""),
    )
