"""The collector's real writers, read back by the pipeline's readers: fetch and pack snapshots,
log the fetches, save a static version, and check the pipeline finds all of it."""

import io
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
from google.transit import gtfs_realtime_pb2 as R
from wbot_collector import fetch, pack, static_gtfs
from wbot_collector.config import FEEDS
from wbot_collector.config import Settings as CollectorSettings

from wbot_pipeline import archive, events, gaps, static
from wbot_pipeline.config import Settings

HOUR = datetime(2026, 10, 7, 15, tzinfo=UTC)
TRIPUPDATES = next(f for f in FEEDS if f.name == events.FEED)


def feed_message(ts: int) -> bytes:
    msg = R.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = ts
    e = msg.entity.add()
    e.id = "T1"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.timestamp = ts - 3
    return msg.SerializeToString()


def collect(tmp_path, times: list[datetime], status: int = 200):
    """Fetch TripUpdates at each time with the collector's own code. Returns both sides' settings."""
    collector = CollectorSettings(data_dir=tmp_path / "data")
    for t in times:
        content = feed_message(int(t.timestamp()))
        client = httpx.Client(transport=httpx.MockTransport(lambda req, c=content: httpx.Response(status, content=c)))
        fetch.fetch_one(client, TRIPUPDATES, collector, t)
    return collector, Settings(data_dir=collector.data_dir, derived_dir=tmp_path / "derived")


def test_packed_snapshots_are_read_back_in_order(tmp_path):
    times = [HOUR + timedelta(seconds=30 * i) for i in range(4)]
    # The same second twice, as after a restart: both are kept.
    collector, settings = collect(tmp_path, [*times, times[-1]])
    hour = int(HOUR.timestamp())
    assert archive.spool_pending(settings.spool_dir, events.FEED, [hour])

    assert pack.pack_completed(collector, now=HOUR + timedelta(hours=2)) == 5
    assert not archive.spool_pending(settings.spool_dir, events.FEED, [hour])
    packs = archive.packs_for_hour(settings.archive_dir, events.FEED, hour)
    assert len(packs) == 1
    got = list(archive.snapshots(packs))
    assert [t for t, _, _ in got] == [int(t.timestamp()) for t in [*times, times[-1]]]
    assert len({name for _, name, _ in got}) == 5
    assert all(R.FeedMessage.FromString(data).header.timestamp == t for t, _, data in got)


def test_late_files_are_read_from_the_next_pack(tmp_path):
    collector, settings = collect(tmp_path, [HOUR])
    pack.pack_completed(collector, now=HOUR + timedelta(hours=2))
    collect(tmp_path, [HOUR + timedelta(minutes=30)])
    pack.pack_completed(collector, now=HOUR + timedelta(hours=3))
    hour = int(HOUR.timestamp())
    packs = archive.packs_for_hour(settings.archive_dir, events.FEED, hour)
    assert len(packs) == 2
    assert [t for t, _, _ in archive.snapshots(packs)] == [hour, hour + 1800]


def test_fetch_log_is_read_by_the_gap_finder(tmp_path):
    ok = [HOUR + timedelta(seconds=30 * i) for i in range(3)]
    _, settings = collect(tmp_path, ok)
    collect(tmp_path, [HOUR + timedelta(minutes=5)], status=503)
    start, end = int(HOUR.timestamp()), int(HOUR.timestamp()) + 3600
    rows = gaps.read_fetches(settings.log_dir, start, end)
    assert [r["ts"] for r in rows] == [int(t.timestamp()) for t in ok] + [start + 300]
    assert [gaps.classify(r) for r in rows] == ["good", "good", "good", "feed"]


def test_static_versions_are_listed(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("feed_info.txt", "feed_publisher_name,feed_version,feed_start_date,feed_end_date\n"
                                    "Example,2026 fall/1,20261001,20261231\n")
    content = buf.getvalue()
    collector = CollectorSettings(data_dir=tmp_path / "data")
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=content)))
    assert static_gtfs.check(collector, client, url="https://example.invalid/gtfs.zip", now=HOUR) == "saved"

    settings = Settings(data_dir=collector.data_dir, derived_dir=tmp_path / "derived")
    [v] = static.list_versions(settings.static_dir)
    assert v.stem.startswith("2026_fall_1_")
    assert v.feed_version == "2026 fall/1"
    assert v.fetched_at == int(HOUR.timestamp())
    assert len(v.sha256) == 64
    assert (v.start.isoformat(), v.end.isoformat()) == ("2026-10-01", "2026-12-31")
