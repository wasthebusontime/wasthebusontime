"""Archive every version of the static GTFS zip, unmodified."""

import csv
import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime

import httpx

from . import health
from .config import STATIC_URL, USER_AGENT, Settings
from .storage import append_log, iso, sha256_hex, utc_now, write_atomic

log = logging.getLogger(__name__)

# The static zip is ~4 MB, so allow longer than the real-time feeds get.
STATIC_TIMEOUT_S = 60.0


def read_feed_info(content: bytes) -> dict[str, str]:
    """feed_info.txt's first row, or {} if the zip has none."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        if "feed_info.txt" not in z.namelist():
            return {}
        text = z.read("feed_info.txt").decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    return {k.strip(): (v or "").strip() for k, v in rows[0].items()} if rows else {}


def _known_hashes(settings: Settings) -> set[str]:
    hashes = set()
    for sidecar in settings.static_dir.glob("*.json"):
        hashes.add(json.loads(sidecar.read_text(encoding="utf-8"))["sha256"])
    return hashes


def check(settings: Settings, client: httpx.Client, url: str = STATIC_URL, now: datetime | None = None) -> str:
    """Fetch the static feed if it changed. Returns 'unchanged', 'duplicate', or 'saved'."""
    now = now or utc_now()
    state_path = settings.state_dir / "static.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]

    t0 = time.monotonic()
    resp = client.get(url, headers=headers)
    record = {
        "fetched_at": iso(now),
        "feed": "static",
        "url": url,
        "final_url": str(resp.url),
        "status": resp.status_code,
        "bytes": len(resp.content),
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
        "sha256": None,
        "path": None,
        "error": None,
    }

    try:
        if resp.status_code == 304:
            return "unchanged"
        if resp.status_code != 200:
            record["error"] = f"HTTP {resp.status_code}"
            raise RuntimeError(record["error"])
        content = resp.content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            record["error"] = "response is not a zip file"
            raise RuntimeError(record["error"])

        digest = sha256_hex(content)
        record["sha256"] = digest
        result = "duplicate"
        if digest not in _known_hashes(settings):
            info = read_feed_info(content)
            version = re.sub(r"[^A-Za-z0-9._-]", "_", info.get("feed_version", "")) or "noversion"
            path = settings.static_dir / f"{version}_{digest[:8]}.zip"
            write_atomic(path, content)
            sidecar = {
                "fetched_at": iso(now),
                "url": url,
                "final_url": str(resp.url),
                "sha256": digest,
                "bytes": len(content),
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
                "feed_info": info,
            }
            write_atomic(path.with_suffix(".json"), json.dumps(sidecar, indent=2).encode())
            record["path"] = path.relative_to(settings.data_dir).as_posix()
            log.info("saved new static GTFS version %s", path.name)
            result = "saved"

        state = {"etag": resp.headers.get("ETag"), "last_modified": resp.headers.get("Last-Modified")}
        write_atomic(state_path, json.dumps(state).encode())
        return result
    finally:
        append_log(settings.log_dir, record, now)


def run(settings: Settings) -> bool:
    try:
        with httpx.Client(
            timeout=STATIC_TIMEOUT_S,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            result = check(settings, client)
    except Exception as e:
        log.exception("static check failed")
        health.ping(settings, "static", fail=True, message=f"{type(e).__name__}: {e}")
        return False
    log.info("static GTFS: %s", result)
    health.ping(settings, "static", message=result)
    return True
