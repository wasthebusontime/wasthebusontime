"""Copy archives, static GTFS versions, and fetch logs offsite with rclone."""

import logging
import subprocess

from . import health
from .config import Settings

log = logging.getLogger(__name__)

# The spool is excluded on purpose: only verified archives go offsite. "derived" is
# the stats pipeline's output (stop events, facts, aggregates), when it exists.
BACKUP_DIRS = ("archive", "static", "log", "derived")


def rclone_commands(settings: Settings) -> list[list[str]]:
    # 'copy', never 'sync': deleting something locally must not delete the backup.
    return [
        [
            "rclone",
            "copy",
            str(settings.data_dir / d),
            f"{settings.b2_remote}:{settings.b2_bucket}/{d}",
            "--exclude",
            ".tmp-*",
            # The pipeline's scratch space, its site files (published, and rebuilt each
            # night from what is backed up) and half-written files.
            "--exclude",
            "tmp/**",
            "--exclude",
            "out*/**",
            "--exclude",
            "*.tmp",
            "--exclude",
            "*.tmp/**",
        ]
        for d in BACKUP_DIRS
        if (settings.data_dir / d).exists()
    ]


def run(settings: Settings) -> bool:
    if not settings.b2_bucket:
        log.error("WBOT_B2_BUCKET is not set")
        health.ping(settings, "backup", fail=True, message="WBOT_B2_BUCKET is not set")
        return False
    for cmd in rclone_commands(settings):
        log.info("running %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("rclone failed (%d): %s", result.returncode, result.stderr)
            health.ping(settings, "backup", fail=True, message=result.stderr[-5000:])
            return False
    health.ping(settings, "backup")
    return True
