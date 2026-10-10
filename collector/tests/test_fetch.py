import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from conftest import make_feed
from wbot_collector.config import FEEDS, Feed, Settings, load_settings, wsdot_feeds
from wbot_collector.fetch import Heartbeat, fetch_one, next_slot, summarize, summarize_json

FEED = Feed("tripupdates", "https://example.invalid/tu", 30)
CODE = "s3cret-code"
WSDOT = Feed("wsdot_traveltimes", "https://example.invalid/tt", 120, kind="json", access_code=CODE)
NOW = datetime(2026, 10, 4, 2, 9, 46, 123000, tzinfo=UTC)


def client_returning(status: int, content: bytes) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, content=content)))


def read_log(settings) -> list[dict]:
    lines = (settings.log_dir / "2026-10-04.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def test_next_slot_aligns_to_wall_clock():
    assert next_slot(1000.0, 30) == 1020
    assert next_slot(1020.0, 30) == 1050  # strictly after now
    assert next_slot(1000.0, 300, offset_s=15) == 1215
    assert next_slot(1215.0, 300, offset_s=15) == 1515


def test_summarize():
    assert summarize(make_feed(1_791_000_000)) == {
        "header_ts": 1_791_000_000,
        "data_ts": 1_791_000_000 - 3,
        "entities": 3,
    }
    assert summarize(make_feed(1_791_000_000, n_trips=0))["data_ts"] is None
    assert summarize(b"<html>not protobuf</html>") == {"header_ts": None, "data_ts": None, "entities": None}


def test_fetch_saves_raw_bytes_and_logs(settings):
    body = make_feed(1_791_000_000)
    rec = fetch_one(client_returning(200, body), FEED, settings, NOW)

    path = settings.data_dir / rec["path"]
    assert rec["path"] == "spool/tripupdates/2026-10-04/02/tripupdates-20261004T020946Z.pb"
    assert path.read_bytes() == body
    assert rec["status"] == 200
    assert rec["header_ts"] == 1_791_000_000
    assert rec["data_ts"] == 1_791_000_000 - 3
    assert rec["entities"] == 3
    assert rec["error"] is None
    assert read_log(settings) == [rec]
    assert not list(path.parent.glob(".tmp-*"))


def test_fetch_keeps_unparseable_bytes(settings):
    rec = fetch_one(client_returning(200, b"garbage"), FEED, settings, NOW)
    assert (settings.data_dir / rec["path"]).read_bytes() == b"garbage"
    assert rec["header_ts"] is None


def test_fetch_error_status_logs_without_spooling(settings):
    rec = fetch_one(client_returning(503, b"down"), FEED, settings, NOW)
    assert rec["error"] == "HTTP 503"
    assert rec["path"] is None
    assert not settings.spool_dir.exists()
    assert read_log(settings)[0]["status"] == 503


def test_fetch_network_error_is_logged(settings):
    def boom(req):
        raise httpx.ConnectError("no route")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    rec = fetch_one(client, FEED, settings, NOW)
    assert rec["status"] is None
    assert "ConnectError" in rec["error"]


def test_same_second_does_not_overwrite(settings):
    a = fetch_one(client_returning(200, b"a"), FEED, settings, NOW)
    b = fetch_one(client_returning(200, b"b"), FEED, settings, NOW)
    assert a["path"] != b["path"]
    assert (settings.data_dir / a["path"]).read_bytes() == b"a"


def test_heartbeat_pings(settings, monkeypatch):
    pings = []
    monkeypatch.setattr("wbot_collector.health.ping", lambda s, check, **kw: pings.append(check))
    hb = Heartbeat(settings, start=0)
    frozen = {"feed": "tripupdates", "error": None, "data_ts": 5, "entities": 3}

    # An Alerts-only first cycle neither pings nor delays the first ping.
    hb.record({"feed": "alerts", "error": None, "data_ts": None, "entities": 3}, now=5)
    hb.maybe_ping(now=5)
    assert pings == []

    hb.record(frozen, now=10)
    hb.maybe_ping(now=10)
    assert pings == ["collector", "feed_stale"]

    # Data stops updating: collector keeps pinging, feed_stale stops once
    # data_ts is more than 600 s old (after t=310).
    pings.clear()
    for t in range(310, 1300, 300):
        hb.record(frozen, now=t)
        hb.maybe_ping(now=t)
    assert pings.count("collector") == 4
    assert pings.count("feed_stale") == 1

    # Overnight the feed is empty, which is normal: feed_stale pings again.
    pings.clear()
    hb.record({"feed": "tripupdates", "error": None, "data_ts": None, "entities": 0}, now=1500)
    hb.maybe_ping(now=1510)
    assert pings == ["collector", "feed_stale"]


def test_summarize_json():
    body = json.dumps(
        [
            {"TravelTimeID": 1, "TimeUpdated": "/Date(1791000000000-0700)/"},
            {"TravelTimeID": 2, "TimeUpdated": "/Date(1791000120000-0700)/"},
            {"AlertID": 3, "LastUpdatedTime": "/Date(1791000060000-0700)/"},
            {"AlertID": 4, "LastUpdatedTime": None},
        ]
    ).encode()
    assert summarize_json(body) == {"header_ts": None, "data_ts": 1_791_000_120, "entities": 4}
    assert summarize_json(b"[]") == {"header_ts": None, "data_ts": None, "entities": 0}
    assert summarize_json(b"<html>nope</html>") == {"header_ts": None, "data_ts": None, "entities": None}
    assert summarize_json(b'{"not": "a list"}') == {"header_ts": None, "data_ts": None, "entities": None}


def test_wsdot_fetch_sends_code_and_keeps_it_out_of_the_log(settings):
    seen = []

    def handler(req):
        seen.append(req.url.params.get("AccessCode"))
        return httpx.Response(200, content=b'[{"TimeUpdated": "/Date(1791000000000-0700)/"}]')

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rec = fetch_one(client, WSDOT, settings, NOW)

    assert seen == [CODE]
    assert rec["path"] == "spool/wsdot_traveltimes/2026-10-04/02/wsdot_traveltimes-20261004T020946Z.json"
    assert rec["entities"] == 1
    assert rec["data_ts"] == 1_791_000_000
    assert CODE not in (settings.log_dir / "2026-10-04.jsonl").read_text()
    assert CODE not in repr(WSDOT)


def test_wsdot_error_text_is_redacted(settings):
    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")

    client = httpx.Client(transport=httpx.MockTransport(boom))
    rec = fetch_one(client, WSDOT, settings, NOW)
    assert "ConnectError" in rec["error"]
    assert CODE not in rec["error"]
    assert CODE not in (settings.log_dir / "2026-10-04.jsonl").read_text()


def test_wsdot_feeds_only_with_access_code():
    assert wsdot_feeds("") == ()
    assert Settings(data_dir=Path("unused")).feeds == FEEDS
    on = load_settings({"WBOT_DATA_DIR": "d", "WBOT_WSDOT_ACCESS_CODE": f" {CODE} "})
    names = [f.name for f in on.feeds]
    assert names == ["tripupdates", "vehiclepositions", "alerts", "wsdot_traveltimes", "wsdot_alerts"]
    assert all(f.access_code == CODE for f in on.feeds[3:])
    # WSDOT refreshes about every 2 minutes; never poll faster than that.
    assert min(f.interval_s for f in on.feeds[3:]) >= 120


def test_heartbeat_pings_wsdot_separately(settings, monkeypatch):
    pings = []
    monkeypatch.setattr("wbot_collector.health.ping", lambda s, check, **kw: pings.append(check))
    hb = Heartbeat(settings, start=0)
    hb.record({"feed": "tripupdates", "error": None, "data_ts": 5, "entities": 3}, now=10)
    hb.maybe_ping(now=10)
    assert "wsdot" not in pings  # not configured, or not yet fetched

    pings.clear()
    hb.record({"feed": "wsdot_traveltimes", "error": None, "data_ts": 5, "entities": 3}, now=315)
    hb.record({"feed": "tripupdates", "error": None, "data_ts": 310, "entities": 3}, now=315)
    hb.maybe_ping(now=320)
    assert "wsdot" in pings

    # WSDOT erroring does not touch the bus-feed checks, and the wsdot ping stops.
    pings.clear()
    hb.record({"feed": "wsdot_traveltimes", "error": "HTTP 503", "data_ts": None, "entities": None}, now=600)
    hb.record({"feed": "tripupdates", "error": None, "data_ts": 600, "entities": 3}, now=600)
    hb.maybe_ping(now=630)
    assert pings == ["collector", "feed_stale"]
