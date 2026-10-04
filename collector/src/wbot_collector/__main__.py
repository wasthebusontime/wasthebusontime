"""Command line: wbot-collector {run,pack,static,check,backup}."""

import argparse
import logging
import sys

from . import backup, fetch, health, pack, static_gtfs
from .config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wbot-collector", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", help="poll the real-time feeds until stopped")
    p_run.add_argument("--cycles", type=int, default=None, help="stop after this many rounds (for testing)")
    sub.add_parser("pack", help="pack completed spool hours into verified .tar.zst archives")
    sub.add_parser("static", help="archive the static GTFS zip if it changed")
    sub.add_parser("check", help="check free disk space")
    sub.add_parser("backup", help="copy archives, static versions, and logs to B2 with rclone")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "run":
        fetch.run(settings, max_cycles=args.cycles)
        return 0
    ok = {
        "pack": pack.run,
        "static": static_gtfs.run,
        "check": health.check_disk,
        "backup": backup.run,
    }[args.command](settings)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
