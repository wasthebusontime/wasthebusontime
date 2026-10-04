"""healthchecks.io pings and the disk space check."""

import logging
import shutil

import httpx

from .config import USER_AGENT, Settings

log = logging.getLogger(__name__)

PING_TIMEOUT_S = 5.0


def ping(settings: Settings, check: str, *, fail: bool = False, message: str = "") -> None:
    """Ping a healthchecks.io check. Never raises: monitoring must not stop collection."""
    url = settings.hc_urls.get(check)
    if not url:
        return
    if fail:
        url = url.rstrip("/") + "/fail"
    try:
        httpx.post(
            url,
            content=message.encode()[:10_000],
            headers={"User-Agent": USER_AGENT},
            timeout=PING_TIMEOUT_S,
        )
    except httpx.HTTPError as e:
        log.warning("healthcheck ping %s failed: %s", check, e)


def check_disk(settings: Settings) -> bool:
    """Ping the disk check; fail it when free space drops below the threshold."""
    usage = shutil.disk_usage(settings.data_dir)
    free_pct = usage.free / usage.total * 100
    message = f"{free_pct:.1f}% free ({usage.free / 1e9:.1f} GB) on {settings.data_dir}"
    ok = free_pct >= settings.disk_min_free_pct
    (log.info if ok else log.error)("disk: %s", message)
    ping(settings, "disk", fail=not ok, message=message)
    return ok
