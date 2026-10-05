"""healthchecks.io pings for the nightly run. Never raises: monitoring must not fail a run."""

import logging
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"wasthebusontime-pipeline/{__version__} (+https://wasthebusontime.com)"
TIMEOUT_S = 10


def ping(url: str, kind: str = "", message: str = "") -> None:
    """kind is '' (success), 'start' or 'fail'."""
    if not url:
        return
    target = url.rstrip("/") + (f"/{kind}" if kind else "")
    req = urllib.request.Request(target, data=message.encode()[:10_000], headers={"User-Agent": USER_AGENT})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT_S).close()
    except OSError as e:
        log.warning("healthcheck ping failed: %s", e)
