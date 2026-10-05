"""Command line: wbot-pipeline {run,rebuild,day,site,publish,status}."""

import argparse
import json
import logging
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

from . import events, health, pipeline, publish, sitefiles, static
from .config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wbot-pipeline", description=__doc__)
    parser.add_argument("--data", help="collector data directory (default: WBOT_DATA_DIR)")
    parser.add_argument("--derived", help="where derived data goes (default: WBOT_DERIVED_DIR or <data>/derived)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="nightly run: process new and changed service dates, write site files, publish")
    sub.add_parser("rebuild", help="reprocess every service date from the archive, then write and publish")
    p_day = sub.add_parser("day", help="process the given service dates only (YYYY-MM-DD ...)")
    p_day.add_argument("dates", nargs="+", type=date.fromisoformat)
    sub.add_parser("site", help="rewrite the site files from the stop events already derived")
    sub.add_parser("publish", help="publish the last written site files")
    sub.add_parser("status", help="list processed service dates")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    overrides = {}
    if args.data:
        overrides["data_dir"] = Path(args.data)
        if not args.derived:
            overrides["derived_dir"] = Path(args.data) / "derived"
    if args.derived:
        overrides["derived_dir"] = Path(args.derived)
    settings = load_settings(**overrides)

    if args.command == "status":
        versions = static.list_versions(settings.static_dir)
        for d in pipeline.candidate_dates(settings, int(time.time())):
            m = events.read_manifest(settings, d) or {}
            reason = events.stale_reason(settings, versions, d)
            counts = m.get("counts", {})
            print(f"{d}  {m.get('status', '-'):10s} events {counts.get('events', '-'):>7} "
                  f"observed {counts.get('observed', '-'):>7}  {'needs: ' + reason if reason else 'current'}")
        return 0

    if args.command in ("run", "rebuild", "day"):
        health.ping(settings.hc_url, "start")
        try:
            result = pipeline.run(settings, rebuild=args.command == "rebuild",
                                  dates=args.dates if args.command == "day" else None)
        except Exception as e:
            logging.exception("pipeline run failed")
            health.ping(settings.hc_url, "fail", f"{type(e).__name__}: {e}")
            return 1
        logging.info("%s", result.summary())
        health.ping(settings.hc_url, "", result.summary())
        return 0

    if args.command == "site":
        ok = sitefiles.build(settings, settings.out_dir, pipeline_version=pipeline.pipeline_version(),
                             generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return 0 if ok else 1

    if args.command == "publish":
        meta = json.loads((settings.out_dir / "site" / "meta.json").read_text(encoding="utf-8"))
        try:
            print(publish.publish(settings, settings.out_dir,
                                  message=f"Stats through {meta['data_through']} (pipeline {meta['pipeline_version']})"))
        except publish.PublishError as e:
            logging.error("%s", e)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
