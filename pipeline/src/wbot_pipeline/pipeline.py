"""The nightly run: process the service dates that need it, write the site files, publish."""

import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import __version__, events, publish, sitefiles, static, timeutil
from .config import Settings

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[3]


def pipeline_version() -> str:
    """The code's git commit (with +dirty for uncommitted pipeline changes), or the package version."""
    try:
        commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "pipeline"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return commit + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return __version__


@dataclass
class Result:
    processed: dict[str, str] = field(default_factory=dict)   # date -> status
    wrote_site: bool = False
    published: str = "disabled"
    seconds: float = 0.0

    def summary(self) -> str:
        done = sum(1 for s in self.processed.values() if s == "ok")
        waiting = [d for d, s in self.processed.items() if s == "not_ready"]
        parts = [f"{done} service dates processed"]
        if waiting:
            parts.append(f"{len(waiting)} not ready yet")
        parts.append("site files written" if self.wrote_site else "no site files")
        parts.append(f"publish: {self.published}")
        parts.append(f"{self.seconds:.0f} s")
        return ", ".join(parts)


def candidate_dates(settings: Settings, now: int) -> list[date]:
    last = timeutil.today_local(now) - timedelta(days=1)
    n = (last - settings.first_service_date).days + 1
    return [settings.first_service_date + timedelta(days=i) for i in range(max(0, n))]


def plan(settings: Settings, versions: list[static.Version], now: int, rebuild: bool = False) -> list[tuple[date, str]]:
    """(date, reason) for every date to process: outdated or new ones, plus the trailing window."""
    dates = candidate_dates(settings, now)
    trailing = set(dates[-settings.trailing_days:]) if settings.trailing_days else set()
    todo = []
    for d in dates:
        reason = "rebuild" if rebuild else events.stale_reason(settings, versions, d)
        if reason is None and d in trailing:
            reason = "trailing window"
        if reason:
            todo.append((d, reason))
    return todo


def run(settings: Settings, *, now: int | None = None, rebuild: bool = False, dates: list[date] | None = None,
        do_publish: bool = True, generated_at: str | None = None) -> Result:
    started = time.monotonic()
    now = now or int(time.time())
    version = pipeline_version()
    versions = static.list_versions(settings.static_dir)
    result = Result()
    todo = [(d, "requested") for d in dates] if dates is not None else plan(settings, versions, now, rebuild)
    for d, reason in todo:
        log.info("%s: processing (%s)", d, reason)
        result.processed[d.isoformat()] = events.process_date(settings, versions, d, now, version)
    stamp = generated_at or datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    result.wrote_site = sitefiles.build(settings, settings.out_dir, pipeline_version=version, generated_at=stamp)
    if result.wrote_site and do_publish:
        through = sitefiles.find_period(settings).end
        result.published = publish.publish(settings, settings.out_dir,
                                           message=f"Stats through {through.isoformat()} (pipeline {version})")
    result.seconds = time.monotonic() - started
    return result
