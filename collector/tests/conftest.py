"""Shared fixtures. All feed data here is synthetic; no real feed data is committed."""

import pytest
from google.transit import gtfs_realtime_pb2

from wbot_collector.config import Settings


def make_feed(header_ts: int, n_trips: int = 3) -> bytes:
    msg = gtfs_realtime_pb2.FeedMessage()
    msg.header.gtfs_realtime_version = "2.0"
    msg.header.timestamp = header_ts
    for i in range(n_trips):
        entity = msg.entity.add()
        entity.id = f"trip-{i}"
        entity.trip_update.trip.trip_id = f"T{i}"
        entity.trip_update.timestamp = header_ts - 3 - i
        stu = entity.trip_update.stop_time_update.add()
        stu.stop_id = f"S{i}"
        stu.arrival.time = header_ts + 60 * i
    return msg.SerializeToString()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")
